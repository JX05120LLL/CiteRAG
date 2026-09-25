"""Isolated business PostgreSQL with synthetic originals and an in-memory engine."""

import asyncio
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from test_ingestion_lifecycle import FakeAdapter, application, finished, new_kb, upload
from test_postgres_local import migrate

from app.models import Document, KnowledgeBase, ParsedBlockRecord
from app.rag.query_adapter import RetrievedChunk

pytest_plugins = ["test_postgres_local"]
pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def no_provider_access(monkeypatch):
    async def fake_start(_runtime, _owner):
        return None
    monkeypatch.setattr("app.rag.runtime.RagRuntime.start", fake_start)


@pytest_asyncio.fixture
async def lifecycle(schema_database, tmp_path):
    db, settings = schema_database
    await migrate(db)
    adapter = FakeAdapter()
    root = tmp_path / "sources"
    async with application(db, settings, root, adapter) as (api, app):
        yield api, db, app, adapter


async def documents(api, kb):
    result = await api.get(f"/api/knowledge-bases/{kb}/documents")
    assert result.status_code == 200
    return result.json()["items"]


@pytest.mark.asyncio
async def test_delete_last_document_clears_workspace_and_original(lifecycle):
    api, db, app, adapter = lifecycle
    kb = await new_kb(api)
    uploaded = await finished(api, await upload(api, kb))
    doc_id = uploaded["document_ids"][0]
    async with db.sessions() as session:
        doc = await session.get(Document, UUID(doc_id))
        original = app.state.source_store.path_for(doc.storage_key)
        prior_workspace = (await session.get(KnowledgeBase, UUID(kb))).active_workspace
    assert original.is_file()
    request_id = str(uuid4())
    accepted = await api.post(f"/api/documents/{doc_id}/delete",
                              json={"client_request_id": request_id})
    assert accepted.status_code == 202
    assert (await api.get(f"/api/documents/{doc_id}/original")).status_code != 200
    completed = await finished(api, accepted)
    assert completed["status"] == "succeeded" and not completed["cleanup_pending"]
    assert (kb, prior_workspace) not in adapter.writes
    assert not original.exists()
    assert (await api.post(f"/api/documents/{doc_id}/delete",
                           json={"client_request_id": request_id})).json()["id"] == completed["id"]
    async with db.sessions() as session:
        base = await session.get(KnowledgeBase, UUID(kb))
        doc = await session.get(Document, UUID(doc_id))
        assert base.status == "empty" and base.revision == 2
        assert base.hide_history_before_revision == 2
        assert doc.status == "deleted" and doc.parsed_text is None
        assert await session.scalar(select(ParsedBlockRecord).where(
            ParsedBlockRecord.document_id == doc.id)) is None


@pytest.mark.asyncio
async def test_replacement_switches_manifest_and_cleans_old_source(lifecycle):
    api, db, app, adapter = lifecycle
    kb = await new_kb(api)
    old = await finished(api, await upload(api, kb, data=b"Old limit 42 C.\n"))
    old_id = old["document_ids"][0]
    async with db.sessions() as session:
        old_doc = await session.get(Document, UUID(old_id))
        old_source = app.state.source_store.path_for(old_doc.storage_key)
        prior_workspace = (await session.get(KnowledgeBase, UUID(kb))).active_workspace
    accepted = await api.post(f"/api/documents/{old_id}/replacement",
        data={"client_request_id": str(uuid4())},
        files={"file": ("revised.txt", b"New limit 58 C.\n", "text/plain")})
    assert accepted.status_code == 202
    assert (await api.get(f"/api/documents/{old_id}/blocks")).status_code != 200
    complete = await finished(api, accepted)
    assert complete["status"] == "succeeded" and not old_source.exists()
    assert (kb, prior_workspace) not in adapter.writes
    rows = await documents(api, kb)
    assert {row["status"] for row in rows} == {"ready", "deleted"}
    assert next(row for row in rows if row["status"] == "ready")["filename"] == "revised.txt"
    assert (await api.get(f"/api/documents/{old_id}/original")).status_code == 404


