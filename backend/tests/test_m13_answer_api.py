"""M1-3 slice: real isolated business DB, synthetic indexed source, fake model."""

import asyncio
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from test_ingestion_lifecycle import FakeAdapter, application, finished, new_kb, upload
from test_postgres_local import migrate

from app.models import (
    AnswerAttempt,
    Conversation,
    ConversationMessage,
    ConversationSummary,
    KnowledgeBase,
)
from app.rag.query_adapter import QueryError, RetrievedChunk

pytest_plugins = ["test_postgres_local"]
pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def no_provider_access(monkeypatch):
    async def fake_start(_runtime, _assert_owned):
        return None
    monkeypatch.setattr("app.rag.runtime.RagRuntime.start", fake_start)


@pytest_asyncio.fixture
async def m13_environment(schema_database, tmp_path):
    database, settings = schema_database
    await migrate(database)
    adapter = FakeAdapter()
    async with application(database, settings, tmp_path / "sources", adapter) as (api, app):
        yield api, database, app, adapter


class Query:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def retrieve(self, kb, workspace, question, sources):
        self.calls += 1
        assert question == "What is the limit?" and sources
        return self.result


class Answer:
    async def answer(self, question, evidence):
        assert question == "What is the limit?" and evidence[0]["id"] == "E1"
        return '{"status":"answered","text":"The safe limit is 42 C.","evidence_ids":["E1"]}'


class ContextAnswer(Answer):
    def __init__(self):
        self.contexts = []

    async def answer_with_context(self, question, evidence, context):
        self.contexts.append(context)
        return await self.answer(question, evidence)


@pytest.mark.asyncio
async def test_answer_is_saved_once_with_real_location_and_replay(m13_environment):
    api, db, app, _ = m13_environment
    kb = await new_kb(api)
    job = await finished(api, await upload(api, kb, data=b"The safe limit is 42 C.\n"))
    assert job["status"] == "succeeded"
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    app.state.answer_enabled = True
    query = Query([RetrievedChunk("chunk_a", "source_" + job["document_ids"][0].replace("-", ""),
                                  "The safe limit is 42 C.")])
    app.state.query_adapter = query
    app.state.answer_adapter = Answer()
    key = str(uuid4())
    body = {"client_message_id": key, "text": "What is the limit?"}
    first = await api.post(f"/api/conversations/{chat}/messages", json=body)
    assert first.status_code == 200, first.json()
    assert first.json()["status"] == "answered"
    assert first.json()["citations"][0]["locator"]["line_start"] == 1
    replay = await api.post(f"/api/conversations/{chat}/messages", json=body)
    assert replay.status_code == 200 and replay.json() == first.json()
    assert query.calls == 1
    assert (await api.post(f"/api/conversations/{chat}/messages", json={
        "client_message_id": key, "text": "different",
    })).status_code == 409
    history = (await api.get(f"/api/conversations/{chat}/messages")).json()["items"]
    assert len(history) == 1 and history[0]["status"] == "answered"
    async with db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(ConversationMessage)) == 1
        assert await session.scalar(select(func.count()).select_from(AnswerAttempt)) == 1
        base = await session.get(KnowledgeBase, kb)
        base.status, base.revision = "blocked", base.revision + 1
        await session.commit()
    blocked = await api.post(f"/api/conversations/{chat}/messages", json=body)
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "kb_not_ready"
    hidden = (await api.get(f"/api/conversations/{chat}/messages")).json()["items"]
    assert hidden[0]["hidden"] and not hidden[0]["text"] and not hidden[0]["citations"]
    async with db.sessions() as session:
        base = await session.get(KnowledgeBase, kb)
        base.status = "ready"
        await session.commit()
    changed = await api.post(f"/api/conversations/{chat}/messages", json=body)
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "kb_changed"


