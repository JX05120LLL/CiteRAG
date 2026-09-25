"""SSE events are ordered around durable input and checked final answers."""

# Pytest consumes imported fixtures by name; Ruff cannot see fixture injection.
# ruff: noqa: F401, F811

import asyncio
import json
from uuid import UUID, uuid4

import pytest
from test_ingestion_lifecycle import finished, new_kb, upload
from test_m13_answer_api import Answer, Query, m13_environment, no_provider_access

from app.models import KnowledgeBase
from app.rag.query_adapter import RetrievedChunk
from app.rag.streamed_answer import ExtractiveDraft
from app.services.answers import AnswerService
from app.services.errors import ServiceError

pytest_plugins = ["test_postgres_local"]
pytestmark = pytest.mark.postgres


def events(response):
    result = []
    for block in response.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        result.append((lines["event"], json.loads(lines["data"])))
    return result


@pytest.mark.asyncio
async def test_stream_accepts_input_then_only_emits_verified_saved_text(m13_environment):
    api, _, app, _ = m13_environment
    kb = await new_kb(api)
    job = await finished(api, await upload(api, kb, data=b"The safe limit is 42 C.\n"))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    app.state.answer_enabled = True
    app.state.query_adapter = Query([RetrievedChunk(
        "chunk_a", "source_" + job["document_ids"][0].replace("-", ""),
        "The safe limit is 42 C.",
    )])
    app.state.answer_adapter = Answer()
    body = {"client_message_id": str(uuid4()), "text": "What is the limit?"}
    response = await api.post(f"/api/conversations/{chat}/messages/stream", json=body)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    received = events(response)
    assert received[0][0] == "accepted" and received[0][1]["status"] == "running"
    assert not received[0][1]["saved"] and received[0][1]["text"] == ""
    assert "T" in received[0][1]["created_at"]
    assert received[-1][0] == "saved" and received[-1][1]["status"] == "answered"
    assert received[-1][1]["saved"]
    streamed = "".join(data["text"] for name, data in received if name == "delta")
    assert streamed == received[-1][1]["text"]
    assert all(data["saved"] for name, data in received if name == "delta")
    assert received[-1][1]["citations"][0]["locator"]["line_start"] == 1
    replay = events(await api.post(f"/api/conversations/{chat}/messages/stream", json=body))
    assert replay[0][0] != "accepted" and replay[-1][1] == received[-1][1]


@pytest.mark.asyncio
async def test_stream_does_not_emit_fake_answer_without_evidence(m13_environment):
    api, _, app, _ = m13_environment
    kb = await new_kb(api)
    await finished(api, await upload(api, kb, data=b"The safe limit is 42 C.\n"))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    app.state.answer_enabled = True
    app.state.query_adapter = Query([])
    app.state.answer_adapter = Answer()
    received = events(await api.post(f"/api/conversations/{chat}/messages/stream", json={
        "client_message_id": str(uuid4()), "text": "What is the limit?",
    }))
    assert [name for name, _ in received] == ["accepted", "saved"]
    assert received[-1][1]["status"] == "insufficient_evidence"
    assert received[-1][1]["text"] == "" and received[-1][1]["citations"] == []


@pytest.mark.asyncio
async def test_stream_retracts_late_answer_when_library_enters_maintenance(m13_environment):
    api, db, app, _ = m13_environment
    kb = await new_kb(api)
    job = await finished(api, await upload(api, kb, data=b"The safe limit is 42 C.\n"))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    app.state.answer_enabled = True

    class ChangingQuery(Query):
        async def retrieve(self, *args):
            async with db.sessions() as session:
                row = await session.get(KnowledgeBase, UUID(kb))
                row.status, row.revision = "maintaining", row.revision + 1
                await session.commit()
            return await super().retrieve(*args)

    app.state.query_adapter = ChangingQuery([RetrievedChunk(
        "chunk_a", "source_" + job["document_ids"][0].replace("-", ""),
        "The safe limit is 42 C.",
    )])
    app.state.answer_adapter = Answer()
    received = events(await api.post(f"/api/conversations/{chat}/messages/stream", json={
        "client_message_id": str(uuid4()), "text": "What is the limit?",
    }))
    assert [name for name, _ in received] == ["accepted", "error"]
    assert received[-1][1]["code"] == "kb_changed"
    history = (await api.get(f"/api/conversations/{chat}/messages")).json()["items"]
    assert history[0]["hidden"] and history[0]["text"] == ""


def test_extractive_draft_only_releases_literal_evidence_prefix():
    draft = ExtractiveDraft({"E1": "The safe limit is 42 C."})
    assert draft.feed('{"status":"answered","text":"The safe ') == "The safe "
    assert draft.feed('limit is 42') == "limit is 42"
    assert draft.feed(' C.","evidence_ids":["E1"]}') == " C."
    bad = ExtractiveDraft({"E1": "The safe limit is 42 C."})
    assert bad.feed('{"status":"answered","text":"The safe limit is 52') == ""


