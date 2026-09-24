"""Fail-closed ingestion and evidence checks for the pinned LightRAG SDK.

The durable business job owns the per-library mutation lock and records entry into
engine mutation before calling this adapter. A successful enqueue is never proof
of completed ingestion. ``verify`` requires the entire desired workspace manifest.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True, repr=False)
class EngineDocument:
    id: str
    source_key: str
    text: str


class IngestionError(RuntimeError):
    """Public category only; never retain SDK text, source contents or identifiers."""

    def __init__(self, category: str = "rebuild_required") -> None:
        self.category = category
        super().__init__(category)


class IngestionRuntime(Protocol):
    async def get_for_workspace(self, kb_id: UUID, workspace: str) -> Any: ...
    async def clear_workspace(self, kb_id: UUID, workspace: str,
                              document_ids: list[str]) -> None: ...
    async def verify_empty_workspace(self, kb_id: UUID, workspace: str) -> None: ...


def _manifest(documents: Sequence[EngineDocument]) -> dict[str, tuple[EngineDocument, str]]:
    from lightrag.utils import sanitize_text_for_encoding

    result: dict[str, tuple[EngineDocument, str]] = {}
    sources: set[str] = set()
    for document in documents:
        if (
            not isinstance(document, EngineDocument)
            or not isinstance(document.id, str)
            or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", document.id)
            or not isinstance(document.source_key, str)
            or not re.fullmatch(r"[a-zA-Z0-9_-][a-zA-Z0-9_.-]{0,180}", document.source_key)
            or ".." in document.source_key
            or document.id in result
            or document.source_key in sources
            or not isinstance(document.text, str)
        ):
            raise IngestionError("invalid_document")
        # The fixed SDK sanitizes RAW input before persisting it. Keep the business
        # parsed text untouched; this normalized view is only engine evidence.
        normalized = sanitize_text_for_encoding(document.text)
        if not normalized:
            raise IngestionError("invalid_document")
        result[document.id] = (document, normalized)
        sources.add(document.source_key)
    if not result:
        raise IngestionError("invalid_document")
    return result


def _assert_workspace(engine: Any, workspace: str) -> None:
    if getattr(engine, "workspace", None) != workspace:
        raise IngestionError("rebuild_required")
    for name in ("doc_status", "full_docs", "text_chunks", "chunks_vdb"):
        if getattr(getattr(engine, name, None), "workspace", None) != workspace:
            raise IngestionError("rebuild_required")


async def _assert_document(
    engine: Any, document: EngineDocument, normalized: str, status: Any
) -> None:
    stored = await engine.full_docs.get_by_id(document.id)
    if (
        not isinstance(status, dict)
        or status.get("file_path") != document.source_key
        or status.get("content_length") != len(normalized)
        or not isinstance(stored, dict)
        or stored.get("file_path") != document.source_key
        or stored.get("content") != normalized
    ):
        raise IngestionError("rebuild_required")


class LightRAGIngestionAdapter:
    def __init__(self, runtime: IngestionRuntime) -> None:
        self._runtime = runtime

    async def clear_workspace(self, kb_id: UUID, workspace: str,
                              document_ids: list[str]) -> None:
        try:
            await self._runtime.clear_workspace(kb_id, workspace, document_ids)
        except Exception:
            raise IngestionError("cleanup_failed") from None

    async def verify_empty(self, kb_id: UUID, workspace: str) -> None:
        try:
            await self._runtime.verify_empty_workspace(kb_id, workspace)
        except Exception:
            raise IngestionError("verification_failed") from None

    async def insert(
        self,
        kb_id: UUID,
        workspace: str,
        documents: Sequence[EngineDocument],
        job_id: UUID,
    ) -> None:
        try:
            manifest = _manifest(documents)
            if not isinstance(job_id, UUID):
                raise IngestionError("invalid_document")
            engine = await self._runtime.get_for_workspace(kb_id, workspace)
            _assert_workspace(engine, workspace)
            await self._insert(engine, manifest, job_id)
        except IngestionError:
            raise
        except Exception:
            # SDK/provider exceptions may include request bodies or workspace IDs.
            # Cancellation remains a BaseException and reaches the durable worker.
            raise IngestionError("engine_unavailable") from None

    @staticmethod
    async def _insert(
        engine: Any, manifest: dict[str, tuple[EngineDocument, str]], job_id: UUID
    ) -> None:
        from lightrag.base import DocStatus

        # process_enqueue drains workspace-wide work. Never let an unrelated or
        # failed document enter that mutation implicitly during an ordinary retry.
        unfinished = await engine.doc_status.get_docs_by_statuses(
            [status for status in DocStatus if status != DocStatus.PROCESSED], strict=True
        )
        if any(
            identifier not in manifest
            or getattr(row, "status", None) not in {DocStatus.PENDING, DocStatus.PROCESSING}
            for identifier, row in unfinished.items()
        ):
            raise IngestionError("rebuild_required")

        new_documents: list[EngineDocument] = []
        must_process = False
        for document, normalized in manifest.values():
            status = await engine.doc_status.get_by_id(document.id)
            if status is None:
                # An interrupted enqueue may have persisted only the body. Do not
                # overwrite an orphan and pretend a fresh identity made it safe.
                if await engine.full_docs.get_by_id(document.id) is not None:
                    raise IngestionError("rebuild_required")
                new_documents.append(document)
                continue
            await _assert_document(engine, document, normalized, status)
            state = status.get("status")
            if state not in {DocStatus.PENDING, DocStatus.PROCESSING, DocStatus.PROCESSED}:
                raise IngestionError("rebuild_required")
            must_process |= state != DocStatus.PROCESSED

        if new_documents:
            await engine.apipeline_enqueue_documents(
                [document.text for document in new_documents],
                ids=[document.id for document in new_documents],
                file_paths=[document.source_key for document in new_documents],
                track_id=f"job_{job_id.hex}",
            )
            must_process = True
        if must_process:
            await engine.apipeline_process_enqueue_documents()

        # The upstream processor can record failure without raising an exception.
        for document, normalized in manifest.values():
            status = await engine.doc_status.get_by_id(document.id)
            await _assert_document(engine, document, normalized, status)
            if status.get("status") == DocStatus.FAILED:
                raise IngestionError("rebuild_required")
            if status.get("status") != DocStatus.PROCESSED:
                raise IngestionError("engine_incomplete")

    async def verify(
        self, kb_id: UUID, workspace: str, documents: Sequence[EngineDocument]
    ) -> dict[str, int]:
        """Return per-document chunk counts only after storage and retrieval agree.

        Uses one bounded naive retrieval probe, with reranking and answer generation
        disabled. In production this still incurs one embedding provider request.
        """
        try:
            manifest = _manifest(documents)
            engine = await self._runtime.get_for_workspace(kb_id, workspace)
            _assert_workspace(engine, workspace)
            return await self._verify(engine, manifest)
        except IngestionError:
            raise
        except Exception:
            raise IngestionError("verification_failed") from None

    @staticmethod
    async def _verify(
        engine: Any, manifest: dict[str, tuple[EngineDocument, str]]
    ) -> dict[str, int]:
        from lightrag.base import QueryParam

        counts = await engine.doc_status.get_all_status_counts()
        if (
            not isinstance(counts, dict)
            or counts.get("all") != len(manifest)
            or counts.get("processed") != len(manifest)
        ):
            raise IngestionError("rebuild_required")

        chunk_counts: dict[str, int] = {}
        verified_chunks: dict[str, tuple[str, str]] = {}
        for document, normalized in manifest.values():
            status = await engine.doc_status.get_by_id(document.id)
            await _assert_document(engine, document, normalized, status)
            chunk_ids = status.get("chunks_list")
            if (
                status.get("status") != "processed"
                or not isinstance(chunk_ids, list)
                or not chunk_ids
                or any(not isinstance(key, str) or not key for key in chunk_ids)
                or len(set(chunk_ids)) != len(chunk_ids)
                or status.get("chunks_count") != len(chunk_ids)
            ):
                raise IngestionError("rebuild_required")
            # Batch reads remain bounded even for a larger valid document.
            for start in range(0, len(chunk_ids), 128):
                batch = chunk_ids[start : start + 128]
                chunks = await engine.text_chunks.get_by_ids(batch)
                vectors = await engine.chunks_vdb.get_by_ids(batch)
                if len(chunks) != len(batch) or len(vectors) != len(batch):
                    raise IngestionError("rebuild_required")
                for key, chunk, vector in zip(batch, chunks, vectors, strict=True):
                    if not isinstance(chunk, dict) or not isinstance(vector, dict):
                        raise IngestionError("rebuild_required")
                    content = chunk.get("content")
                    if (
                        key in verified_chunks
                        or not isinstance(content, str)
                        or not content.strip()
                        or content not in normalized
                        or chunk.get("full_doc_id") != document.id
                        or chunk.get("file_path") != document.source_key
                        or vector.get("full_doc_id") != document.id
                        or vector.get("file_path") != document.source_key
                        or vector.get("content") != content
                    ):
                        raise IngestionError("rebuild_required")
                    verified_chunks[key] = (document.source_key, content)
            chunk_counts[document.id] = len(chunk_ids)

        sample = next(iter(verified_chunks.values()))[1][:256]
        response = await engine.aquery_data(
            sample,
            QueryParam(
                mode="naive", top_k=3, chunk_top_k=3,
                enable_rerank=False, include_references=True,
            ),
        )
        _assert_retrieval(response, verified_chunks)
        return chunk_counts


def _assert_retrieval(response: Any, chunks: dict[str, tuple[str, str]]) -> None:
    if not isinstance(response, dict) or response.get("status") != "success":
        raise IngestionError("verification_failed")
    data = response.get("data")
    if not isinstance(data, dict):
        raise IngestionError("verification_failed")
    results, references = data.get("chunks"), data.get("references")
    if not isinstance(results, list) or not results or not isinstance(references, list):
        raise IngestionError("verification_failed")
    reference_map: dict[str, str] = {}
    valid_sources = {source for source, _ in chunks.values()}
    for reference in references:
        if not isinstance(reference, dict):
            raise IngestionError("verification_failed")
        identifier, source = reference.get("reference_id"), reference.get("file_path")
        if (
            not isinstance(identifier, str) or not identifier or identifier in reference_map
            or not isinstance(source, str) or source not in valid_sources
        ):
            raise IngestionError("verification_failed")
        reference_map[identifier] = source
    for result in results:
        if not isinstance(result, dict):
            raise IngestionError("verification_failed")
        identifier, reference = result.get("chunk_id"), result.get("reference_id")
        if (
            not isinstance(identifier, str)
            or chunks.get(identifier) != (result.get("file_path"), result.get("content"))
            or not isinstance(reference, str)
            or reference_map.get(reference) != result.get("file_path")
        ):
            raise IngestionError("verification_failed")