@pytest.mark.asyncio
async def test_chat_rename_and_delete_remove_messages_and_summary(m13_environment):
    api, db, app, _ = m13_environment
    kb = await new_kb(api)
    job = await finished(api, await upload(api, kb, data=b"The safe limit is 42 C.\n"))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    app.state.answer_enabled = True
    app.state.query_adapter = Query([RetrievedChunk(
        "chunk_a", "source_" + job["document_ids"][0].replace("-", ""),
        "The safe limit is 42 C.",
    )])
    app.state.answer_adapter = Answer()
    assert (await api.post(f"/api/conversations/{chat}/messages", json={
        "client_message_id": str(uuid4()), "text": "What is the limit?",
    })).status_code == 200
    renamed = await api.patch(f"/api/conversations/{chat}", json={"title": "  New title  "})
    assert renamed.status_code == 200 and renamed.json()["title"] == "New title"
    assert (await api.patch(f"/api/conversations/{chat}", json={"title": " "})).status_code == 422
    assert (await api.delete(f"/api/conversations/{chat}")).json() == {"deleted": True}
    assert (await api.get(f"/api/conversations/{chat}/messages")).status_code == 404
    async with db.sessions() as session:
        for model in (Conversation, ConversationMessage, AnswerAttempt, ConversationSummary):
            assert await session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.asyncio
async def test_retry_creates_new_attempt_for_same_message_and_replay_is_idempotent(m13_environment):
    api, db, app, _ = m13_environment
    kb = await new_kb(api)
    job = await finished(api, await upload(api, kb, data=b"The safe limit is 42 C.\n"))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    app.state.answer_enabled = True

    class FlakyQuery:
        calls = 0

        async def retrieve(self, *_args):
            self.calls += 1
            if self.calls == 1:
                raise QueryError("retrieval_failed")
            return [RetrievedChunk("chunk_a", "source_" + job["document_ids"][0].replace("-", ""),
                                   "The safe limit is 42 C.")]

    query = FlakyQuery()
    app.state.query_adapter = query
    app.state.answer_adapter = Answer()
    first = await api.post(f"/api/conversations/{chat}/messages", json={
        "client_message_id": str(uuid4()), "text": "What is the limit?",
    })
    assert first.status_code == 200 and first.json()["status"] == "failed"
    message_id = first.json()["message_id"]
    retry_id = str(uuid4())
    path = f"/api/conversations/{chat}/messages/{message_id}/retry"
    retried = await api.post(path, json={"attempt_id": retry_id})
    assert retried.status_code == 200 and retried.json()["status"] == "answered"
    assert retried.json()["message_id"] == message_id
    assert retried.json()["attempt_id"] == retry_id
    assert (await api.post(path, json={"attempt_id": retry_id})).json() == retried.json()
    assert query.calls == 2
    async with db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(ConversationMessage)) == 1
        assert await session.scalar(select(func.count()).select_from(AnswerAttempt)) == 2
    messages = (await api.get(f"/api/conversations/{chat}/messages")).json()["items"]
    assert messages[0]["status"] == "answered"


@pytest.mark.asyncio
async def test_answer_receives_only_current_revision_same_chat_window(m13_environment):
    api, db, app, _ = m13_environment
    kb = await new_kb(api)
    job = await finished(api, await upload(api, kb, data=b"The safe limit is 42 C.\n"))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    app.state.answer_enabled = True
    app.state.query_adapter = Query([RetrievedChunk(
        "chunk_a", "source_" + job["document_ids"][0].replace("-", ""),
        "The safe limit is 42 C.",
    )])
    answerer = ContextAnswer()
    app.state.answer_adapter = answerer
    for _ in range(2):
        response = await api.post(f"/api/conversations/{chat}/messages", json={
            "client_message_id": str(uuid4()), "text": "What is the limit?",
        })
        assert response.status_code == 200 and response.json()["status"] == "answered"
    assert answerer.contexts[0] == {"summary": "", "turns": []}
    assert answerer.contexts[1]["turns"] == [{
        "user": "What is the limit?", "assistant": "The safe limit is 42 C.",
    }]
    async with db.sessions() as session:
        base = await session.get(KnowledgeBase, kb)
        base.revision += 1
        await session.commit()
    third = await api.post(f"/api/conversations/{chat}/messages", json={
        "client_message_id": str(uuid4()), "text": "What is the limit?",
    })
    assert third.status_code == 200 and answerer.contexts[2] == {"summary": "", "turns": []}


