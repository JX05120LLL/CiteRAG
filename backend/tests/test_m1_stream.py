"""SSE events are ordered around durable input and checked final answers."""

# Pytest consumes imported fixtures by name; Ruff cannot see fixture injection.
# ruff: noqa: F401, F811

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from test_ingestion_lifecycle import finished, new_kb, upload
from test_m13_answer_api import Answer, Query, m13_environment, no_provider_access

from app.api.answers import router as answer_router
from app.api.dependencies import local_owner
from app.models import AnswerAttempt, ConversationMessage, KnowledgeBase
from app.rag.answer_adapter import AnswerError, checked_answer
from app.rag.query_adapter import RetrievedChunk
from app.rag.streamed_answer import ExtractiveDraft
from app.rag.task_constraints import make_task_contract
from app.services.answers import AnswerService
from app.services.errors import ServiceError

pytest_plugins = ["test_postgres_local"]
pytestmark = pytest.mark.postgres


async def disconnect_stream(app, path, body, *, after="accepted", generation_ready=None):
    """Send a real ASGI disconnect after one event, without buffering the SSE response."""
    requested = asyncio.Event()
    sent, first_request = [], True
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.0"},
        "method": "POST", "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "http_version": "1.1", "server": ("127.0.0.1", 5173),
        "client": ("127.0.0.1", 12345),
        "headers": [(b"host", b"127.0.0.1:5173"),
                    (b"origin", b"http://127.0.0.1:5173"),
                    (b"content-type", b"application/json")],
    }

    async def receive():
        nonlocal first_request
        if first_request:
            first_request = False
            return {"type": "http.request", "body": json.dumps(body).encode(),
                    "more_body": False}
        await requested.wait()
        if generation_ready is not None:
            await generation_ready.wait()
        return {"type": "http.disconnect"}

    async def send(event):
        sent.append(event)
        if event["type"] == "http.response.body" and (
            f"event: {after}\n".encode() in event.get("body", b"")
        ):
            requested.set()

    await asyncio.wait_for(app(scope, receive, send), 5)
    status = next(event["status"] for event in sent if event["type"] == "http.response.start")
    stream = b"".join(event.get("body", b"") for event in sent).decode()
    return status, events(SimpleNamespace(text=stream)) if status == 200 and stream else []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["ask", "retry"])
@pytest.mark.parametrize("with_preview", [False, True])
@pytest.mark.parametrize("late_result", [False, True])
async def test_stream_disconnect_cancels_generation_and_closes_session(
    monkeypatch, operation, with_preview, late_result,
):
    owner, chat, message, attempt = uuid4(), uuid4(), uuid4(), uuid4()
    running = {"message_id": str(message), "attempt_id": str(attempt),
               "status": "running", "text": "", "saved": False}
    generation_ready, cancelled, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cleanup_order = []

    @asynccontextmanager
    async def sessions():
        try:
            yield object()
        finally:
            cleanup_order.append("session_closed")
            closed.set()

    async def generate(*args, on_accepted, on_preview, **kwargs):
        assert args[:2] == (owner, chat)
        if operation == "retry":
            assert args[2:4] == (message, attempt)
        await on_accepted(running)
        if with_preview:
            await on_preview(attempt, "Synthetic verified prefix")
        generation_ready.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            cleanup_order.append("generation_cleanup_finished")
            if late_result:
                # Some providers finish while cancellation is propagating. Their
                # late result must never become a saved event on the closed stream.
                return {**running, "status": "answered", "text": "Late answer", "saved": True}
            raise

    service = SimpleNamespace(ask=generate, retry=generate)
    monkeypatch.setattr("app.api.answers.AnswerService", lambda *_args, **_kwargs: service)
    app = FastAPI()
    app.include_router(answer_router)
    app.dependency_overrides[local_owner] = lambda: owner
    app.state.database = SimpleNamespace(sessions=sessions)
    app.state.answer_enabled = True
    app.state.voice_runtime = SimpleNamespace(registry=SimpleNamespace(require_text=lambda _: None))
    app.state.rag_runtime = object()
    app.state.query_adapter = object()
    app.state.answer_adapter = object()
    app.state.image_store = app.state.image_observer = None
    path = (f"/api/conversations/{chat}/messages/stream" if operation == "ask" else
            f"/api/conversations/{chat}/messages/{message}/retry/stream")
    body = ({"client_message_id": str(uuid4()), "text": "Synthetic question"}
            if operation == "ask" else {"attempt_id": str(attempt)})
    status, received = await disconnect_stream(
        app, path, body, after="delta" if with_preview else "accepted",
        generation_ready=generation_ready,
    )
    assert status == 200
    assert cancelled.is_set(), "Disconnect left the generation coroutine running"
    assert closed.is_set()
    assert cleanup_order == ["generation_cleanup_finished", "session_closed"]
    assert [name for name, _ in received] == (["accepted", "delta"] if with_preview
                                            else ["accepted"])
    assert not any(name == "saved" for name, _ in received)


