"""Real disposable PostgreSQL and synthetic files; the engine boundary is a local fake."""

import asyncio
import os
import time
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from test_ingestion_jobs import new_kb, upload, wait_job
from test_postgres_local import ORIGIN, migrate, seed_knowledge_base

from app.main import create_app
from app.models import ParsedBlockRecord
from app.rag.ingestion_adapter import IngestionError

pytestmark = pytest.mark.postgres
pytest_plugins = ["test_postgres_local"]


class FakeAdapter:
    def __init__(self):
        self.failure = None
        self.invalid_counts = False
        self.insert_started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.active = 0
        self.max_active = 0
        self.insert_count = 0
        self.writes = {}

    async def insert(self, kb_id, workspace, documents, _job_id):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.insert_count += 1
        self.insert_started.set()
        try:
            await self.release.wait()
            if self.failure == "insert":
                raise IngestionError("rebuild_required")
            if self.failure == "transient_insert":
                raise IngestionError("engine_unavailable")
            self.writes.setdefault((kb_id, workspace), {}).update(
                {document.id: document for document in documents}
            )
        finally:
            self.active -= 1

    async def verify(self, kb_id, workspace, documents):
        if self.failure == "verify":
            raise IngestionError("verification_failed")
        if self.invalid_counts:
            return {}
        stored = self.writes.get((kb_id, workspace), {})
        if any(stored.get(document.id) != document for document in documents):
            raise IngestionError("rebuild_required")
        return {document.id: 1 for document in documents}

    async def verify_empty(self, kb_id, workspace):
        if self.failure == "empty":
            raise IngestionError("verification_failed")
        assert not self.writes.get((kb_id, workspace))

    async def clear_workspace(self, kb_id, workspace, _document_ids):
        if self.failure == "cleanup":
            raise IngestionError("cleanup_failed")
        self.writes.pop((kb_id, workspace), None)


@pytest.fixture(autouse=True)
def no_provider_access(monkeypatch):
    async def start(_runtime, _assert_owned):
        return None

    monkeypatch.setattr("app.rag.runtime.RagRuntime.start", start)


@asynccontextmanager
async def application(database, settings, root, adapter, *, enabled=True):
    app = create_app(settings.model_copy(update={"ingestion_enabled": enabled}), database=database)
    app.state.source_root = root
    app.state.image_root = root.parent / "images"
    app.state.ingestion_adapter = adapter
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app, raise_app_exceptions=False),
            base_url=ORIGIN,
            headers={"Origin": ORIGIN},
        ) as api:
            yield api, app


@pytest_asyncio.fixture
async def environment(schema_database, tmp_path):
    database, settings = schema_database
    await migrate(database)
    adapter = FakeAdapter()
    async with application(database, settings, tmp_path / "sources", adapter) as (api, app):
        yield api, database, app, adapter


async def status(api, kb):
    items = (await api.get("/api/knowledge-bases")).json()["items"]
    return next(item["status"] for item in items if item["id"] == str(kb))


async def finished(api, response):
    assert response.status_code == 202
    return await wait_job(
        api,
        response.json()["id"],
        lambda job: (
            job["status"]
            in {
                "succeeded",
                "failed",
                "interrupted",
            }
        ),
    )


async def test_ingestion_only_becomes_ready_after_verification(environment):
    api, database, _, adapter = environment
    adapter.release.clear()
    kb = await new_kb(api)
    accepted = await upload(api, kb)
    assert accepted.status_code == 202
    await asyncio.wait_for(adapter.insert_started.wait(), 5)
    job = (await api.get(f"/api/jobs/{accepted.json()['id']}")).json()
    assert job["status"] == "running" and job["stage"] == "indexing"
    assert await status(api, kb) == "maintaining"
    document = job["document_ids"][0]
    for suffix in ("original", "blocks"):
        assert (await api.get(f"/api/documents/{document}/{suffix}")).status_code == 409
    assert (await api.post("/api/conversations", json={"kb_id": kb})).status_code == 409
    adapter.release.set()
    completed = await finished(api, accepted)
    assert completed["status"] == "succeeded" and completed["stage"] == "complete"
    assert await status(api, kb) == "ready"
    async with database.engine.connect() as connection:
        assert await connection.scalar(text("SELECT sum(chunk_count) FROM documents")) == 1
        assert await connection.scalar(text("SELECT revision FROM knowledge_bases")) == 1