@pytest.mark.asyncio
async def test_empty_retrieval_refuses_without_answer_model(m13_environment):
    api, _, app, _ = m13_environment
    kb = await new_kb(api)
    await finished(api, await upload(api, kb, data=b"The safe limit is 42 C.\n"))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    app.state.answer_enabled = True
    app.state.query_adapter = Query([])
    app.state.answer_adapter = Answer()
    result = await api.post(f"/api/conversations/{chat}/messages", json={
        "client_message_id": str(uuid4()), "text": "What is the limit?",
    })
    assert result.status_code == 200 and result.json()["status"] == "insufficient_evidence"
    assert result.json()["citations"] == []


@pytest.mark.asyncio
async def test_default_disabled_and_foreign_library_source_cannot_be_cited(m13_environment):
    api, _, app, _ = m13_environment
    first = await new_kb(api)
    second = await new_kb(api)
    await finished(api, await upload(api, first, data=b"First library limit is 42 C.\n"))
    other_job = await finished(api, await upload(api, second, data=b"Other limit is 58 C.\n"))
    chat = (await api.post("/api/conversations", json={"kb_id": first})).json()["id"]
    body = {"client_message_id": str(uuid4()), "text": "What is the limit?"}
    disabled = await api.post(f"/api/conversations/{chat}/messages", json=body)
    assert disabled.status_code == 503
    assert disabled.json()["detail"]["code"] == "answer_disabled"
    assert (await api.get(f"/api/conversations/{chat}/messages")).json()["items"] == []
    app.state.answer_enabled = True
    app.state.query_adapter = Query([
        RetrievedChunk("foreign", "source_" + other_job["document_ids"][0].replace("-", ""),
                       "Other limit is 58 C."),
    ])
    app.state.answer_adapter = Answer()
    result = await api.post(f"/api/conversations/{chat}/messages", json=body)
    assert result.status_code == 200
    assert result.json()["status"] == "failed"
    assert result.json()["citations"] == []


@pytest.mark.asyncio
async def test_normalized_engine_text_is_cited_as_verbatim_original(m13_environment):
    api, _, app, _ = m13_environment
    kb = await new_kb(api)
    job = await finished(api, await upload(api, kb, data=b"A &amp; B is valid.\n"))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    app.state.answer_enabled = True
    app.state.query_adapter = Query([
        RetrievedChunk("chunk_entity", "source_" + job["document_ids"][0].replace("-", ""),
                       "A & B is valid."),
    ])

    class OriginalAnswer:
        async def answer(self, _question, evidence):
            assert evidence == [{"id": "E1", "text": "A &amp; B is valid."}]
            return ('{"status":"answered","text":"A &amp; B is valid.",'
                    '"evidence_ids":["E1"]}')

    app.state.answer_adapter = OriginalAnswer()
    response = await api.post(f"/api/conversations/{chat}/messages", json={
        "client_message_id": str(uuid4()), "text": "What is the limit?",
    })
    assert response.status_code == 200
    assert response.json()["text"] == "A &amp; B is valid."
    assert response.json()["citations"][0]["excerpt"] == "A &amp; B is valid."
    assert response.json()["citations"][0]["locator"]["line_start"] == 1


@pytest.mark.asyncio
async def test_maintenance_during_retrieval_discards_late_answer(m13_environment):
    api, db, app, _ = m13_environment
    kb = await new_kb(api)
    await finished(api, await upload(api, kb, data=b"The safe limit is 42 C.\n"))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    app.state.answer_enabled = True

    class ChangingQuery(Query):
        async def retrieve(self, *_args):
            async with db.sessions() as session:
                row = await session.get(KnowledgeBase, kb)
                row.status, row.revision = "maintaining", row.revision + 1
                await session.commit()
            return []

    app.state.query_adapter = ChangingQuery([])
    app.state.answer_adapter = Answer()
    result = await api.post(f"/api/conversations/{chat}/messages", json={
        "client_message_id": str(uuid4()), "text": "What is the limit?",
    })
    assert result.status_code == 409
    history = (await api.get(f"/api/conversations/{chat}/messages")).json()["items"]
    assert history[0]["status"] == "interrupted" and not history[0]["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revision", "workspace"])
