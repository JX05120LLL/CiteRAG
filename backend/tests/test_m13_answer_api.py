"""M1-3 slice: real isolated business DB, synthetic indexed source, fake model."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from test_ingestion_lifecycle import FakeAdapter, application, finished, new_kb, upload
from test_postgres_local import migrate

from app.models import (
    AnswerAttempt,
    Conversation,
    ConversationMessage,
    ConversationSummary,
    KnowledgeBase,
    ToolCall,
)
from app.rag.answer_adapter import AnswerError
from app.rag.query_adapter import QueryError, RetrievedChunk
from app.services.answers import AnswerService

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
async def test_archive_keeps_history_but_blocks_new_questions_until_restored(m13_environment):
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
    question = {"client_message_id": str(uuid4()), "text": "What is the limit?"}
    assert (await api.post(f"/api/conversations/{chat}/messages", json=question)).status_code == 200
    archived = await api.patch(f"/api/conversations/{chat}/archive", json={"archived": True})
    assert archived.status_code == 200 and archived.json()["archived_at"]
    assert (await api.get("/api/conversations")).json()["items"] == []
    archived_list = await api.get("/api/conversations", params={"archived": True})
    assert [item["id"] for item in archived_list.json()["items"]] == [chat]
    assert len((await api.get(f"/api/conversations/{chat}/messages")).json()["items"]) == 1
    blocked = await api.post(f"/api/conversations/{chat}/messages", json={
        "client_message_id": str(uuid4()), "text": "What is the limit?",
    })
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "conversation_archived"
    restored = await api.patch(f"/api/conversations/{chat}/archive", json={"archived": False})
    assert restored.json()["archived_at"] is None
    assert [item["id"] for item in (await api.get("/api/conversations")).json()["items"]] == [chat]


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


@pytest.mark.asyncio
async def test_history_limits_messages_instead_of_retry_attempts(m13_environment):
    api, db, _, _ = m13_environment
    chat = (await api.post("/api/conversations", json={"kb_id": None})).json()["id"]
    start = datetime.now(UTC) - timedelta(hours=1)
    attempt_id_base = (uuid4().int >> 16) << 16
    async with db.sessions() as session:
        for index in range(51):
            message = ConversationMessage(
                id=uuid4(), conversation_id=chat, client_message_id=uuid4(),
                content=f"Synthetic question {index}", mode="auto",
                query_filter={"mode": "general"}, created_at=start + timedelta(minutes=index),
            )
            session.add(message)
            await session.flush()
            count = 60 if index == 50 else 1
            for attempt_index in range(count):
                session.add(AnswerAttempt(
                    id=UUID(int=attempt_id_base + index * 100 + attempt_index),
                    conversation_id=chat, message_id=message.id,
                    status="answered" if attempt_index == count - 1 else "failed",
                    text=f"Synthetic answer {index}/{attempt_index}", citations=[],
                    kb_revision=0, workspace="ordinary",
                    # Same timestamp deliberately exercises the stable ID tie-break.
                    created_at=message.created_at,
                ))
        await session.commit()
    history = (await api.get(f"/api/conversations/{chat}/messages")).json()["items"]
    assert len(history) == 50
    assert [item["question"] for item in history] == [
        f"Synthetic question {index}" for index in range(1, 51)
    ]
    assert history[-1]["status"] == "answered"
    assert history[-1]["text"] == "Synthetic answer 50/59"


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_status", ["failed", "interrupted", "partial"])
@pytest.mark.parametrize("tool_status", ["pending_approval", "running"])
async def test_retry_waits_for_active_manual_tool(
    m13_environment, previous_status, tool_status,
):
    api, db, app, _ = m13_environment
    chat = (await api.post("/api/conversations", json={"kb_id": None})).json()
    app.state.answer_enabled = True

    class Model:
        calls = 0

        async def general_answer(self, question, context):
            self.calls += 1
            if self.calls == 1:
                raise AnswerError("answer_unavailable")
            return '{"text":"Synthetic recovered answer"}'

    model = Model()
    app.state.answer_adapter = model
    first = (await api.post(f"/api/conversations/{chat['id']}/messages", json={
        "client_message_id": str(uuid4()), "text": "Synthetic question", "mode": "auto",
    })).json()
    assert first["status"] == "failed"
    tool_id = uuid4()
    async with db.sessions() as session:
        attempt = await session.get(AnswerAttempt, UUID(first["attempt_id"]))
        attempt.status = previous_status
        session.add(ToolCall(
            id=tool_id, owner_id=chat["owner_id"], conversation_id=chat["id"],
            request_id=uuid4(), kb_id=None, kb_revision=0, workspace="ordinary",
            tool_id="local.time", arguments={}, impact="Synthetic read-only operation",
            status=tool_status, effect="read_only",
        ))
        await session.commit()
    path = f"/api/conversations/{chat['id']}/messages/{first['message_id']}/retry"
    retry_id = str(uuid4())
    blocked = await api.post(path, json={"attempt_id": retry_id})
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "tool_in_progress"
    assert model.calls == 1
    async with db.sessions() as session:
        assert await session.get(AnswerAttempt, UUID(retry_id)) is None
        tool = await session.get(ToolCall, tool_id)
        tool.status = "rejected" if tool_status == "pending_approval" else "succeeded"
        await session.commit()
    retry = await api.post(path, json={"attempt_id": retry_id})
    assert retry.status_code == 200 and retry.json()["status"] == "answered"
    assert retry.json()["citations"] == [] and model.calls == 2
    assert (await api.post(path, json={"attempt_id": retry_id})).json() == retry.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["ask", "retry"])
async def test_cancellation_in_accepted_callback_releases_attempt(m13_environment, operation):
    api, db, app, _ = m13_environment
    chat = (await api.post("/api/conversations", json={"kb_id": None})).json()
    app.state.answer_enabled = True

    class Model:
        async def general_answer(self, question, context):
            raise AnswerError("answer_unavailable")

    app.state.answer_adapter = Model()
    prior = None
    if operation == "retry":
        prior = (await api.post(f"/api/conversations/{chat['id']}/messages", json={
            "client_message_id": str(uuid4()), "text": "Synthetic question", "mode": "auto",
        })).json()
        assert prior["status"] == "failed"
    entered = asyncio.Event()
    accepted_views = []

    async def accepted(view):
        accepted_views.append(view)
        entered.set()
        await asyncio.Event().wait()

    async with db.sessions() as session:
        service = AnswerService(session)
        if operation == "ask":
            work = service.ask(UUID(chat["owner_id"]), UUID(chat["id"]), uuid4(),
                               "Synthetic question", None, Model(), mode="auto",
                               on_accepted=accepted)
        else:
            work = service.retry(UUID(chat["owner_id"]), UUID(chat["id"]),
                                 UUID(prior["message_id"]), uuid4(), None, Model(),
                                 on_accepted=accepted)
        task = asyncio.create_task(work)
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    async with db.sessions() as session:
        saved = await session.get(AnswerAttempt, UUID(str(accepted_views[0]["attempt_id"])))
        assert saved.status == "interrupted"
        assert saved.error_code == "request_interrupted"
        assert saved.text is None and saved.citations == [] and saved.finished_at is not None
    new_question = await api.post(f"/api/conversations/{chat['id']}/messages", json={
        "client_message_id": str(uuid4()), "text": "Synthetic next question", "mode": "auto",
    })
    assert new_question.status_code == 200
    assert new_question.json()["status"] == "failed"


@pytest.mark.asyncio
async def test_answer_commit_and_new_admission_use_same_row_lock_order(m13_environment):
    api, db, _, _ = m13_environment
    kb = await new_kb(api)
    await finished(api, await upload(api, kb, data=b"The safe limit is 42 C.\n"))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()
    owner, chat_id = UUID(chat["owner_id"]), UUID(chat["id"])
    async with db.sessions() as preparation:
        service = AnswerService(preparation)
        view = await service.ask(owner, chat_id, uuid4(), "What is the limit?", None, None,
                                 defer_finish=True)
        message = await preparation.get(ConversationMessage, view["message_id"])
        attempt = await preparation.get(AnswerAttempt, view["attempt_id"])
        base = await preparation.get(KnowledgeBase, UUID(kb))
    held_chat, release_admission = asyncio.Event(), asyncio.Event()
    async with db.sessions() as admission, db.sessions() as completion:
        original_scalar = admission.scalar

        async def pause_after_chat_lock(statement, *args, **kwargs):
            result = await original_scalar(statement, *args, **kwargs)
            if (statement._for_update_arg is not None
                and statement.column_descriptions[0]["entity"] is Conversation):
                held_chat.set()
                await release_admission.wait()
            return result

        admission.scalar = pause_after_chat_lock

        async def admit():
            try:
                await AnswerService(admission)._owned_conversation(owner, chat_id, lock=True)
                await admission.commit()
            finally:
                await admission.rollback()

        contender = asyncio.create_task(admit())
        await asyncio.wait_for(held_chat.wait(), 5)
        pid = await completion.scalar(text("SELECT pg_backend_pid()"))
        finishing = asyncio.create_task(AnswerService(completion)._commit_result(
            owner, chat_id, base, message, attempt, "answered", "The safe limit is 42 C.", [],
        ))
        try:
            # Wait for an actual PostgreSQL row-lock wait before advancing admission.
            async with db.engine.connect() as observer:
                for _ in range(100):
                    waiting = await observer.scalar(text(
                        "SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"
                    ), {"pid": pid})
                    await observer.rollback()
                    if waiting == "Lock":
                        break
                    await asyncio.sleep(0.01)
                else:
                    pytest.fail("Synthetic completion did not reach the competing row lock")
            release_admission.set()
            _, result = await asyncio.wait_for(asyncio.gather(contender, finishing), 5)
            assert result["status"] == "answered"
        finally:
            release_admission.set()
            for task in (contender, finishing):
                if not task.done():
                    task.cancel()
            await asyncio.gather(contender, finishing, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat_cancel", [False, True])
async def test_commit_phase_cancellation_releases_transaction_before_cleanup(
    monkeypatch, repeat_cancel,
):
    """An offline commit barrier checks cleanup ordering and repeated cancellation."""
    events = []
    entered_commit, entered_rollback, release_cleanup = (
        asyncio.Event(), asyncio.Event(), asyncio.Event(),
    )
    session = MagicMock()
    session.commit = AsyncMock()

    async def rollback():
        entered_rollback.set()
        await release_cleanup.wait()
        events.append("rollback")

    session.rollback = AsyncMock(side_effect=rollback)
    cleanup = MagicMock()
    cleanup.__aenter__.return_value = cleanup
    cleanup.execute = AsyncMock(side_effect=lambda *_: events.append("interrupt"))
    cleanup.commit = AsyncMock()
    monkeypatch.setattr("app.services.answers.AsyncSession", lambda **_: cleanup)
    monkeypatch.setattr("app.services.answers.prepare_context", AsyncMock(return_value={}))
    service = AnswerService(session)
    service._resolve = AsyncMock(return_value=("answered", "Synthetic complete answer", []))

    async def committing(*_):
        entered_commit.set()
        await asyncio.Event().wait()

    service._commit_result = committing
    message = ConversationMessage(id=uuid4(), content="Synthetic question", mode="auto")
    attempt = AnswerAttempt(id=uuid4(), status="running", kb_revision=0, workspace="ordinary")
    task = asyncio.create_task(service._finish_attempt(
        uuid4(), uuid4(), None, message, attempt, None, None, "auto", None,
    ))
    try:
        await asyncio.wait_for(entered_commit.wait(), 2)
        task.cancel()
        await asyncio.wait_for(entered_rollback.wait(), 2)
        if repeat_cancel:
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done(), "Repeated cancellation abandoned the cleanup transaction"
        release_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert events == ["rollback", "interrupt"]
        cleanup.commit.assert_awaited_once()
    finally:
        release_cleanup.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancellation_while_final_commit_waits_for_row_lock(m13_environment):
    api, db, app, _ = m13_environment
    chat = (await api.post("/api/conversations", json={"kb_id": None})).json()
    app.state.answer_enabled = True

    class Model:
        async def general_answer(self, question, context):
            return '{"text":"Synthetic complete answer"}'

    accepted, resume, entering_commit = asyncio.Event(), asyncio.Event(), asyncio.Event()
    views, backend_pid = [], []

    async with db.sessions() as worker, db.sessions() as locker:
        service = AnswerService(worker)
        original_commit = service._commit_result

        async def committing(*args, **kwargs):
            backend_pid.append(await worker.scalar(text("SELECT pg_backend_pid()")))
            entering_commit.set()
            return await original_commit(*args, **kwargs)

        async def on_accepted(view):
            views.append(view)
            accepted.set()
            await resume.wait()

        service._commit_result = committing
        task = asyncio.create_task(service.ask(
            UUID(chat["owner_id"]), UUID(chat["id"]), uuid4(), "Synthetic question",
            None, Model(), mode="auto", on_accepted=on_accepted,
        ))
        try:
            await asyncio.wait_for(accepted.wait(), 5)
            await locker.get(Conversation, UUID(chat["id"]), with_for_update=True)
            resume.set()
            await asyncio.wait_for(entering_commit.wait(), 5)
            async with db.engine.connect() as observer:
                for _ in range(100):
                    waiting = await observer.scalar(text(
                        "SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"
                    ), {"pid": backend_pid[0]})
                    await observer.rollback()
                    if waiting == "Lock":
                        break
                    await asyncio.sleep(0.01)
                else:
                    pytest.fail("Synthetic completion did not wait for the held conversation lock")
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
        finally:
            resume.set()
            await locker.rollback()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    async with db.sessions() as session:
        saved = await session.get(AnswerAttempt, UUID(str(views[0]["attempt_id"])))
        assert saved.status == "interrupted" and saved.error_code == "request_interrupted"
        assert saved.text is None and saved.citations == [] and saved.finished_at is not None
    app.state.answer_adapter = Model()
    next_question = await api.post(f"/api/conversations/{chat['id']}/messages", json={
        "client_message_id": str(uuid4()), "text": "Synthetic next question", "mode": "auto",
    })
    assert next_question.status_code == 200 and next_question.json()["status"] == "answered"