@pytest.mark.parametrize("failure", ["insert", "verify", "counts"])
async def test_engine_failure_blocks_queries_and_downloads(environment, failure):
    api, _, _, adapter = environment
    adapter.failure = failure
    adapter.invalid_counts = failure == "counts"
    kb = await new_kb(api)
    job = await finished(api, await upload(api, kb))
    assert job["status"] == "failed" and job["engine_mutated"]
    assert await status(api, kb) == "blocked"
    assert (await api.post("/api/conversations", json={"kb_id": kb})).status_code == 409
    for suffix in ("original", "blocks"):
        endpoint = f"/api/documents/{job['document_ids'][0]}/{suffix}"
        assert (await api.get(endpoint)).status_code == 409
    assert (await upload(api, kb, data=b"Another document")).status_code == 409


async def test_explicit_retry_can_recover_verified_engine_failure(environment):
    api, _, _, adapter = environment
    adapter.failure = "verify"
    kb = await new_kb(api)
    job = await finished(api, await upload(api, kb))
    assert job["status"] == "failed" and job["can_retry"]
    adapter.failure = None
    retried = await api.post(f"/api/jobs/{job['id']}/retry")
    assert (await finished(api, retried))["status"] == "succeeded"
    assert await status(api, kb) == "ready"


async def test_preflight_error_leaves_existing_ready_library_unchanged(environment):
    api, database, _, adapter = environment
    kb, _ = await seed_knowledge_base(database, "Ready synthetic", "ready")
    job = await finished(api, await upload(api, kb, data=b"\x00broken"))
    assert job["status"] == "failed" and not job["engine_mutated"]
    assert await status(api, kb) == "ready"
    assert adapter.insert_count == 0
    async with database.engine.connect() as connection:
        assert await connection.scalar(text("SELECT revision FROM knowledge_bases")) == 0


async def test_same_library_excludes_parallel_tasks_and_all_engine_writes_are_serial(environment):
    api, database, _, adapter = environment
    adapter.release.clear()
    first_kb, second_kb = await new_kb(api), await new_kb(api)
    first = await upload(api, first_kb)
    await asyncio.wait_for(adapter.insert_started.wait(), 5)
    second = await upload(api, second_kb, data=b"Synthetic second library")
    conflict = await upload(api, first_kb, data=b"Concurrent synthetic document")
    rebuild_conflict = await api.post(
        f"/api/knowledge-bases/{first_kb}/rebuild",
        json={"client_request_id": str(uuid4())},
    )
    assert second.status_code == 202 and conflict.status_code == 409
    assert rebuild_conflict.status_code == 409
    async with database.engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM ingestion_jobs WHERE status IN ('queued', 'running')")
            )
            == 2
        )
    await asyncio.sleep(0.1)
    assert adapter.insert_count == 1 and adapter.max_active == 1
    adapter.release.set()
    assert (await finished(api, first))["status"] == "succeeded"
    assert (await finished(api, second))["status"] == "succeeded"
    assert adapter.insert_count == 2 and adapter.max_active == 1


async def test_restart_blocks_uncertain_engine_write_without_replaying(schema_database, tmp_path):
    database, settings = schema_database
    await migrate(database)
    adapter = FakeAdapter()
    root = tmp_path / "sources"
    async with application(database, settings, root, adapter, enabled=False) as (api, _):
        kb = await new_kb(api)
        accepted = await upload(api, kb)
        job = await wait_job(api, accepted.json()["id"], lambda job: job["stage"] == "parsed")
    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE ingestion_jobs SET status = 'running', stage = 'indexing', "
                "engine_mutated = true"
            )
        )
        await connection.execute(text("UPDATE knowledge_bases SET status = 'maintaining'"))
    async with application(database, settings, root, adapter) as (api, _):
        restored = (await api.get(f"/api/jobs/{job['id']}")).json()
        assert restored["status"] == "interrupted" and restored["engine_mutated"]
        assert await status(api, kb) == "blocked"
        await asyncio.sleep(0.1)
        assert adapter.insert_count == 0


