"""One supervised writer. PostgreSQL, rather than an in-memory queue, owns progress."""
import asyncio
import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select

from app.ingestion.content import engine_content_hash
from app.ingestion.parsing import parse_document
from app.models import Document, IngestionJob, KnowledgeBase, ParsedBlockRecord
from app.rag.ingestion_adapter import EngineDocument, IngestionError
from app.services.errors import ServiceError


class IngestionRunner:
    def __init__(self, database, store, adapter, assert_owned, *, enabled: bool,
                 backup_gate=None):
        self.database = database
        self.store = store
        self.adapter = adapter
        self.assert_owned = assert_owned
        self.enabled = enabled
        self.backup_gate = backup_gate
        self._wake = asyncio.Event()
        self._task = None
        self.available = True

    async def start(self):
        self.assert_owned()
        await self.recover()
        self._task = asyncio.create_task(self._run(), name="managed-ingestion")
        return self

    def wake(self):
        self._wake.set()

    async def recover(self):
        async with self.database.sessions() as session:
            jobs = list(await session.scalars(select(IngestionJob).join(KnowledgeBase).where(
                IngestionJob.status.in_(("queued", "running")),
                KnowledgeBase.owner_id.is_not(None),
            ).with_for_update()))
            for job in jobs:
                if job.engine_mutated:
                    job.status, job.error_code = "interrupted", "interrupted"
                    job.message = "引擎修改曾中断，知识库已暂停；请显式重试或从原文重建"
                    job.finished_at = datetime.now(UTC)
                    kb = await session.get(KnowledgeBase, job.kb_id)
                    kb.status = "blocked"
                elif job.status == "running":
                    job.status, job.stage = "queued", "accepted"
                    job.message = "解析中断，已保留原文并重新排队"
            maintaining = await session.scalars(select(KnowledgeBase).where(
                KnowledgeBase.status == "maintaining",
                KnowledgeBase.owner_id.is_not(None),
            ))
            for kb in maintaining:
                preflight = next((job for job in jobs if job.kb_id == kb.id
                                  and not job.engine_mutated
                                  and job.operation in {"delete", "replace"}), None)
                if preflight is None:
                    kb.status = "blocked"
            await session.commit()
            references = set(await session.scalars(select(Document.storage_key)))
        await asyncio.to_thread(self.store.cleanup_orphans, references)

    async def _run(self):
        try:
            while True:
                self.assert_owned()
                self._wake.clear()
                async with self.database.sessions() as session:
                    query = select(IngestionJob.id).join(KnowledgeBase).where(
                        IngestionJob.status == "queued", KnowledgeBase.owner_id.is_not(None),
                    )
                    if not self.enabled:
                        query = query.where(IngestionJob.stage != "parsed",
                                            IngestionJob.engine_mutated.is_(False))
                    identifier = await session.scalar(query.order_by(
                        IngestionJob.created_at, IngestionJob.id,
                    ).limit(1))
                if identifier is not None:
                    if self.backup_gate is not None:
                        await self.backup_gate.wait_and_enter()
                    try:
                        await self.process(identifier)
                    finally:
                        if self.backup_gate is not None:
                            await self.backup_gate.leave()
                    continue
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=2)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception:
            # Supervisory boundary: stop accepting writes if progress cannot be persisted.
            # No raw exception, SQL parameters, provider body or source text is logged.
            self.available = False

    async def close(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _source_check(self, document):
        path = self.store.path_for(document.storage_key)

        def digest():
            result = hashlib.sha256()
            with path.open("rb") as stream:
                size = 0
                while block := stream.read(1024 * 1024):
                    size += len(block)
                    if size > document.size:
                        raise ServiceError(422, "source_changed", "原文大小已改变，请检查备份")
                    result.update(block)
            return result.hexdigest()

        try:
            actual = await asyncio.to_thread(digest)
        except OSError:
            raise ServiceError(422, "source_missing", "受管原文缺失，请从备份恢复后重试") from None
        if actual != document.sha256:
            raise ServiceError(422, "source_changed", "原文校验不一致，请检查备份，不能继续入库")
        return path

    async def process(self, identifier: UUID):
        try:
            await self._process(identifier)
        except asyncio.CancelledError:
            await self._fail(
                identifier, "interrupted", "任务中断，请查看恢复状态", interrupted=True,
            )
            raise
        except ServiceError as error:
            await self._fail(identifier, error.code, error.message)
        except IngestionError as error:
            await self._fail(identifier, error.category, "引擎核验未通过，请重试或从原文重建")
        except Exception:
            # Persist a conservative safe failure at the external parser/SDK boundary.
            await self._fail(identifier, "index_failed", "处理未完成，未确认入库；请重试或重建")

    async def _process(self, identifier):
        self.assert_owned()
        async with self.database.sessions() as session:
            job = await session.get(IngestionJob, identifier, with_for_update=True)
            if job is None or job.status != "queued":
                return
            job.status, job.started_at = "running", datetime.now(UTC)
            ids = [UUID(value) for value in job.document_ids]
            documents = list(await session.scalars(select(Document).where(
                Document.id.in_(ids), Document.kb_id == job.kb_id,
            ).order_by(Document.created_at, Document.id)))
            if len(documents) != len(ids) or (not documents and job.operation != "rebuild"):
                raise ServiceError(409, "source_missing", "任务原文清单不完整，请检查备份")
            if not job.engine_mutated:
                job.stage, job.message = "parsing", "正在检查与解析，尚未修改知识索引"
                if job.operation == "upload":
                    for document in documents:
                        document.status = "parsing"
            await session.commit()
        if job.stage == "cleanup":
            await self._finish_cleanup(identifier)
            return
        if job.operation in {"delete", "replace"}:
            await self._process_retirement(identifier)
            return
        parsed = []
        # Recheck immutable sources even on an engine retry; never trust an old path alone.
        for document in documents:
            path = await self._source_check(document)
            if not job.engine_mutated:
                parsed.append((document.id, await parse_document(path, document.filename)))
        self.assert_owned()
        # Raw-file hashes cannot detect the SDK's HTML/whitespace normalization.
        # Reject equivalent content before maintenance, including within one batch.
        async with self.database.sessions() as session:
            comparison = await session.scalars(select(Document).where(
                Document.kb_id == job.kb_id, Document.indexed_once.is_(True),
                Document.id.not_in(ids),
                Document.status.not_in(("deleted", "deleting", "replacing")),
            ))
            known = {engine_content_hash(document.parsed_text or "") for document in comparison}
            texts = [result.text for _, result in parsed] if parsed else [
                document.parsed_text or "" for document in documents
            ]
            for source_text in texts:
                digest = engine_content_hash(source_text)
                if digest in known:
                    raise ServiceError(
                        409, "duplicate_document", "解析正文与同库资料重复，请保留一份可靠原文",
                    )
                known.add(digest)
        if parsed:
            async with self.database.sessions() as session:
                job = await session.get(IngestionJob, identifier, with_for_update=True)
                for document_id, result in parsed:
                    document = await session.get(Document, document_id)
                    document.parsed_text, document.error_code = result.text, None
                    if job.operation == "upload":
                        document.status = "parsed"
                    await session.execute(delete(ParsedBlockRecord).where(
                        ParsedBlockRecord.document_id == document_id,
                    ))
                    for ordinal, block in enumerate(result.blocks, 1):
                        session.add(ParsedBlockRecord(
                            document_id=document_id, ordinal=ordinal, text=block.text,
                            locator=block.locator, start=block.start, end=block.end,
                        ))
                job.stage = "parsed"
                job.message = (
                    "解析已保存，入库执行尚未启用" if not self.enabled else "解析完成，等待索引"
                )
                if not self.enabled:
                    job.status = "queued"
                await session.commit()
        if not self.enabled:
            return
        async with self.database.sessions() as session:
            job = await session.get(IngestionJob, identifier, with_for_update=True)
            kb = await session.get(KnowledgeBase, job.kb_id, with_for_update=True)
            if job.operation == "rebuild" and job.retired_workspace is not None and (
                job.retired_workspace != kb.active_workspace
            ):
                raise ServiceError(409, "stale_job", "旧重建任务已被新的重建替代，不能再次执行")
            if kb.status == "blocked" and not job.engine_mutated and job.operation != "rebuild":
                raise ServiceError(409, "kb_blocked", "知识库待修复，不能追加资料")
            docs = list(await session.scalars(select(Document).where(Document.id.in_(ids))))
            # Conservative preflight upper bound: one UTF-8 byte per possible token,
            # plus per-document overhead; measured engine counts are checked again below.
            estimate = sum((len((doc.parsed_text or "").encode()) + 1099) // 1100 + 1
                           for doc in docs)
            existing = await session.scalar(select(func.coalesce(func.sum(Document.chunk_count), 0))
                .where(Document.id.not_in(ids)))
            if estimate + existing > 10_000:
                raise ServiceError(409, "capacity_exceeded", "预估片段超出 10,000 上限，请缩小资料")
            kb.status, kb.revision = "maintaining", kb.revision + 1
            if job.operation == "rebuild":
                kb.hide_history_before_revision = kb.revision
                if job.target_workspace is None:
                    job.target_workspace = "kb_" + kb.id.hex + "_r_" + uuid4().hex
                    job.retired_workspace = kb.active_workspace
            elif job.target_workspace is None:
                job.target_workspace = kb.active_workspace
            elif job.target_workspace != kb.active_workspace:
                raise ServiceError(409, "rebuild_required", "任务原空间已退出使用，请查看当前资料")
            job.engine_mutated, job.stage = True, "indexing"
            job.message = "知识库维护中，正在建索引，暂不可问答"
            for document in docs:
                if not document.parsed_text:
                    raise ServiceError(409, "source_missing", "解析原文缺失，不能建索引")
                document.indexed_once, document.status = True, "indexing"
                document.error_code = None
            await session.commit()
            inputs = [EngineDocument(doc.engine_doc_id, doc.source_key, doc.parsed_text)
                      for doc in docs]
        self.assert_owned()
        if inputs:
            await self.adapter.insert(job.kb_id, job.target_workspace, inputs, job.id)
        else:
            await self.adapter.verify_empty(job.kb_id, job.target_workspace)
        async with self.database.sessions() as session:
            job = await session.get(IngestionJob, identifier, with_for_update=True)
            job.stage, job.message = "verifying", "正在核对引擎状态、片段与真实来源"
            manifest = list(await session.scalars(select(Document).where(
                Document.kb_id == job.kb_id, Document.indexed_once.is_(True),
                Document.status.not_in(("deleted", "deleting", "replacing")),
            )))
            await session.commit()
        for document in manifest:
            await self._source_check(document)
        if manifest:
            counts = await self.adapter.verify(job.kb_id, job.target_workspace, [
                EngineDocument(doc.engine_doc_id, doc.source_key, doc.parsed_text)
                for doc in manifest
            ])
        else:
            await self.adapter.verify_empty(job.kb_id, job.target_workspace)
            counts = {}
        expected = {doc.engine_doc_id for doc in manifest}
        if set(counts) != expected or any(type(value) is not int or value < 1
                                          for value in counts.values()):
            raise ServiceError(409, "verification_failed", "引擎文档与片段清单不一致")
        self.assert_owned()
        async with self.database.sessions() as session:
            job = await session.get(IngestionJob, identifier, with_for_update=True)
            kb = await session.get(KnowledgeBase, job.kb_id, with_for_update=True)
            others = await session.scalar(select(func.coalesce(func.sum(Document.chunk_count), 0))
                .where(Document.kb_id != kb.id))
            if others + sum(counts.values()) > 10_000:
                raise ServiceError(409, "capacity_exceeded", "实际索引片段超限，知识库保持暂停")
            for document in manifest:
                row = await session.get(Document, document.id)
                row.status, row.error_code = "ready", None
                row.chunk_count = counts[row.engine_doc_id]
            if job.operation == "rebuild":
                kb.active_workspace = job.target_workspace
                job.cleanup_pending = True
                for document in await session.scalars(select(Document).where(
                    Document.kb_id == kb.id,
                    Document.status.in_(("deleting", "replacing")),
                )):
                    document.status = "deleted"
                job.stage, job.message = "cleanup", "新空间已核验，正在清理旧空间"
            else:
                kb.status = "ready"
                job.status, job.stage, job.error_code = "succeeded", "complete", None
                job.message = "入库与来源核验通过"
                job.finished_at = datetime.now(UTC)
            await session.commit()
        if job.operation == "rebuild":
            await self._finish_cleanup(identifier)

    async def _process_retirement(self, identifier: UUID):
        self.assert_owned()
        async with self.database.sessions() as session:
            job = await session.get(IngestionJob, identifier)
            old = await session.get(Document, UUID(job.document_ids[0]))
            replacement = (await session.get(Document, UUID(job.document_ids[1]))
                           if job.operation == "replace" else None)
            retained = list(await session.scalars(select(Document).where(
                Document.kb_id == job.kb_id,
                Document.id.not_in([UUID(value) for value in job.document_ids]),
                Document.indexed_once.is_(True),
                Document.status.not_in(("deleted", "deleting", "replacing")),
            )))
        # Complete source preflight before touching the engine. The old source is
        # intentionally not required to remove it from a replacement workspace.
        for document in retained:
            await self._source_check(document)
            if not document.parsed_text:
                raise ServiceError(409, "source_missing", "保留资料缺少解析正文")
        parsed = None
        if replacement is not None:
            path = await self._source_check(replacement)
            if not job.engine_mutated:
                parsed = await parse_document(path, replacement.filename)
                digest = engine_content_hash(parsed.text)
                if digest in {engine_content_hash(doc.parsed_text or "") for doc in retained}:
                    raise ServiceError(409, "duplicate_document", "替换正文与库内其他资料重复")
        if parsed is not None:
            async with self.database.sessions() as session:
                replacement = await session.get(Document, replacement.id, with_for_update=True)
                replacement.parsed_text, replacement.status = parsed.text, "parsed"
                await session.execute(delete(ParsedBlockRecord).where(
                    ParsedBlockRecord.document_id == replacement.id,
                ))
                for ordinal, block in enumerate(parsed.blocks, 1):
                    session.add(ParsedBlockRecord(document_id=replacement.id,
                        ordinal=ordinal, text=block.text, locator=block.locator,
                        start=block.start, end=block.end))
                await session.commit()
        if not self.enabled:
            async with self.database.sessions() as session:
                job = await session.get(IngestionJob, identifier, with_for_update=True)
                job.status, job.stage = "queued", "parsed"
                job.message = "原文检查完成，等待明确启用索引维护"
                await session.commit()
            return
        async with self.database.sessions() as session:
            job = await session.get(IngestionJob, identifier, with_for_update=True)
            kb = await session.get(KnowledgeBase, job.kb_id, with_for_update=True)
            if job.retired_workspace is not None and job.retired_workspace != kb.active_workspace:
                raise ServiceError(409, "stale_job", "任务所属旧空间已退出使用")
            if not job.engine_mutated:
                if kb.status != "maintaining":
                    raise ServiceError(409, "kb_not_ready", "知识库尚未就绪")
                kb.revision += 1
                kb.hide_history_before_revision = kb.revision
                job.target_workspace = "kb_" + kb.id.hex + "_r_" + uuid4().hex
                job.retired_workspace = kb.active_workspace
                job.engine_mutated = True
            else:
                kb.status = "maintaining"
            job.stage, job.message = "indexing", "维护中，正在构建不含旧资料的新空间"
            if replacement is not None:
                replacement = await session.get(Document, replacement.id,
                                                with_for_update=True)
                replacement.status, replacement.indexed_once = "indexing", True
            await session.commit()
            target, retired = job.target_workspace, job.retired_workspace
        manifest = [EngineDocument(doc.engine_doc_id, doc.source_key, doc.parsed_text)
                    for doc in retained]
        if replacement is not None:
            manifest.append(EngineDocument(replacement.engine_doc_id,
                                           replacement.source_key, replacement.parsed_text))
        if manifest:
            await self.adapter.insert(job.kb_id, target, manifest, job.id)
            counts = await self.adapter.verify(job.kb_id, target, manifest)
            if (set(counts) != {doc.id for doc in manifest}
                or any(type(value) is not int or value < 1 for value in counts.values())):
                raise ServiceError(409, "verification_failed", "新空间资料清单未核验通过")
        else:
            await self.adapter.verify_empty(job.kb_id, target)
            counts = {}
        for document in retained:
            await self._source_check(document)
        if replacement is not None:
            await self._source_check(replacement)
        async with self.database.sessions() as session:
            job = await session.get(IngestionJob, identifier, with_for_update=True)
            kb = await session.get(KnowledgeBase, job.kb_id, with_for_update=True)
            if kb.active_workspace != retired or kb.status != "maintaining":
                raise ServiceError(409, "kb_changed", "库状态已变化，不能切换空间")
            others = await session.scalar(select(func.coalesce(func.sum(Document.chunk_count), 0))
                .where(Document.kb_id != kb.id, Document.status == "ready"))
            if others + sum(counts.values()) > 10_000:
                raise ServiceError(409, "capacity_exceeded", "新空间片段超出本机容量上限")
            old = await session.get(Document, UUID(job.document_ids[0]), with_for_update=True)
            old.status = "deleted"
            for document in retained:
                row = await session.get(Document, document.id)
                row.status, row.chunk_count = "ready", counts[row.engine_doc_id]
            if replacement is not None:
                row = await session.get(Document, replacement.id)
                row.status, row.chunk_count = "ready", counts[row.engine_doc_id]
            kb.active_workspace = target
            job.stage, job.cleanup_pending = "cleanup", True
            job.message = "新空间已核验，正在物理清理旧空间和旧原文"
            await session.commit()
        await self._finish_cleanup(identifier)

    async def _finish_cleanup(self, identifier: UUID):
        self.assert_owned()
        async with self.database.sessions() as session:
            job = await session.get(IngestionJob, identifier, with_for_update=True)
            kb = await session.get(KnowledgeBase, job.kb_id, with_for_update=True)
            if (not job.cleanup_pending or job.retired_workspace is None
                or job.retired_workspace == kb.active_workspace):
                raise ServiceError(409, "cleanup_unavailable", "旧空间不能安全清理")
            engine_ids = list(await session.scalars(select(Document.engine_doc_id).where(
                Document.kb_id == kb.id, Document.indexed_once.is_(True),
            )))
            retired = job.retired_workspace
            deleted = list(await session.scalars(select(Document).where(
                Document.kb_id == kb.id, Document.status == "deleted",
            )))
        await self.adapter.clear_workspace(job.kb_id, retired, engine_ids)
        for document in deleted:
            await asyncio.to_thread(self.store.discard, document.storage_key)
        async with self.database.sessions() as session:
            job = await session.get(IngestionJob, identifier, with_for_update=True)
            kb = await session.get(KnowledgeBase, job.kb_id, with_for_update=True)
            if kb.active_workspace == job.retired_workspace:
                raise ServiceError(409, "kb_changed", "空间已变化，不能完成清理")
            for document in deleted:
                row = await session.get(Document, document.id, with_for_update=True)
                if row.status == "deleted":
                    await session.execute(delete(ParsedBlockRecord).where(
                        ParsedBlockRecord.document_id == row.id,
                    ))
                    row.parsed_text, row.chunk_count, row.indexed_once = None, 0, False
                    row.doc_code = row.model_code = row.edition = None
            remaining = await session.scalar(select(Document.id).where(
                Document.kb_id == kb.id, Document.status == "ready",
            ).limit(1))
            kb.status = "ready" if remaining else "empty"
            job.status, job.stage, job.cleanup_pending = "succeeded", "complete", False
            job.error_code, job.message = None, "新空间已核验，旧空间与旧原文清理完成"
            job.finished_at = datetime.now(UTC)
            await session.commit()

    async def _fail(self, identifier, code, message, *, interrupted=False):
        async with self.database.sessions() as session:
            job = await session.get(IngestionJob, identifier, with_for_update=True)
            if job is None or job.status == "succeeded":
                return
            if interrupted and not job.engine_mutated:
                job.status, job.stage, job.message = "queued", "accepted", "解析中断，原文已保留"
            else:
                job.status = "interrupted" if interrupted else "failed"
                job.error_code, job.message = code, message
                job.finished_at = datetime.now(UTC)
            if job.engine_mutated:
                kb = await session.get(KnowledgeBase, job.kb_id, with_for_update=True)
                relevant = (job.target_workspace if job.operation == "upload"
                            else job.retired_workspace) == kb.active_workspace
                if relevant or job.cleanup_pending:
                    kb.status = "blocked"
            if job.operation == "upload":
                for document_id in job.document_ids:
                    document = await session.get(Document, UUID(document_id))
                    if document:
                        document.status, document.error_code = "failed", code
            elif job.operation in {"delete", "replace"}:
                old = await session.get(Document, UUID(job.document_ids[0]))
                if old is not None and not job.engine_mutated:
                    old.status = "ready"
                    kb = await session.get(KnowledgeBase, job.kb_id, with_for_update=True)
                    kb.status = "ready"
                if job.operation == "replace":
                    replacement = await session.get(Document, UUID(job.document_ids[1]))
                    if replacement is not None and not job.engine_mutated:
                        replacement.status, replacement.error_code = "failed", code
            await session.commit()
