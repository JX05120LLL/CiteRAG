"""Business ownership, durable acceptance and explicit repair requests."""
import hashlib
import json
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, IngestionJob, KnowledgeBase, LocalProfile, ParsedBlockRecord
from app.services.errors import ServiceError

ACTIVE = ("queued", "running")


async def owned_kb(session: AsyncSession, owner: UUID, kb_id: UUID, *, lock=False):
    query = select(KnowledgeBase).where(
        KnowledgeBase.id == kb_id, KnowledgeBase.owner_id == owner,
    )
    kb = await session.scalar(query.with_for_update() if lock else query)
    if kb is None:
        raise ServiceError(404, "kb_not_found", "知识库不存在或不可访问")
    return kb


async def owned_job(session: AsyncSession, owner: UUID, job_id: UUID):
    job = await session.scalar(select(IngestionJob).join(KnowledgeBase).where(
        IngestionJob.id == job_id, KnowledgeBase.owner_id == owner,
    ))
    if job is None:
        raise ServiceError(404, "job_not_found", "任务不存在或不可访问")
    return job


async def no_active_job(session: AsyncSession, kb_id: UUID):
    if await session.scalar(select(IngestionJob.id).where(
        IngestionJob.kb_id == kb_id, IngestionJob.status.in_(ACTIVE),
    ).limit(1)):
        raise ServiceError(409, "kb_busy", "该库已有待处理任务，请先查看任务进度")