async def seed_retry(m13_environment, previous_status):
    api, db, app, _ = m13_environment
    kb = await new_kb(api)
    job = await finished(api, await upload(api, kb, data=b"The safe limit is 42 C.\n"))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()
    app.state.answer_enabled = True
    app.state.query_adapter = Query([RetrievedChunk(
        "chunk_a", "source_" + job["document_ids"][0].replace("-", ""),
        "The safe limit is 42 C.",
    )])
    async with db.sessions() as session:
        base = await session.get(KnowledgeBase, UUID(kb))
        message = ConversationMessage(
            id=uuid4(), conversation_id=UUID(chat["id"]), client_message_id=uuid4(),
            content="What is the limit?", mode="semantic",
        )
        session.add(message)
        await session.flush()
        previous = AnswerAttempt(
            id=uuid4(), conversation_id=UUID(chat["id"]), message_id=message.id,
            status=previous_status, text="The safe" if previous_status == "partial" else None,
            citations=[], kb_revision=base.revision, workspace=base.active_workspace,
        )
        session.add(previous)
        await session.commit()
    return chat, message, previous


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_status", ["failed", "interrupted", "partial"])
async def test_retry_stream_accepts_saved_attempt_then_returns_answer_and_replays(
    m13_environment, previous_status,
):
    api, db, app, _ = m13_environment
    chat, message, previous = await seed_retry(m13_environment, previous_status)
    attempt_id = uuid4()

    class DurableAnswer(Answer):
        async def answer(self, question, evidence):
            async with db.sessions() as session:
                running = await session.get(AnswerAttempt, attempt_id)
                assert running is not None and running.status == "running"
                assert running.message_id == message.id
            return await super().answer(question, evidence)

    app.state.answer_adapter = DurableAnswer()
    path = f"/api/conversations/{chat['id']}/messages/{message.id}/retry/stream"
    body = {"attempt_id": str(attempt_id)}
    response = await api.post(path, json=body)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    received = events(response)
    assert received[0][0] == "accepted" and not received[0][1]["saved"]
    assert received[0][1]["message_id"] == str(message.id)
    assert received[0][1]["attempt_id"] == str(attempt_id)
    assert received[0][1]["client_message_id"] == str(message.client_message_id)
    assert received[-1][0] == "saved" and received[-1][1]["status"] == "answered"
    assert received[-1][1]["attempt_id"] == str(attempt_id)
    assert received[-1][1]["citations"][0]["locator"]["line_start"] == 1
    async with db.sessions() as session:
        original = await session.get(AnswerAttempt, previous.id)
        final = await session.get(AnswerAttempt, attempt_id)
        assert original.status == previous_status
        assert final.status == "answered" and final.message_id == message.id
    replay = events(await api.post(path, json=body))
    assert replay[-1] == received[-1]
    assert not any(name == "accepted" for name, _ in replay)