@pytest.mark.asyncio
async def test_cleanup_failure_blocks_then_retry_finishes_without_reindex(lifecycle):
    api, db, _, adapter = lifecycle
    kb = await new_kb(api)
    old = await finished(api, await upload(api, kb))
    old_id = old["document_ids"][0]
    before = adapter.insert_count
    adapter.failure = "cleanup"
    failed = await finished(api, await api.post(f"/api/documents/{old_id}/delete",
        json={"client_request_id": str(uuid4())}))
    assert failed["status"] == "failed" and failed["cleanup_pending"]
    async with db.sessions() as session:
        assert (await session.get(KnowledgeBase, UUID(kb))).status == "blocked"
    assert (await api.get(f"/api/documents/{old_id}/original")).status_code == 409
    adapter.failure = None
    recovered = await finished(api, await api.post(f"/api/jobs/{failed['id']}/retry"))
    assert recovered["status"] == "succeeded" and not recovered["cleanup_pending"]
    assert adapter.insert_count == before


@pytest.mark.asyncio
async def test_preflight_failure_restores_ready_without_revision_change(lifecycle):
    api, db, app, _ = lifecycle
    kb = await new_kb(api)
    first = await finished(api, await upload(api, kb, data=b"First safe note.\n"))
    second = await finished(api, await upload(api, kb, data=b"Second safe note.\n"))
    async with db.sessions() as session:
        retained = await session.get(Document, UUID(second["document_ids"][0]))
        app.state.source_store.discard(retained.storage_key)
        before = (await session.get(KnowledgeBase, UUID(kb))).revision
    failed = await finished(api, await api.post(
        f"/api/documents/{first['document_ids'][0]}/delete",
        json={"client_request_id": str(uuid4())}))
    assert failed["status"] == "failed" and not failed["engine_mutated"]
    async with db.sessions() as session:
        base = await session.get(KnowledgeBase, UUID(kb))
        old = await session.get(Document, UUID(first["document_ids"][0]))
        assert base.status == "ready" and base.revision == before
        assert old.status == "ready"


@pytest.mark.asyncio
async def test_engine_failure_stays_blocked_then_retries_in_candidate(lifecycle):
    api, db, _, adapter = lifecycle
    kb = await new_kb(api)
    old = await finished(api, await upload(api, kb, data=b"First note.\n"))
    other = await finished(api, await upload(api, kb, data=b"Second note.\n"))
    adapter.failure = "transient_insert"
    failed = await finished(api, await api.post(
        f"/api/documents/{old['document_ids'][0]}/delete",
        json={"client_request_id": str(uuid4())}))
    assert failed["status"] == "failed" and failed["engine_mutated"]
    assert failed["can_retry"]
    async with db.sessions() as session:
        assert (await session.get(KnowledgeBase, UUID(kb))).status == "blocked"
    adapter.failure = None
    repaired = await finished(api, await api.post(f"/api/jobs/{failed['id']}/retry"))
    assert repaired["status"] == "succeeded"
    rows = await documents(api, kb)
    assert {row["status"] for row in rows} == {"ready", "deleted"}
    assert any(row["id"] == other["document_ids"][0] and row["status"] == "ready"
               for row in rows)