async def test_rebuild_switch_is_persisted_and_restart_resolves_new_workspace(
    schema_database,
    tmp_path,
    monkeypatch,
):
    database, settings = schema_database
    await migrate(database)
    adapter = FakeAdapter()
    root = tmp_path / "sources"
    async with application(database, settings, root, adapter) as (api, _):
        kb = await new_kb(api)
        assert (await finished(api, await upload(api, kb)))["status"] == "succeeded"
        async with database.engine.connect() as connection:
            previous = await connection.scalar(text("SELECT active_workspace FROM knowledge_bases"))
        rebuild = await api.post(
            f"/api/knowledge-bases/{kb}/rebuild",
            json={"client_request_id": str(uuid4())},
        )
        result = await finished(api, rebuild)
        assert result["status"] == "succeeded" and not result["cleanup_pending"]
        assert (kb, previous) not in adapter.writes
        async with database.engine.connect() as connection:
            current = await connection.scalar(text("SELECT active_workspace FROM knowledge_bases"))
            assert bool(previous != current)
            assert await connection.scalar(
                text(
                    "SELECT revision = 2 AND hide_history_before_revision = 2 FROM knowledge_bases"
                )
            )

    selected = []

    async def capture_resolved(_runtime, _kb_id, workspace):
        selected.append(workspace)
        return object()

    monkeypatch.setattr("app.rag.runtime.RagRuntime._get", capture_resolved)
    async with application(database, settings, root, adapter) as (_, app):
        await app.state.rag_runtime.get(UUID(kb))
        assert bool(selected == [current])
        assert adapter.insert_count == 2


async def test_failed_acceptance_transaction_never_reports_success_or_exposes_orphan(environment):
    api, database, app, adapter = environment
    kb = await new_kb(api)
    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE FUNCTION reject_job() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN RAISE EXCEPTION 'synthetic private failure'; END $$"
            )
        )
        await connection.execute(
            text(
                "CREATE TRIGGER reject_job BEFORE INSERT ON ingestion_jobs "
                "FOR EACH ROW EXECUTE FUNCTION reject_job()"
            )
        )
    response = await upload(api, kb)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "persistence_failed"
    assert "synthetic private failure" not in response.text
    assert adapter.insert_count == 0
    async with database.engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM documents")) == 0
        assert await connection.scalar(text("SELECT count(*) FROM ingestion_jobs")) == 0
    assert (await api.get(f"/api/knowledge-bases/{kb}/documents")).json()["items"] == []
    # Unknown commit outcome is intentionally retained privately for recovery.
    assert len(list(app.state.source_root.glob("*.source"))) == 1


async def test_post_index_completion_transaction_failure_keeps_blocked(environment):
    api, database, _, _ = environment
    kb = await new_kb(api)
    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE FUNCTION reject_ready() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN IF NEW.status = 'ready' THEN RAISE EXCEPTION 'synthetic finish failure'; "
                "END IF; RETURN NEW; END $$"
            )
        )
        await connection.execute(
            text(
                "CREATE TRIGGER reject_ready BEFORE UPDATE ON knowledge_bases "
                "FOR EACH ROW EXECUTE FUNCTION reject_ready()"
            )
        )
    job = await finished(api, await upload(api, kb))
    assert job["status"] == "failed" and job["engine_mutated"]
    assert await status(api, kb) == "blocked"
    async with database.engine.connect() as connection:
        assert (
            await connection.scalar(text("SELECT count(*) FROM documents WHERE status = 'ready'"))
            == 0
        )