@pytest.mark.asyncio
@pytest.mark.parametrize("with_preview", [False, True])
async def test_retry_stream_disconnect_persists_interruption_and_allows_retry(
    m13_environment, with_preview,
):
    api, db, app, _ = m13_environment
    chat, message, _ = await seed_retry(m13_environment, "failed")
    attempt_id = uuid4()
    generation_ready, cancelled = asyncio.Event(), asyncio.Event()

    class HangingAnswer(Answer):
        async def stream_with_context(self, question, evidence, context):
            try:
                if with_preview:
                    yield '{"status":"answered","text":"The safe limit'
                generation_ready.set()
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    app.state.answer_adapter = HangingAnswer()
    path = f"/api/conversations/{chat['id']}/messages/{message.id}/retry/stream"
    status, received = await disconnect_stream(
        app, path, {"attempt_id": str(attempt_id)},
        after="delta" if with_preview else "accepted", generation_ready=generation_ready,
    )
    assert status == 200 and cancelled.is_set()
    assert [name for name, _ in received] == (["accepted", "delta"] if with_preview
                                            else ["accepted"])
    async with db.sessions() as session:
        attempt = await session.get(AnswerAttempt, attempt_id)
        assert attempt.status == ("partial" if with_preview else "interrupted")
        assert attempt.text == ("The safe limit" if with_preview else None)
        assert attempt.citations == [] and attempt.error_code == "request_interrupted"
    app.state.answer_adapter = Answer()
    new_id = uuid4()
    resumed = events(await api.post(path, json={"attempt_id": str(new_id)}))
    assert resumed[0][0] == "accepted" and resumed[0][1]["attempt_id"] == str(new_id)
    assert resumed[-1][0] == "saved" and resumed[-1][1]["status"] == "answered"


@pytest.mark.asyncio
async def test_retry_stream_preserves_partial_preview_when_provider_fails(m13_environment):
    api, db, app, _ = m13_environment
    chat, message, _ = await seed_retry(m13_environment, "partial")
    attempt_id = uuid4()

    class FailingStream(Answer):
        async def stream_with_context(self, question, evidence, context):
            yield '{"status":"answered","text":"The safe limit'
            raise RuntimeError("synthetic stream failure")

    app.state.answer_adapter = FailingStream()
    response = await api.post(
        f"/api/conversations/{chat['id']}/messages/{message.id}/retry/stream",
        json={"attempt_id": str(attempt_id)},
    )
    assert response.status_code == 200
    received = events(response)
    assert [name for name, _ in received] == ["accepted", "delta", "saved"]
    assert received[1][1]["saved"] is False
    assert received[-1][1]["status"] == "partial"
    assert received[-1][1]["text"] == "The safe limit" and received[-1][1]["citations"] == []
    async with db.sessions() as session:
        stored = await session.get(AnswerAttempt, attempt_id)
        assert stored.status == "partial" and stored.text == "The safe limit"


@pytest.mark.asyncio
@pytest.mark.parametrize("supported", [True, False, "invalid"])
async def test_summary_stream_waits_for_support_check_and_preserves_real_citations(
    m13_environment, supported,
):
    api, _, app, _ = m13_environment
    kb = await new_kb(api)
    source = "Nimbus uses PostgreSQL. Nimbus queues work with Redis."
    job = await finished(api, await upload(api, kb, data=(source + "\n").encode()))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    app.state.answer_enabled = True
    requested = []

    class Retrieval:
        async def retrieve(self, _kb, _workspace, question, _sources):
            requested.append(question)
            return [RetrievedChunk("synthetic_chunk", "source_" +
                                   job["document_ids"][0].replace("-", ""), source)]

    class Summary:
        requires_support_verification = True
        verified = False

        async def route_with_context(self, question, candidates, context, documents):
            assert documents and documents[0]["status"] == "ready"
            if question == "How does it queue work?":
                assert context["turns"][0]["user"] == "Introduce Nimbus"
                return '{"mode":"semantic","query":"Nimbus queues work"}'
            return '{"mode":"semantic"}'

        async def stream_with_context(self, question, evidence, context):
            raw = json.dumps({"status": "answered", "text": "Nimbus combines PostgreSQL and Redis.",
                              "evidence_ids": ["E1"], "support": [
                                  {"evidence_id": "E1", "quote": source}]})
            for offset in range(0, len(raw), 8):
                yield raw[offset:offset+8]

        async def verify_answer(self, text, evidence):
            assert evidence[0]["text"] == source and "PostgreSQL" in text
            self.verified = True
            return json.dumps({"supported": supported})

    model = Summary()
    app.state.query_adapter, app.state.answer_adapter = Retrieval(), model
    received = events(await api.post(f"/api/conversations/{chat}/messages/stream", json={
        "client_message_id": str(uuid4()), "text": "Introduce Nimbus", "mode": "auto",
    }))
    result = received[-1][1]
    assert model.verified and received[0][0] == "accepted" and received[-1][0] == "saved"
    if supported is True:
        assert result["status"] == "answered" and result["citations"][0]["excerpt"] == source
        assert all(data["saved"] for name, data in received if name == "delta")
        followup = events(await api.post(f"/api/conversations/{chat}/messages/stream", json={
            "client_message_id": str(uuid4()), "text": "How does it queue work?", "mode": "auto",
        }))[-1][1]
        assert followup["status"] == "answered" and requested[-1] == "Nimbus queues work"
    else:
        expected = ("answer_unsupported_claims" if supported is False
                    else "answer_verification_unavailable")
        assert result["status"] == "failed" and result["error_code"] == expected
        assert result["text"] == "" and result["citations"] == []
        assert [name for name, _data in received] == ["accepted", "saved"]