@pytest.mark.asyncio
async def test_failed_last_document_delete_can_rebuild_to_verified_empty_space(lifecycle):
    api, db, app, adapter = lifecycle
    kb = await new_kb(api)
    uploaded = await finished(api, await upload(api, kb, data=b"Last synthetic note.\n"))
    doc_id = uploaded["document_ids"][0]
    async with db.sessions() as session:
        original = app.state.source_store.path_for(
            (await session.get(Document, UUID(doc_id))).storage_key)
    adapter.failure = "empty"
    failed = await finished(api, await api.post(f"/api/documents/{doc_id}/delete",
        json={"client_request_id": str(uuid4())}))
    assert failed["status"] == "failed" and failed["engine_mutated"]
    async with db.sessions() as session:
        assert (await session.get(KnowledgeBase, UUID(kb))).status == "blocked"
    adapter.failure = None
    repaired = await finished(api, await api.post(f"/api/knowledge-bases/{kb}/rebuild",
        json={"client_request_id": str(uuid4())}))
    assert repaired["status"] == "succeeded" and not original.exists()
    async with db.sessions() as session:
        base = await session.get(KnowledgeBase, UUID(kb))
        assert base.status == "empty"
        assert (await session.get(Document, UUID(doc_id))).status == "deleted"


@pytest.mark.asyncio
async def test_interrupted_replacement_stays_blocked_after_process_restart(
    schema_database, tmp_path
):
    db, settings = schema_database
    await migrate(db)
    adapter = FakeAdapter()
    root = tmp_path / "sources"
    async with application(db, settings, root, adapter) as (api, _):
        kb = await new_kb(api)
        old = await finished(api, await upload(api, kb, data=b"Old synthetic note.\n"))
        adapter.insert_started.clear()
        adapter.release.clear()
        accepted = await api.post(f"/api/documents/{old['document_ids'][0]}/replacement",
            data={"client_request_id": str(uuid4())},
            files={"file": ("new.txt", b"New synthetic note.\n", "text/plain")})
        assert accepted.status_code == 202
        await asyncio.wait_for(adapter.insert_started.wait(), 5)
        job_id = accepted.json()["id"]
    adapter.release.set()
    async with application(db, settings, root, adapter) as (api, _):
        job = (await api.get(f"/api/jobs/{job_id}")).json()
        assert job["status"] == "interrupted" and job["engine_mutated"]
        async with db.sessions() as session:
            assert (await session.get(KnowledgeBase, UUID(kb))).status == "blocked"
        repaired = await finished(api, await api.post(f"/api/jobs/{job_id}/retry"))
        assert repaired["status"] == "succeeded"
        rows = await documents(api, kb)
        assert {row["status"] for row in rows} == {"ready", "deleted"}


@pytest.mark.asyncio
async def test_delete_interrupts_late_answer_and_hides_old_evidence(lifecycle):
    api, db, app, _ = lifecycle
    kb = await new_kb(api)
    old = await finished(api, await upload(api, kb, data=b"The safe limit is 42 C.\n"))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    source = "source_" + old["document_ids"][0].replace("-", "")
    started, release = asyncio.Event(), asyncio.Event()

    class WaitingQuery:
        async def retrieve(self, *_args):
            started.set()
            await release.wait()
            return [RetrievedChunk("synthetic", source, "The safe limit is 42 C.")]

    class Answer:
        async def answer(self, *_args):
            return ('{"status":"answered","text":"The safe limit is 42 C.",'
                    '"evidence_ids":["E1"]}')

    app.state.answer_enabled = True
    app.state.query_adapter = WaitingQuery()
    app.state.answer_adapter = Answer()
    pending = asyncio.create_task(api.post(f"/api/conversations/{chat}/messages", json={
        "client_message_id": str(uuid4()), "text": "What is the limit?",
    }))
    await asyncio.wait_for(started.wait(), 5)
    deleted = await finished(api, await api.post(
        f"/api/documents/{old['document_ids'][0]}/delete",
        json={"client_request_id": str(uuid4())}))
    assert deleted["status"] == "succeeded"
    release.set()
    result = await pending
    assert result.status_code == 409 and result.json()["detail"]["code"] == "kb_changed"
    history = (await api.get(f"/api/conversations/{chat}/messages")).json()["items"]
    assert history[0]["hidden"] and not history[0]["text"] and not history[0]["citations"]