@pytest.mark.asyncio
async def test_stream_failure_after_verified_prefix_saves_partial_without_citation(m13_environment):
    api, _, app, _ = m13_environment
    kb = await new_kb(api)
    job = await finished(api, await upload(api, kb, data=b"The safe limit is 42 C.\n"))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    app.state.answer_enabled = True
    app.state.query_adapter = Query([RetrievedChunk(
        "chunk_a", "source_" + job["document_ids"][0].replace("-", ""),
        "The safe limit is 42 C.",
    )])

    class BrokenStream(Answer):
        async def stream_with_context(self, question, evidence, context):
            yield '{"status":"answered","text":"The safe '
            yield 'limit is 42'
            raise RuntimeError("synthetic stream failure")

    app.state.answer_adapter = BrokenStream()
    body = {"client_message_id": str(uuid4()), "text": "What is the limit?"}
    received = events(await api.post(f"/api/conversations/{chat}/messages/stream", json=body))
    assert [name for name, _ in received] == ["accepted", "delta", "delta", "saved"]
    assert all(data["saved"] is False for name, data in received if name == "delta")
    final = received[-1][1]
    assert final["status"] == "partial" and final["saved"]
    assert final["text"] == "The safe limit is 42" and final["citations"] == []
    history = (await api.get(f"/api/conversations/{chat}/messages")).json()["items"]
    assert history[0]["status"] == "partial"
    replay = events(await api.post(f"/api/conversations/{chat}/messages/stream", json=body))
    assert [name for name, _ in replay] == ["saved"]
    assert replay[-1][1] == final


@pytest.mark.asyncio
async def test_preview_precedes_completion_and_maintenance_blocks_final(m13_environment):
    api, db, app, _ = m13_environment
    kb = await new_kb(api)
    job = await finished(api, await upload(api, kb, data=b"The safe limit is 42 C.\n"))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()
    ready, release = asyncio.Event(), asyncio.Event()
    previews = []

    class PausedStream(Answer):
        async def stream_with_context(self, question, evidence, context):
            yield '{"status":"answered","text":"The safe limit'
            await release.wait()
            yield ' is 42 C.","evidence_ids":["E1"]}'

    async def on_preview(_attempt, delta):
        previews.append(delta)
        ready.set()

    retriever = Query([RetrievedChunk(
        "chunk_a", "source_" + job["document_ids"][0].replace("-", ""),
        "The safe limit is 42 C.",
    )])
    async with db.sessions() as session:
        task = asyncio.create_task(AnswerService(session).ask(
            UUID(chat["owner_id"]), UUID(chat["id"]), uuid4(), "What is the limit?",
            retriever, PausedStream(), on_preview=on_preview,
        ))
        await asyncio.wait_for(ready.wait(), 5)
        assert previews == ["The safe limit"] and not task.done()
        async with db.sessions() as maintenance:
            base = await maintenance.get(KnowledgeBase, UUID(kb))
            base.status, base.revision = "maintaining", base.revision + 1
            await maintenance.commit()
        release.set()
        with pytest.raises(ServiceError) as stopped:
            await task
        assert stopped.value.code == "kb_changed"
    history = (await api.get(f"/api/conversations/{chat['id']}/messages")).json()["items"]
    assert history[0]["hidden"] and history[0]["text"] == ""


@pytest.mark.asyncio
async def test_disconnect_after_preview_saves_partial_and_allows_new_attempt(m13_environment):
    api, db, app, _ = m13_environment
    kb = await new_kb(api)
    job = await finished(api, await upload(api, kb, data=b"The safe limit is 42 C.\n"))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()
    ready = asyncio.Event()

    class HangingStream(Answer):
        async def stream_with_context(self, question, evidence, context):
            yield '{"status":"answered","text":"The safe limit'
            await asyncio.Event().wait()

    async def on_preview(_attempt, _delta):
        ready.set()
        await asyncio.Event().wait()

    retriever = Query([RetrievedChunk(
        "chunk_a", "source_" + job["document_ids"][0].replace("-", ""),
        "The safe limit is 42 C.",
    )])
    async with db.sessions() as session:
        task = asyncio.create_task(AnswerService(session).ask(
            UUID(chat["owner_id"]), UUID(chat["id"]), uuid4(), "What is the limit?",
            retriever, HangingStream(), on_preview=on_preview,
        ))
        await asyncio.wait_for(ready.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    history = (await api.get(f"/api/conversations/{chat['id']}/messages")).json()["items"]
    assert history[0]["status"] == "partial"
    assert history[0]["text"] == "The safe limit" and history[0]["citations"] == []
    app.state.answer_enabled = True
    app.state.query_adapter, app.state.answer_adapter = retriever, Answer()
    retried = await api.post(
        f"/api/conversations/{chat['id']}/messages/{history[0]['message_id']}/retry",
        json={"attempt_id": str(uuid4())},
    )
    assert retried.status_code == 200 and retried.json()["status"] == "answered"
