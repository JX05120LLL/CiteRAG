"""Bounded LightRAG retrieval with storage-backed source verification."""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID


class QueryError(Exception):
    """Public error category only; SDK responses may contain private text."""


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    source_key: str
    content: str


class QueryRuntime(Protocol):
    async def query_data(self, kb_id: UUID, workspace: str, question: str) -> tuple[Any, int]: ...
    async def get_for_workspace(self, kb_id: UUID, workspace: str) -> Any: ...


class LightRAGQueryAdapter:
    def __init__(self, runtime: QueryRuntime):
        self.runtime = runtime

    async def retrieve(
        self, kb_id: UUID, workspace: str, question: str, sources: dict[str, str]
    ) -> list[RetrievedChunk]:
        try:
            response, reranks = await self.runtime.query_data(kb_id, workspace, question)
            if not isinstance(response, dict):
                raise QueryError("retrieval_failed")
            data = response.get("data")
            if not isinstance(data, dict):
                raise QueryError("retrieval_failed")
            if response.get("status") == "failure":
                metadata = response.get("metadata")
                if (data == {} and isinstance(metadata, dict)
                    and metadata.get("failure_reason") == "no_results"):
                    return []
                if (response.get("message") == "Query returned empty dataset."
                    and data.get("chunks") == []):
                    return []
                raise QueryError("retrieval_failed")
            if not isinstance(data.get("chunks"), list):
                raise QueryError("retrieval_failed")
            chunks = data["chunks"]
            if response.get("status") != "success" or not isinstance(data.get("references"), list):
                raise QueryError("retrieval_failed")
            if len(chunks) > 8 or sum(len(item.get("content", "")) for item in chunks
                                      if isinstance(item, dict)) > 12_000:
                raise QueryError("retrieval_failed")
            if chunks and reranks < 1:
                raise QueryError("rerank_missing")
            references: dict[str, str] = {}
            for item in data["references"]:
                if not isinstance(item, dict):
                    raise QueryError("retrieval_failed")
                key, source = item.get("reference_id"), item.get("file_path")
                if (not isinstance(key, str) or not key or key in references
                    or source not in sources):
                    raise QueryError("retrieval_failed")
                references[key] = source
            engine = await self.runtime.get_for_workspace(kb_id, workspace)
            if getattr(engine, "workspace", None) != workspace:
                raise QueryError("retrieval_failed")
            verified: list[RetrievedChunk] = []
            seen: set[str] = set()
            for item in chunks:
                if not isinstance(item, dict):
                    raise QueryError("retrieval_failed")
                key = item.get("chunk_id")
                source = item.get("file_path")
                content = item.get("content")
                ref = item.get("reference_id")
                if (not isinstance(key, str) or not key or key in seen
                    or source not in sources or references.get(ref) != source
                    or not isinstance(content, str) or not content.strip()):
                    raise QueryError("retrieval_failed")
                doc_id = sources[source]
                status = await engine.doc_status.get_by_id(doc_id)
                stored = await engine.text_chunks.get_by_id(key)
                vector = await engine.chunks_vdb.get_by_id(key)
                if (not isinstance(status, dict) or status.get("status") != "processed"
                    or status.get("file_path") != source or key not in status.get("chunks_list", [])
                    or not isinstance(stored, dict) or not isinstance(vector, dict)
                    or any(row.get("full_doc_id") != doc_id or row.get("file_path") != source
                           or row.get("content") != content for row in (stored, vector))):
                    raise QueryError("retrieval_failed")
                verified.append(RetrievedChunk(key, source, content))
                seen.add(key)
            return verified
        except QueryError:
            raise
        except Exception:
            raise QueryError("retrieval_failed") from None