@pytest.mark.asyncio
async def test_general_question_skips_retrieval_and_kb_miss_never_falls_back(m13_environment):
    api, _, app, _ = m13_environment
    kb = await new_kb(api)
    await finished(api, await upload(api, kb))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    app.state.answer_enabled = True
    calls = []

    class Routes:
        async def route_with_context(self, question, _candidates, context, _documents):
            if question == "Explain that concept further":
                assert context["turns"][-1]["answer_kind"] == "general"
                return '{"mode":"general","query":"Explain RAG further"}'
            return json.dumps({"mode": "general" if question == "What is RAG?" else "semantic"})

        async def general_answer(self, question, context):
            calls.append("general")
            # A routing rewrite is retrieval input, not a replacement user task.
            assert question in {"What is RAG?", "Explain that concept further"}
            assert context["_task_contract"] == make_task_contract(question)
            if question == "Explain that concept further":
                assert context["turns"][-1]["user"] == "What is RAG?"
                assert context["turns"][-1]["answer_kind"] == "general"
            return '{"text":"RAG uses retrieval to provide context for generation."}'

    class EmptyRetrieval:
        async def retrieve(self, *_args):
            calls.append("retrieval")
            return []

    app.state.answer_adapter, app.state.query_adapter = Routes(), EmptyRetrieval()
    async def ask(question):
        return events(await api.post(f"/api/conversations/{chat}/messages/stream", json={
            "client_message_id": str(uuid4()), "text": question, "mode": "auto",
        }))[-1][1]

    general = await ask("What is RAG?")
    assert general["status"] == "answered" and general["route"] == "general"
    assert general["citations"] == [] and calls == ["general"]
    followup = await ask("Explain that concept further")
    assert followup["route"] == "general" and followup["status"] == "answered"
    assert calls == ["general", "general"]
    missing = await ask("What is in my missing document?")
    assert missing["status"] == "insufficient_evidence" and missing["citations"] == []
    assert calls == ["general", "general", "retrieval"]


@pytest.mark.parametrize(("raw", "code"), [
    ('{"status":', "answer_format_invalid"),
    ('{"status":"answered","text":"safe","evidence_ids":["E9"]}', "answer_reference_invalid"),
    ('{"status":"answered","text":"invented answer","evidence_ids":["E1"]}',
     "answer_source_mismatch"),
])
def test_invalid_model_answers_have_distinct_safe_reasons(raw, code):
    with pytest.raises(AnswerError, match=f"^{code}$"):
        checked_answer(raw, {"E1": "The safe limit is 42 C."})


