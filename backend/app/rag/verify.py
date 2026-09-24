"""Opt-in, bounded two-workspace verification for the pinned LightRAG engine."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

import asyncpg

from app.credentials import DashScopeConfig
from app.providers.types import ProviderUsage
from app.rag.database import RagDatabaseSettings
from app.rag.engine import workspace_for
from app.rag.sdk import LIGHTRAG_COMMIT, TOKENIZER_SHA256
from app.validation import ValidationUsage, _restrict_path, model_fingerprint, rag_fingerprint

_TEXT_A = "A 产品质保期为 12 个月。编号 A-WARRANTY-12。"
_TEXT_B = "B 产品质保期为 24 个月。编号 B-WARRANTY-24。"
_PROCESSING_DEADLINE_SECONDS = 30
_STORAGE_NAMES = (
    "full_docs",
    "text_chunks",
    "full_entities",
    "full_relations",
    "entity_chunks",
    "relation_chunks",
    "entities_vdb",
    "relationships_vdb",
    "chunks_vdb",
    "chunk_entity_relation_graph",
    "llm_response_cache",
    "doc_status",
)


class RagVerificationError(RuntimeError):
    """Only a stable stage/category is exposed; SDK/provider bodies are never attached."""

    def __init__(self, stage: str, category: str):
        self.stage = stage
        self.category = category
        super().__init__(f"RAG verification failed: {stage}/{category}")


@dataclass
class RagProviderEvidence:
    """Count successful adapter calls without retaining prompts or responses."""

    request_ids: list[str]
    usage: dict[str, ValidationUsage]
    rerank_count: int

    def __init__(self) -> None:
        self.request_ids = []
        self.usage = {}
        self.rerank_count = 0

    def record(self, model: str, request_id: str | None, usage: ProviderUsage) -> None:
        if (
            not isinstance(request_id, str)
            or not re.fullmatch(r"[A-Za-z0-9._-]{8,200}", request_id)
            or not any(character.isdigit() for character in request_id)
            or request_id in self.request_ids
        ):
            raise RagVerificationError("provider", "request_id_invalid")
        self.request_ids.append(request_id)
        prior = self.usage.get(model)

        def add(old: int | None, new: int | None) -> int | None:
            if old is None and new is None:
                return None
            return (old or 0) + (new or 0)

        self.usage[model] = ValidationUsage(
            prompt_tokens=add(prior.prompt_tokens if prior else None, usage.prompt_tokens),
            completion_tokens=add(
                prior.completion_tokens if prior else None, usage.completion_tokens
            ),
            total_tokens=add(prior.total_tokens if prior else None, usage.total_tokens),
        )
        if model == "qwen3-vl-rerank":
            self.rerank_count += 1


@dataclass(frozen=True)
class VerificationCase:
    kb_id: UUID
    workspace: str
    directory: Path
    source_key: str
    doc_id: str
    text: str
    marker: str


@dataclass(frozen=True)
class VerificationResult:
    workspaces: tuple[str, str]
    storage_counts: tuple[StorageCount, ...]


@dataclass(frozen=True)
class StorageCount:
    table: str
    workspace: str
    row_count: int


class VerificationRuntime(Protocol):
    async def get(self, kb_id: UUID) -> Any: ...

    async def close(self) -> None: ...


class StorageInspector(Protocol):
    async def assert_unused(
        self, cases: tuple[VerificationCase, VerificationCase]
    ) -> None: ...

    async def snapshot(
        self, cases: tuple[VerificationCase, VerificationCase], engines: tuple[Any, Any]
    ) -> tuple[Any, ...]: ...

    async def assert_deleted(self, case: VerificationCase, engine: Any) -> None: ...

    async def assert_cleaned(
        self, cases: tuple[VerificationCase, VerificationCase]
    ) -> None: ...


def rag_configuration_fingerprint(
    model_config: DashScopeConfig, database: RagDatabaseSettings
) -> str:
    """Bind real RAG evidence to both provider and engine configuration."""

    return rag_fingerprint(
        {
            "models": model_fingerprint(
                model_config, model_config.credential_ciphertext_sha256
            ),
            "database": {
                "host": database.host,
                "port": database.port,
                "name": database.database,
                "username": database.username,
                "credential_ciphertext_sha256": database.credential_ciphertext_sha256,
            },
            "image_digest": database.image_digest,
            "lightrag_commit": LIGHTRAG_COMMIT,
            "tokenizer_sha256": TOKENIZER_SHA256,
            "storages": (
                "PGKVStorage",
                "PGVectorStorage",
                "PGTableGraphStorage",
                "PGDocStatusStorage",
            ),
            "chunk_size": 1200,
            "chunk_overlap": 100,
            "embedding_dimension": 1024,
        }
    )


class RagVerificationOwner:
    """Serialize opt-in CLI runs inside the dedicated engine database."""

    _LOCK_KEY = 0x4349544552414701

    def __init__(self, database: RagDatabaseSettings) -> None:
        self._database = database
        self._connection: asyncpg.Connection | None = None

    async def __aenter__(self) -> RagVerificationOwner:
        database = self._database
        try:
            connection = await asyncpg.connect(
                host=database.host,
                port=database.port,
                database=database.database,
                user=database.username,
                password=database.password.get_secret_value(),
                timeout=5,
                command_timeout=5,
                server_settings={"application_name": "citerag-rag-verify-owner"},
            )
            self._connection = connection
            if not await connection.fetchval(
                "SELECT pg_try_advisory_lock($1)", self._LOCK_KEY
            ):
                raise RagVerificationError("owner", "another_verification_active")
        except (OSError, TimeoutError, asyncpg.PostgresError):
            await self.__aexit__(None, None, None)
            raise RagVerificationError("owner", "database_unavailable") from None
        except BaseException:
            await self.__aexit__(None, None, None)
            raise
        return self

    def assert_owned(self) -> None:
        if self._connection is None or self._connection.is_closed():
            raise RagVerificationError("owner", "ownership_lost")

    async def __aexit__(self, *_args: object) -> None:
        if self._connection is not None:
            connection, self._connection = self._connection, None
            await connection.close(timeout=3)


def write_rag_diagnostic(
    root: Path,
    cases: tuple[VerificationCase, VerificationCase],
    *,
    stage: str,
    category: str,
    reserved_requests: int,
) -> Path:
    """Persist a bounded, non-secret recovery list after interrupted verification."""

    if not all(re.fullmatch(r"[a-z_]{1,40}", part) for part in (stage, category)):
        raise ValueError("diagnostic category is invalid")
    if not 0 <= reserved_requests <= 40:
        raise ValueError("diagnostic request count is invalid")
    root.mkdir(parents=True, exist_ok=True)
    _restrict_path(root)
    path = root / f"rag-incomplete-{cases[0].kb_id.hex}.json"
    payload = json.dumps(
        {
            "schema_version": 1,
            "stage": stage,
            "category": category,
            "reserved_requests": reserved_requests,
            "workspaces": [
                {"workspace": case.workspace, "source_key": case.source_key}
                for case in cases
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    _restrict_path(path)
    return path


_TABLE_PATTERN = re.compile(r"lightrag_[a-z0-9_]{1,54}\Z")


class RagStorageInspector:
    """Read workspace-scoped evidence directly from the dedicated engine database."""

    def __init__(
        self,
        database: RagDatabaseSettings,
        *,
        connector: Callable[[], Awaitable[Any]] | None = None,
    ) -> None:
        self._database = database
        self._connector = connector or self._connect_real

    async def _connect_real(self) -> asyncpg.Connection:
        settings = self._database
        return await asyncpg.connect(
            host=settings.host,
            port=settings.port,
            database=settings.database,
            user=settings.username,
            password=settings.password.get_secret_value(),
            timeout=5,
            command_timeout=5,
            server_settings={"application_name": "citerag-rag-verify"},
        )

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[Any]:
        connection = await self._connector()
        try:
            yield connection
        finally:
            await connection.close(timeout=3)

    @staticmethod
    def _safe_table(table: str) -> str:
        lowered = table.lower()
        if not _TABLE_PATTERN.fullmatch(lowered):
            raise RagVerificationError("database", "table_name_invalid")
        return lowered

    async def _table_names(self, connection: Any) -> tuple[str, ...]:
        rows = await connection.fetch(
            "SELECT t.table_name FROM information_schema.tables t "
            "JOIN information_schema.columns c "
            "ON c.table_schema=t.table_schema AND c.table_name=t.table_name "
            "WHERE t.table_schema='public' AND t.table_type='BASE TABLE' "
            "AND t.table_name LIKE $1 AND c.column_name='workspace' "
            "ORDER BY t.table_name",
            "lightrag_%",
        )
        return tuple(self._safe_table(row["table_name"]) for row in rows)

    async def _count(self, connection: Any, table: str, workspace: str) -> int:
        table = self._safe_table(table)
        result = await connection.fetchval(
            f'SELECT COUNT(*) FROM "{table}" WHERE workspace=$1', workspace
        )
        if not isinstance(result, int) or result < 0:
            raise RagVerificationError("database", "row_count_invalid")
        return result

    async def assert_unused(
        self, cases: tuple[VerificationCase, VerificationCase]
    ) -> None:
        if any(case.directory.exists() or case.directory.is_symlink() for case in cases):
            raise RagVerificationError("setup", "workspace_occupied")
        async with self._connection() as connection:
            tables = await self._table_names(connection)
            for case in cases:
                for table in tables:
                    if await self._count(connection, table, case.workspace):
                        raise RagVerificationError("setup", "workspace_occupied")

    async def snapshot(
        self, cases: tuple[VerificationCase, VerificationCase], engines: tuple[Any, Any]
    ) -> tuple[StorageCount, ...]:
        counts = []
        async with self._connection() as connection:
            for case, engine in zip(cases, engines, strict=True):
                vector_table = self._safe_table(engine.chunks_vdb.table_name)
                if not vector_table.startswith("lightrag_vdb_chunks"):
                    raise RagVerificationError("database", "vector_table_invalid")
                required_tables = (
                    "lightrag_doc_full",
                    "lightrag_doc_chunks",
                    vector_table,
                    "lightrag_graph_nodes",
                    "lightrag_doc_status",
                )
                for table in required_tables:
                    count = await self._count(connection, table, case.workspace)
                    if count < 1:
                        raise RagVerificationError("database", "storage_rows_missing")
                    counts.append(StorageCount(table, case.workspace, count))
                for table, column in (
                    ("lightrag_doc_full", "doc_name"),
                    ("lightrag_doc_chunks", "file_path"),
                    (vector_table, "file_path"),
                    ("lightrag_doc_status", "file_path"),
                ):
                    mismatches = await connection.fetchval(
                        f'SELECT COUNT(*) FROM "{table}" '
                        f'WHERE workspace=$1 AND "{column}" IS DISTINCT FROM $2',
                        case.workspace,
                        case.source_key,
                    )
                    if mismatches != 0:
                        raise RagVerificationError("database", "cross_workspace_source")
        return tuple(counts)

    async def assert_deleted(self, case: VerificationCase, engine: Any) -> None:
        async with self._connection() as connection:
            for table in await self._table_names(connection):
                if table == "lightrag_llm_cache":
                    continue
                if await self._count(connection, table, case.workspace):
                    raise RagVerificationError("deletion", "derived_rows_remain")
            status = await engine.doc_status.get_by_id(case.doc_id)
            if status is not None:
                raise RagVerificationError("deletion", "status_remains")

    async def assert_cleaned(
        self, cases: tuple[VerificationCase, VerificationCase]
    ) -> None:
        async with self._connection() as connection:
            tables = await self._table_names(connection)
            for case in cases:
                for table in tables:
                    if await self._count(connection, table, case.workspace):
                        raise RagVerificationError("cleanup", "rows_remain")


def make_cases(
    root: Path, *, uuid_factory: Callable[[], UUID] = uuid4
) -> tuple[VerificationCase, VerificationCase]:
    """Generate non-colliding temporary identities; caller cannot supply a workspace."""

    identifiers = (uuid_factory(), uuid_factory())
    if identifiers[0] == identifiers[1]:
        raise ValueError("verification identifiers must be unique")
    root = root.resolve()
    cases = []
    for identifier, label, text, marker in (
        (identifiers[0], "A", _TEXT_A, "A-WARRANTY-12"),
        (identifiers[1], "B", _TEXT_B, "B-WARRANTY-24"),
    ):
        workspace, directory = workspace_for(identifier, root)
        cases.append(
            VerificationCase(
                kb_id=identifier,
                workspace=workspace,
                directory=directory,
                source_key=f"citerag-m0-{label}-{identifier.hex}.txt",
                doc_id=f"doc-{identifier.hex}",
                text=text,
                marker=marker,
            )
        )
    return cases[0], cases[1]


def _validate_cases(cases: tuple[VerificationCase, VerificationCase]) -> None:
    if len(cases) != 2 or sum(len(case.text) for case in cases) > 4000:
        raise RagVerificationError("setup", "input_limit")
    if cases[0].kb_id == cases[1].kb_id:
        raise RagVerificationError("setup", "duplicate_workspace")
    if any(not case.text or not case.source_key for case in cases):
        raise RagVerificationError("setup", "invalid_input")


async def _wait_processed(engine: Any, case: VerificationCase, track_id: str) -> None:
    deadline = time.monotonic() + _PROCESSING_DEADLINE_SECONDS
    while True:
        documents = await engine.aget_docs_by_track_id(track_id)
        status = getattr(documents.get(case.doc_id), "status", None)
        if status == "processed":
            return
        if status == "failed":
            raise RagVerificationError("ingestion", "document_failed")
        if time.monotonic() >= deadline:
            raise RagVerificationError("ingestion", "status_timeout")
        await asyncio.sleep(0.2)


def _assert_sources(
    response: Any,
    case: VerificationCase,
    other: VerificationCase,
    *,
    required: bool,
    must_be_empty: bool = False,
) -> None:
    if not isinstance(response, dict):
        raise RagVerificationError("retrieval", "protocol")
    status = response.get("status")
    if status not in {"success", "failure"}:
        raise RagVerificationError("retrieval", "protocol")
    data = response.get("data")
    if not isinstance(data, dict):
        raise RagVerificationError("retrieval", "protocol")
    chunks = data.get("chunks", [])
    references = data.get("references", [])
    if not isinstance(chunks, list) or not isinstance(references, list):
        raise RagVerificationError("retrieval", "protocol")
    paths = []
    for item in (*chunks, *references):
        if not isinstance(item, dict) or not isinstance(item.get("file_path"), str):
            raise RagVerificationError("retrieval", "source_invalid")
        paths.append(item["file_path"])
    if other.source_key in paths or any(path != case.source_key for path in paths):
        raise RagVerificationError("retrieval", "cross_workspace_source")
    if must_be_empty and (chunks or references):
        raise RagVerificationError("retrieval", "deleted_source_visible")
    reference_map: dict[str, str] = {}
    for reference in references:
        identifier = reference.get("reference_id")
        if not isinstance(identifier, str) or not identifier or identifier in reference_map:
            raise RagVerificationError("retrieval", "reference_mismatch")
        reference_map[identifier] = reference["file_path"]
    for chunk in chunks:
        content = chunk.get("content")
        if not isinstance(content, str) or other.marker in content:
            raise RagVerificationError("retrieval", "cross_workspace_content")
        reference_id = chunk.get("reference_id")
        if (
            not isinstance(reference_id, str)
            or reference_map.get(reference_id) != chunk["file_path"]
        ):
            raise RagVerificationError("retrieval", "reference_mismatch")
    if status == "failure":
        if required:
            raise RagVerificationError("retrieval", "empty_result")
        return
    if required and (not chunks or not references):
        raise RagVerificationError("retrieval", "source_missing")
    if required and not any(case.marker in chunk["content"] for chunk in chunks):
        raise RagVerificationError("retrieval", "evidence_missing")


async def _query(
    engine: Any,
    query: str,
    case: VerificationCase,
    other: VerificationCase,
    *,
    required: bool,
    must_be_empty: bool = False,
) -> None:
    from lightrag.base import QueryParam

    result = await engine.aquery_data(
        query,
        QueryParam(
            mode="naive",
            top_k=3,
            chunk_top_k=3,
            enable_rerank=True,
            include_references=True,
        ),
    )
    _assert_sources(result, case, other, required=required, must_be_empty=must_be_empty)


def _clean_directory(case: VerificationCase) -> None:
    directory = case.directory
    root = directory.parent.resolve()
    expected_workspace, expected_directory = workspace_for(case.kb_id, root)
    if (
        case.workspace != expected_workspace
        or directory != expected_directory
        or directory.is_symlink()
        or directory.resolve().parent != root
    ):
        raise RagVerificationError("cleanup", "unsafe_directory")
    if directory.exists():
        shutil.rmtree(directory)


async def _drop_generated(
    engines: tuple[Any, Any],
    cases: tuple[VerificationCase, VerificationCase],
    inspector: StorageInspector,
) -> None:
    for case, engine in zip(cases, engines, strict=True):
        if getattr(engine, "workspace", case.workspace) != case.workspace:
            raise RagVerificationError("cleanup", "workspace_mismatch")
        for name in _STORAGE_NAMES:
            storage = getattr(engine, name)
            if getattr(storage, "workspace", case.workspace) != case.workspace:
                raise RagVerificationError("cleanup", "workspace_mismatch")
            result = await storage.drop()
            if not isinstance(result, dict) or result.get("status") != "success":
                raise RagVerificationError("cleanup", "storage_drop_failed")
    await inspector.assert_cleaned(cases)
    for case in cases:
        _clean_directory(case)


async def verify_two_workspaces(
    cases: tuple[VerificationCase, VerificationCase],
    *,
    open_runtime: Callable[[], Awaitable[VerificationRuntime]],
    inspector: StorageInspector,
    observed_reranks: Callable[[], int] | None = None,
) -> VerificationResult:
    """Return only after isolation, deletion, restart, and scoped cleanup pass."""

    _validate_cases(cases)
    await inspector.assert_unused(cases)
    first = await open_runtime()
    try:
        a, b = await first.get(cases[0].kb_id), await first.get(cases[1].kb_id)
        for case, engine in zip(cases, (a, b), strict=True):
            track_id = await engine.ainsert(
                case.text, ids=case.doc_id, file_paths=case.source_key
            )
            await _wait_processed(engine, case, track_id)

        await _query(a, "A 产品质保期是多久？", cases[0], cases[1], required=True)
        await _query(b, "B 产品质保期是多久？", cases[1], cases[0], required=True)
        if observed_reranks is not None and observed_reranks() < 2:
            raise RagVerificationError("retrieval", "rerank_missing")
        await _query(a, cases[1].marker, cases[0], cases[1], required=False)
        await _query(b, cases[0].marker, cases[1], cases[0], required=False)
        counts = await inspector.snapshot(cases, (a, b))

        deletion = await a.adelete_by_doc_id(cases[0].doc_id)
        if (
            getattr(deletion, "status", None) != "success"
            or getattr(deletion, "doc_id", None) != cases[0].doc_id
        ):
            raise RagVerificationError("deletion", "result_invalid")
        await inspector.assert_deleted(cases[0], a)
    finally:
        await first.close()

    second = await open_runtime()
    try:
        a, b = await second.get(cases[0].kb_id), await second.get(cases[1].kb_id)
        await _query(b, "B 产品质保期是多久？", cases[1], cases[0], required=True)
        await _query(
            a,
            "A 产品质保期是多久？",
            cases[0],
            cases[1],
            required=False,
            must_be_empty=True,
        )
        await inspector.assert_deleted(cases[0], a)
        await _drop_generated((a, b), cases, inspector)
    finally:
        await second.close()
    return VerificationResult(
        workspaces=(cases[0].workspace, cases[1].workspace), storage_counts=counts
    )