async def test_legacy_documents_and_jobs_are_inaccessible(environment):
    api, database, _, _ = environment
    kb = await new_kb(api)
    job = await finished(api, await upload(api, kb))
    legacy, _ = await seed_knowledge_base(database, "Legacy", "ready", owned=False)
    async with database.engine.begin() as connection:
        await connection.execute(text("UPDATE documents SET kb_id = :kb"), {"kb": legacy})
        await connection.execute(text("UPDATE ingestion_jobs SET kb_id = :kb"), {"kb": legacy})
    for suffix in ("original", "blocks"):
        endpoint = f"/api/documents/{job['document_ids'][0]}/{suffix}"
        assert (await api.get(endpoint)).status_code == 404
    assert (await api.get(f"/api/jobs/{job['id']}")).status_code == 404
    assert (await api.post(f"/api/jobs/{job['id']}/retry")).status_code == 404
    assert (await api.get(f"/api/knowledge-bases/{kb}/documents")).json()["items"] == []


async def test_download_rechecks_maintenance_after_disk_read(environment, monkeypatch):
    api, _, _, adapter = environment
    kb = await new_kb(api)
    job = await finished(api, await upload(api, kb))
    original_to_thread = asyncio.to_thread
    disk_read_started, allow_disk_read = asyncio.Event(), asyncio.Event()

    async def pause_original_read(function, *args, **kwargs):
        if getattr(function, "__name__", "") == "read_bounded":
            disk_read_started.set()
            await allow_disk_read.wait()
        return await original_to_thread(function, *args, **kwargs)

    monkeypatch.setattr("app.api.documents.asyncio.to_thread", pause_original_read)
    adapter.release.clear()
    adapter.insert_started.clear()
    request = asyncio.create_task(api.get(f"/api/documents/{job['document_ids'][0]}/original"))
    try:
        await asyncio.wait_for(disk_read_started.wait(), 5)
        accepted = await upload(api, kb, data=b"Synthetic additional manual")
        assert accepted.status_code == 202
        await asyncio.wait_for(adapter.insert_started.wait(), 5)
        assert await status(api, kb) == "maintaining"
        allow_disk_read.set()
        response = await request
        assert response.status_code == 409
    finally:
        adapter.release.set()
        allow_disk_read.set()
        if not request.done():
            request.cancel()


async def test_upgrade_from_0003_preserves_existing_owned_and_legacy_rows(schema_database):
    database, _ = schema_database
    await migrate(database, "0003_knowledge_management")
    await seed_knowledge_base(database, "Owned", "ready")
    await seed_knowledge_base(database, "Legacy", "blocked", owned=False)
    snapshot_query = text(
        "SELECT id, name, status, active_workspace, local_owner_id, created_at "
        "FROM knowledge_bases ORDER BY name"
    )
    async with database.engine.connect() as connection:
        before = list((await connection.execute(snapshot_query)).all())
    await migrate(database)
    await migrate(database)
    async with database.engine.connect() as connection:
        after = list((await connection.execute(snapshot_query)).all())
        assert bool(before == after)
        assert await connection.scalar(text("SELECT count(*) FROM documents")) == 0
        assert await connection.scalar(text("SELECT count(*) FROM ingestion_jobs")) == 0
        assert await connection.scalar(text("SELECT sum(revision) FROM knowledge_bases")) == 0


async def test_parsed_blocks_recheck_maintenance_after_database_read(environment, monkeypatch):
    api, _, _, adapter = environment
    kb = await new_kb(api)
    job = await finished(api, await upload(api, kb))
    original_scalars = AsyncSession.scalars
    blocks_read, release_blocks = asyncio.Event(), asyncio.Event()

    async def pause_blocks_read(session, statement, *args, **kwargs):
        result = await original_scalars(session, statement, *args, **kwargs)
        entities = [item.get("entity") for item in getattr(statement, "column_descriptions", [])]
        if ParsedBlockRecord in entities:
            blocks_read.set()
            await release_blocks.wait()
        return result

    monkeypatch.setattr(AsyncSession, "scalars", pause_blocks_read)
    adapter.release.clear()
    adapter.insert_started.clear()
    request = asyncio.create_task(api.get(f"/api/documents/{job['document_ids'][0]}/blocks"))
    try:
        await asyncio.wait_for(blocks_read.wait(), 5)
        accepted = await upload(api, kb, data=b"Synthetic revised database manual")
        assert accepted.status_code == 202
        await asyncio.wait_for(adapter.insert_started.wait(), 5)
        assert await status(api, kb) == "maintaining"
        release_blocks.set()
        response = await request
        assert response.status_code == 409
    finally:
        adapter.release.set()
        release_blocks.set()
        if not request.done():
            request.cancel()