@pytest.mark.asyncio
@pytest.mark.parametrize(("greeting", "reply"), [
    ("你好！", "你好，今天想聊什么？"),
    ("你好你好", "你好呀，接着聊吧。"),
    ("谢谢", "不客气，需要时继续问我。"),
])
async def test_greeting_is_routed_to_model_without_retrieval(
    m13_environment, greeting, reply,
):
    api, _, app, _ = m13_environment
    kb = await new_kb(api)
    await finished(api, await upload(api, kb))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    app.state.answer_enabled = True

    class Model:
        calls = []

        async def route_with_context(self, question, candidates, context, documents):
            assert question == greeting and not candidates and documents
            self.calls.append("route")
            return '{"mode":"general"}'

        async def general_answer(self, question, context):
            assert question == greeting and context["turns"] == []
            self.calls.append("answer")
            return json.dumps({"text": reply}, ensure_ascii=False)

    class NoRetrieval:
        calls = 0

        async def retrieve(self, *args):
            self.calls += 1
            raise RuntimeError("Unexpected retrieval call")

    app.state.answer_adapter, app.state.query_adapter = Model(), NoRetrieval()
    response = await api.post(f"/api/conversations/{chat}/messages/stream", json={
        "client_message_id": str(uuid4()), "text": greeting, "mode": "auto",
    })
    received = events(response)
    assert received[0][0] == "accepted" and received[-1][0] == "saved"
    saved = received[-1][1]
    assert saved["status"] == "answered" and saved["error_code"] is None
    assert saved["route"] == "general" and saved["citations"] == []
    assert saved["text"] == reply
    assert app.state.answer_adapter.calls == ["route", "answer"]
    assert app.state.query_adapter.calls == 0


@pytest.mark.asyncio
async def test_auto_routes_use_each_current_library_and_recent_turns(m13_environment):
    api, _, app, _ = m13_environment
    topics = [
        ("设备知识库", "device.txt",
         "Device A-17 needs service every 30 days. Rated voltage is 24 V.",
         "A-17", "它的维护周期呢", "A-17 的维护周期", "这里的设备如何维护？"),
        ("咖啡知识库", "coffee.txt", "Coffee B-42 uses 18 g grounds and 36 g water at 93 C.",
         "B-42", "它的用量呢", "B-42 的用量", "这里的咖啡怎么冲？"),
    ]
    app.state.answer_enabled = True
    sources = {}
    chats = []
    for name, filename, source, code, followup, rewritten, dual in topics:
        created = await api.post("/api/knowledge-bases", json={
            "name": name, "client_request_id": str(uuid4()),
        })
        assert created.status_code == 201
        kb = created.json()["id"]
        job = await finished(api, await api.post(f"/api/knowledge-bases/{kb}/documents",
            data={"client_request_id": str(uuid4())},
            files=[("files", (filename, (source + "\n").encode(), "text/plain"))]))
        assert job["status"] == "succeeded"
        chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
        sources[UUID(kb)] = ("source_" + job["document_ids"][0].replace("-", ""), source)
        chats.append((kb, chat, name, filename, source, code, followup, rewritten, dual))

    class Routes:
        async def route_with_context(self, question, candidates, context, documents):
            assert any(context["knowledge_base"]["name"] == item[2]
                       and documents[0]["filename"] == item[3] for item in chats)
            assert context["knowledge_base"]["ready_document_count"] == 1
            if question in {"你好", "为什么天空是蓝色？"}:
                return '{"mode":"general"}'
            for _, _, _, _, _, code, followup, rewritten, dual in chats:
                if question == f"{code} 的参数是什么？":
                    return json.dumps({"mode": "literal", "phrase": code})
                if question == followup:
                    assert context["turns"][-1]["answer_kind"] == "knowledge"
                    return json.dumps({"mode": "semantic", "query": rewritten}, ensure_ascii=False)
                if question == dual:
                    return '{"mode":"semantic"}'
            if question == "那个是什么？":
                return '{"mode":"needs_clarification"}'
            return '{"mode":"semantic"}'

        async def general_answer(self, question, context):
            return json.dumps({"text": "合成普通回答：" + question}, ensure_ascii=False)

        async def answer_with_context(self, question, evidence, context):
            return json.dumps({"status": "answered", "text": evidence[0]["text"],
                               "evidence_ids": ["E1"]})

    class Retrieval:
        async def retrieve(self, kb, workspace, question, source_ids):
            if question == "库内不存在的事实":
                return []
            source_key, content = sources[kb]
            assert source_key in source_ids
            return [RetrievedChunk("synthetic_chunk", source_key, content)]

    app.state.answer_adapter, app.state.query_adapter = Routes(), Retrieval()

    async def ask(chat, question):
        response = await api.post(f"/api/conversations/{chat}/messages/stream", json={
            "client_message_id": str(uuid4()), "text": question, "mode": "auto",
        })
        assert response.status_code == 200
        return events(response)[-1][1]

    for kb_id, chat, _, _, source, code, followup, _, dual in chats:
        for question in ("你好", "为什么天空是蓝色？"):
            ordinary = await ask(chat, question)
            assert ordinary["status"] == "answered" and ordinary["route"] == "general"
            assert ordinary["citations"] == [] and ordinary["text"].startswith("合成普通回答")
        for question, route in (("总结当前库的要求", "semantic"),
                                (f"{code} 的参数是什么？", "literal"),
                                (followup, "semantic"), (dual, "semantic")):
            grounded = await ask(chat, question)
            assert grounded["status"] == "answered" and grounded["route"] == route
            assert grounded["text"] == source and grounded["citations"][0]["excerpt"] == source
        missing = await ask(chat, "库内不存在的事实")
        assert missing["status"] == "insufficient_evidence"
        assert missing["route"] == "semantic" and missing["citations"] == []
        fresh = (await api.post("/api/conversations", json={"kb_id": kb_id})).json()["id"]
        unclear = await ask(fresh, "那个是什么？")
        assert unclear["route"] == "needs_clarification" and unclear["citations"] == []