class DocumentService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def accept(self, owner: UUID, kb_id: UUID, request_id: UUID, sources):
        # Lock installation first, matching the knowledge creation lock order.
        await self.session.scalar(select(LocalProfile).where(
            LocalProfile.id == owner,
        ).with_for_update())
        kb = await owned_kb(self.session, owner, kb_id, lock=True)
        fingerprint = hashlib.sha256(json.dumps(
            [(source.filename, source.size, source.sha256) for source in sources],
            ensure_ascii=True, separators=(",", ":"),
        ).encode()).hexdigest()
        prior = await self.session.scalar(select(IngestionJob).where(
            IngestionJob.kb_id == kb_id, IngestionJob.client_request_id == request_id,
        ))
        if prior is not None:
            if prior.operation != "upload" or prior.fingerprint != fingerprint:
                raise ServiceError(409, "idempotency_conflict", "同一上传请求不能更换文件")
            await self.session.commit()
            return prior, False
        hashes = [source.sha256 for source in sources]
        duplicate = await self.session.scalar(select(Document.id).where(
            Document.kb_id == kb_id, Document.sha256.in_(hashes),
            Document.status != "deleted",
        ).limit(1))
        if len(set(hashes)) != len(hashes) or duplicate:
            raise ServiceError(409, "duplicate_document", "该库已有相同内容，请查看原资料与任务")
        if kb.status == "blocked":
            raise ServiceError(409, "kb_blocked", "知识库待修复，请重试原任务或重建")
        if kb.status == "maintaining":
            raise ServiceError(409, "kb_busy", "知识库维护中，暂不能接收新资料")
        await no_active_job(self.session, kb_id)
        count = await self.session.scalar(select(func.count()).select_from(Document).join(
            KnowledgeBase,
        ).where(KnowledgeBase.owner_id == owner, Document.status != "deleted"))
        if count + len(sources) > 100:
            raise ServiceError(409, "capacity_exceeded", "本地安装最多保存 100 份资料")
        documents = []
        for source in sources:
            identifier = uuid4()
            document = Document(
                id=identifier, kb_id=kb_id, filename=source.filename,
                storage_key=source.storage_key, sha256=source.sha256, size=source.size,
                status="pending", source_key="source_" + identifier.hex,
                engine_doc_id="doc_" + identifier.hex,
            )
            self.session.add(document)
            documents.append(str(identifier))
        job = IngestionJob(
            kb_id=kb_id, client_request_id=request_id, operation="upload",
            fingerprint=fingerprint, document_ids=documents, status="queued", stage="accepted",
            message="原文和任务已受理，尚未解析或入库",
        )
        self.session.add(job)
        await self.session.commit()
        return job, True

    async def retry(self, owner: UUID, job_id: UUID):
        job = await owned_job(self.session, owner, job_id)
        kb = await owned_kb(self.session, owner, job.kb_id, lock=True)
        await self.session.refresh(job)
        if job.status in ACTIVE:
            await self.session.commit()
            return job
        if job.status not in {"failed", "interrupted"}:
            raise ServiceError(409, "job_not_retryable", "任务已完成，无需重试")
        if job.error_code == "stale_job":
            raise ServiceError(409, "stale_job", "此任务已失效，不能再次重试")
        cleaning = (job.cleanup_pending and job.retired_workspace is not None
                    and job.retired_workspace != kb.active_workspace)
        if job.error_code == "rebuild_required" and not cleaning:
            raise ServiceError(409, "rebuild_required", "引擎状态不能安全重试，请从原文重建")
        await no_active_job(self.session, kb.id)
        if not cleaning and job.operation == "rebuild" and job.retired_workspace is not None and (
            job.retired_workspace != kb.active_workspace
        ):
            raise ServiceError(409, "stale_job", "旧重建任务已被新的重建替代，不能再次重试")
        # A failure from a previous revision cannot modify a rebuilt library.
        if job.engine_mutated and job.operation == "upload" and (
            job.target_workspace != kb.active_workspace
        ):
            raise ServiceError(409, "rebuild_required", "任务属于已退出使用的空间，请查看当前资料")
        job.status, job.error_code, job.message = "queued", None, "重试已排队，尚未确认入库"
        job.finished_at = None
        if cleaning:
            job.stage = "cleanup"
            kb.status = "maintaining"
        elif job.operation in {"delete", "replace"}:
            old = await self.session.get(Document, UUID(job.document_ids[0]),
                                         with_for_update=True)
            if job.operation == "replace" or old.indexed_once:
                kb.status = "maintaining"
            if old.status in {"ready", "failed"}:
                old.status = "deleting" if job.operation == "delete" else "replacing"
        # Retain engine_mutated: a failed retry can never release a blocked library.
        if not job.engine_mutated:
            job.stage = "accepted"
        await self.session.commit()
        return job

    async def rebuild(self, owner: UUID, kb_id: UUID, request_id: UUID):
        kb = await owned_kb(self.session, owner, kb_id, lock=True)
        prior = await self.session.scalar(select(IngestionJob).where(
            IngestionJob.kb_id == kb_id, IngestionJob.client_request_id == request_id,
        ))
        if prior is not None:
            if prior.operation != "rebuild":
                raise ServiceError(409, "idempotency_conflict", "请求键已用于其他操作")
            await self.session.commit()
            return prior
        await no_active_job(self.session, kb_id)
        documents = list(await self.session.scalars(select(Document).where(
            Document.kb_id == kb_id, Document.indexed_once.is_(True),
            Document.status.not_in(("deleted", "deleting", "replacing")),
        ).order_by(Document.created_at, Document.id)))
        pending_retirement = await self.session.scalar(select(Document.id).where(
            Document.kb_id == kb_id, Document.indexed_once.is_(True),
            Document.status.in_(("deleting", "replacing")),
        ).limit(1))
        if not documents and not (kb.status == "blocked" and pending_retirement):
            raise ServiceError(409, "nothing_to_rebuild", "没有曾进入索引的受管原文，请先上传资料")
        job = IngestionJob(
            kb_id=kb_id, client_request_id=request_id, operation="rebuild",
            fingerprint="rebuild", document_ids=[str(document.id) for document in documents],
            status="queued", stage="accepted", message="重建已受理，将核对原文后进入维护",
        )
        self.session.add(job)
        await self.session.commit()
        return job

    async def delete(self, owner: UUID, document_id: UUID, request_id: UUID,
                     *, allow_indexed: bool = True):
        await self.session.scalar(select(LocalProfile).where(
            LocalProfile.id == owner,
        ).with_for_update())
        document = await self.session.get(Document, document_id)
        if document is None:
            raise ServiceError(404, "document_not_found", "资料不存在或不可访问")
        kb = await owned_kb(self.session, owner, document.kb_id, lock=True)
        prior = await self.session.scalar(select(IngestionJob).where(
            IngestionJob.kb_id == kb.id, IngestionJob.client_request_id == request_id,
        ))
        fingerprint = hashlib.sha256(("delete:" + document_id.hex).encode()).hexdigest()
        if prior is not None:
            if prior.operation != "delete" or prior.fingerprint != fingerprint:
                raise ServiceError(409, "idempotency_conflict", "请求键已用于其他维护操作")
            await self.session.commit()
            return prior
        failed = document.status == "failed" and kb.status in {"empty", "ready", "blocked"}
        if not failed and (kb.status != "ready" or document.status != "ready"):
            raise ServiceError(409, "kb_not_ready", "资料未就绪，暂不能删除")
        await no_active_job(self.session, kb.id)
        if document.indexed_once and not allow_indexed:
            raise ServiceError(503, "ingestion_disabled", "索引维护尚未启用，不能受理删除")
        if document.indexed_once:
            kb.status = "maintaining"
        document.status = "deleting"
        job = IngestionJob(kb_id=kb.id, client_request_id=request_id,
            operation="delete", fingerprint=fingerprint, document_ids=[str(document_id)],
            status="queued", stage="accepted", message="删除已受理，原文访问已暂停")
        self.session.add(job)
        await self.session.commit()
        return job

    async def replace(self, owner: UUID, document_id: UUID, request_id: UUID, source):
        await self.session.scalar(select(LocalProfile).where(
            LocalProfile.id == owner,
        ).with_for_update())
        old = await self.session.get(Document, document_id)
        if old is None:
            raise ServiceError(404, "document_not_found", "资料不存在或不可访问")
        kb = await owned_kb(self.session, owner, old.kb_id, lock=True)
        fingerprint = hashlib.sha256(json.dumps(
            [str(document_id), source.filename, source.size, source.sha256],
            ensure_ascii=True, separators=(",", ":"),
        ).encode()).hexdigest()
        prior = await self.session.scalar(select(IngestionJob).where(
            IngestionJob.kb_id == kb.id, IngestionJob.client_request_id == request_id,
        ))
        if prior is not None:
            if prior.operation != "replace" or prior.fingerprint != fingerprint:
                raise ServiceError(409, "idempotency_conflict", "同一替换请求不能更换原文")
            await self.session.commit()
            return prior, False
        if kb.status != "ready" or old.status != "ready":
            raise ServiceError(409, "kb_not_ready", "资料未就绪，暂不能替换")
        await no_active_job(self.session, kb.id)
        if await self.session.scalar(select(Document.id).where(
            Document.kb_id == kb.id, Document.sha256 == source.sha256,
            Document.status != "deleted",
        ).limit(1)):
            raise ServiceError(409, "duplicate_document", "该库已有相同内容")
        identifier = uuid4()
        replacement = Document(id=identifier, kb_id=kb.id, filename=source.filename,
            storage_key=source.storage_key, sha256=source.sha256, size=source.size,
            status="pending", source_key="source_" + identifier.hex,
            engine_doc_id="doc_" + identifier.hex)
        self.session.add(replacement)
        kb.status, old.status = "maintaining", "replacing"
        job = IngestionJob(kb_id=kb.id, client_request_id=request_id,
            operation="replace", fingerprint=fingerprint,
            document_ids=[str(document_id), str(identifier)], status="queued",
            stage="accepted", message="替换已受理，旧原文访问已暂停")
        self.session.add(job)
        await self.session.commit()
        return job, True

    async def cleanup(self, owner: UUID, job_id: UUID):
        job = await owned_job(self.session, owner, job_id)
        kb = await owned_kb(self.session, owner, job.kb_id, lock=True)
        await self.session.refresh(job)
        if not job.cleanup_pending:
            raise ServiceError(409, "nothing_to_cleanup", "该任务没有待清理旧空间")
        if job.status in ACTIVE and job.stage == "cleanup":
            await self.session.commit()
            return job
        if (job.status != "succeeded" or job.retired_workspace is None
            or job.retired_workspace == kb.active_workspace or kb.status != "ready"):
            raise ServiceError(409, "cleanup_unavailable", "旧空间尚不能安全清理")
        await no_active_job(self.session, kb.id)
        kb.status = "maintaining"
        job.status, job.stage = "queued", "cleanup"
        job.message = "正在物理清理已退出使用的旧空间"
        job.finished_at = None
        await self.session.commit()
        return job

    async def readable(self, owner: UUID, document_id: UUID):
        pair = (await self.session.execute(select(Document, KnowledgeBase).join(
            KnowledgeBase,
        ).where(Document.id == document_id, KnowledgeBase.owner_id == owner))).first()
        if pair is None:
            raise ServiceError(404, "document_not_found", "资料不存在或不可访问")
        document, kb = pair
        if kb.status in {"maintaining", "blocked"}:
            raise ServiceError(409, "kb_" + kb.status, "知识库维护或待修复，暂不可读取原文")
        if document.status in {"deleting", "replacing", "deleted"}:
            raise ServiceError(404, "document_not_found", "资料不存在或不可访问")
        return document

    async def blocks(self, owner: UUID, document_id: UUID):
        await self.readable(owner, document_id)
        blocks = list(await self.session.scalars(select(ParsedBlockRecord).where(
            ParsedBlockRecord.document_id == document_id,
        ).order_by(ParsedBlockRecord.ordinal)))
        # Materialize before expire_all, then recheck after the asynchronous read.
        from types import SimpleNamespace
        snapshot = [SimpleNamespace(
            ordinal=block.ordinal, text=block.text, locator=block.locator,
            start=block.start, end=block.end,
        ) for block in blocks]
        self.session.expire_all()
        await self.readable(owner, document_id)
        return snapshot