async def test_ready_library_change_also_discards_late_answer(m13_environment, change):
    api, db, app, _ = m13_environment
    kb = await new_kb(api)
    await finished(api, await upload(api, kb, data=b"The safe limit is 42 C.\n"))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    app.state.answer_enabled = True

    class ChangingQuery(Query):
        async def retrieve(self, *_args):
            async with db.sessions() as session:
                row = await session.get(KnowledgeBase, kb)
                if change == "revision":
                    row.revision += 1
                else:
                    row.active_workspace += "_synthetic_candidate"
                await session.commit()
            return []

    app.state.query_adapter = ChangingQuery([])
    app.state.answer_adapter = Answer()
    result = await api.post(f"/api/conversations/{chat}/messages", json={
        "client_message_id": str(uuid4()), "text": "What is the limit?",
    })
    assert result.status_code == 409
    assert result.json()["detail"]["code"] == "kb_changed"
    history = (await api.get(f"/api/conversations/{chat}/messages")).json()["items"]
    assert history[0]["status"] == "interrupted"
    assert not history[0]["citations"]


@pytest.mark.asyncio
async def test_cancelled_request_releases_active_attempt(m13_environment):
    api, _, app, _ = m13_environment
    kb = await new_kb(api)
    await finished(api, await upload(api, kb, data=b"The safe limit is 42 C.\n"))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    app.state.answer_enabled = True
    entered = asyncio.Event()

    class WaitingQuery(Query):
        async def retrieve(self, *_args):
            entered.set()
            await asyncio.Event().wait()

    app.state.query_adapter = WaitingQuery([])
    app.state.answer_adapter = Answer()
    call = asyncio.create_task(api.post(f"/api/conversations/{chat}/messages", json={
        "client_message_id": str(uuid4()), "text": "What is the limit?",
    }))
    await asyncio.wait_for(entered.wait(), 5)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    for _ in range(50):
        history = (await api.get(f"/api/conversations/{chat}/messages")).json()["items"]
        if history[0]["status"] == "interrupted":
            break
        await asyncio.sleep(0.02)
    assert history[0]["status"] == "interrupted"
    assert history[0]["error_code"] == "request_interrupted"
    app.state.query_adapter = Query([])
    retry = await api.post(f"/api/conversations/{chat}/messages", json={
        "client_message_id": str(uuid4()), "text": "What is the limit?",
    })
    assert retry.status_code == 200
    assert retry.json()["status"] == "insufficient_evidence"


@pytest.mark.asyncio
async def test_server_restart_marks_durable_running_attempt_interrupted(schema_database, tmp_path):
    database, settings = schema_database
    await migrate(database)
    root = tmp_path / "sources"
    async with application(database, settings, root, FakeAdapter()) as (api, _):
        kb = await new_kb(api)
        await finished(api, await upload(api, kb, data=b"Synthetic ready source.\n"))
        chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
        async with database.sessions() as session:
            base = await session.get(KnowledgeBase, kb)
            message = ConversationMessage(
                id=uuid4(), conversation_id=chat, client_message_id=uuid4(),
                content="Synthetic question?", mode="semantic",
            )
            session.add(message)
            await session.flush()
            session.add(AnswerAttempt(
                id=uuid4(), conversation_id=chat, message_id=message.id,
                status="running", citations=[], kb_revision=base.revision,
                workspace=base.active_workspace,
            ))
            await session.commit()
    async with application(database, settings, root, FakeAdapter()) as (api, _):
        history = (await api.get(f"/api/conversations/{chat}/messages")).json()["items"]
        assert len(history) == 1
        assert history[0]["status"] == "interrupted"
        assert history[0]["error_code"] == "server_restarted"