@pytest.mark.asyncio
async def test_failed_legacy_chat_retry_uses_model_without_changing_saved_route(m13_environment):
    api, db, app, _ = m13_environment
    kb = await new_kb(api)
    await finished(api, await upload(api, kb))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    app.state.answer_enabled = True

    class Model:
        route_calls = 0

        async def route_with_context(self, *_args):
            self.route_calls += 1
            return '{"mode":"general"}'

        async def general_answer(self, question, context):
            assert question == "你好" and not context["turns"]
            return '{"text":"你好，这次我会结合你的问题回答。"}'

    model = Model()
    app.state.answer_adapter = model
    original = events(await api.post(f"/api/conversations/{chat}/messages/stream", json={
        "client_message_id": str(uuid4()), "text": "你好", "mode": "auto",
    }))[-1][1]
    async with db.sessions() as session:
        message = await session.get(ConversationMessage, UUID(original["message_id"]))
        attempt = await session.get(AnswerAttempt, UUID(original["attempt_id"]))
        message.query_filter = {"mode": "chat"}  # Persisted route from older versions.
        attempt.status, attempt.text, attempt.error_code = "failed", None, "answer_unavailable"
        await session.commit()
    retried = await api.post(
        f"/api/conversations/{chat}/messages/{original['message_id']}/retry",
        json={"attempt_id": str(uuid4())},
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "answered" and retried.json()["route"] == "chat"
    assert retried.json()["text"] == "你好，这次我会结合你的问题回答。"
    assert retried.json()["citations"] == [] and model.route_calls == 1


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
    api, db, app, _ = m13_environment
    kb = await new_kb(api)
    await finished(api, await upload(api, kb, data=b"The safe limit is 42 C.\n"))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    app.state.answer_enabled = True
    app.state.query_adapter = Query([])
    class NoAnswer(Answer):
        async def answer(self, *_args):
            pytest.fail("Empty retrieval must not invoke answer generation")

    app.state.answer_adapter = NoAnswer()
    received = events(await api.post(f"/api/conversations/{chat}/messages/stream", json={
        "client_message_id": str(uuid4()), "text": "What is the limit?",
    }))
    assert [name for name, _ in received] == ["accepted", "saved"]
    assert received[-1][1]["status"] == "insufficient_evidence"
    result = received[-1][1]
    assert result["text"] == (
        "本次检索未找到可核对的原文证据，无法据此回答。请补充相关资料，"
        "或提供更具体的文件名、对象和编号后重试。")
    assert result["citations"] == [] and result["saved"] is True
    async with db.sessions() as session:
        stored = await session.get(AnswerAttempt, UUID(result["attempt_id"]))
        assert stored.status == "insufficient_evidence" and stored.text == result["text"]
        assert stored.citations == []


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