async def test_startup_does_not_mutate_unclaimed_legacy_library(schema_database, tmp_path):
    database, settings = schema_database
    await migrate(database)
    legacy, _ = await seed_knowledge_base(database, "Unclaimed legacy", "maintaining", owned=False)
    async with application(database, settings, tmp_path / "sources", FakeAdapter()):
        async with database.engine.connect() as connection:
            value = await connection.scalar(
                text("SELECT status FROM knowledge_bases WHERE id = :kb"),
                {"kb": legacy},
            )
            assert value == "maintaining"


async def test_startup_orphan_cleanup_preserves_committed_sources_and_recent_uploads(
    schema_database,
    tmp_path,
):
    database, settings = schema_database
    await migrate(database)
    root = tmp_path / "sources"
    adapter = FakeAdapter()
    async with application(database, settings, root, adapter, enabled=False) as (api, _):
        kb = await new_kb(api)
        accepted = await upload(api, kb)
        await wait_job(api, accepted.json()["id"], lambda job: job["stage"] == "parsed")
    committed = next(root.glob("*.source"))
    orphan = root / f"{uuid4().hex}.source"
    partial = root / f"{uuid4().hex}.partial"
    recent = root / f"{uuid4().hex}.source"
    unrelated = root / "private-note.txt"
    for path in (orphan, partial, recent, unrelated):
        path.write_bytes(b"Synthetic local cleanup fixture")
    old = time.time() - 25 * 3600
    for path in (committed, orphan, partial, unrelated):
        os.utime(path, (old, old))
    async with application(database, settings, root, adapter, enabled=False) as (api, _):
        assert committed.is_file() and recent.is_file() and unrelated.is_file()
        assert not orphan.exists() and not partial.exists()
        listed = (await api.get(f"/api/knowledge-bases/{kb}/documents")).json()["items"]
        assert len(listed) == 1
        assert (await api.get(f"/api/documents/{listed[0]['id']}/original")).status_code == 200
        assert adapter.insert_count == 0


async def test_failed_rebuild_keeps_previous_workspace_and_stays_blocked(environment):
    api, database, _, adapter = environment
    kb = await new_kb(api)
    assert (await finished(api, await upload(api, kb)))["status"] == "succeeded"
    async with database.engine.connect() as connection:
        previous = await connection.scalar(text("SELECT active_workspace FROM knowledge_bases"))
    adapter.failure = "verify"
    accepted = await api.post(
        f"/api/knowledge-bases/{kb}/rebuild",
        json={"client_request_id": str(uuid4())},
    )
    job = await finished(api, accepted)
    assert job["status"] == "failed" and job["engine_mutated"]
    assert await status(api, kb) == "blocked"
    async with database.engine.connect() as connection:
        current = await connection.scalar(text("SELECT active_workspace FROM knowledge_bases"))
        assert bool(current == previous)


async def test_restart_requeues_safe_parsing_interruption_and_finishes_once(
    schema_database, tmp_path
):
    database, settings = schema_database
    await migrate(database)
    root, adapter = tmp_path / "sources", FakeAdapter()
    async with application(database, settings, root, adapter, enabled=False) as (api, _):
        kb = await new_kb(api)
        accepted = await upload(api, kb)
        job = await wait_job(api, accepted.json()["id"], lambda job: job["stage"] == "parsed")
    async with database.engine.begin() as connection:
        await connection.execute(
            text("UPDATE ingestion_jobs SET status = 'running', stage = 'parsing'")
        )
    async with application(database, settings, root, adapter) as (api, _):
        recovered = await wait_job(api, job["id"], lambda job: job["status"] == "succeeded")
        assert recovered["engine_mutated"] and await status(api, kb) == "ready"
        assert adapter.insert_count == 1
