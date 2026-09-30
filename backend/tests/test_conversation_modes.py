"""Isolated business DB regressions for fixed chat scope and source-linked memory."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from test_ingestion_lifecycle import finished, new_kb, upload
from test_m13_answer_api import (  # noqa: F401
    ContextAnswer,
    Query,
    m13_environment,
    no_provider_access,
)
from test_postgres_local import migrate, seed_knowledge_base

from app.models import KnowledgeBase, LocalProfile
from app.rag.answer_adapter import AnswerError
from app.rag.query_adapter import RetrievedChunk
from app.services.conversation_retention import sweep_expired_conversations

pytest_plugins = ["test_postgres_local"]
pytestmark = pytest.mark.postgres


class GeneralOnly:
    def __init__(self):
        self.contexts = []

    async def route_with_context(self, *_args):
        raise AssertionError("ordinary chat must not call the knowledge router")

    async def general_answer(self, question, context):
        self.contexts.append(context)
        return json.dumps({"text": f"普通回答：{question}"}, ensure_ascii=False)


class NoRetrieval:
    async def retrieve(self, *_args):
        raise AssertionError("ordinary chat must not call LightRAG")


@pytest.mark.asyncio
async def test_ordinary_chat_never_routes_or_retrieves_and_keeps_history(m13_environment):  # noqa: F811
    api, _, app, _ = m13_environment
    chat_response = await api.post("/api/conversations", json={"kb_id": None})
    assert chat_response.status_code == 201
    chat = chat_response.json()
    assert chat["kb_id"] is None
    assert chat["id"] in [item["id"] for item in (await api.get(
        "/api/conversations")).json()["items"]]
    app.state.answer_enabled = True
    app.state.query_adapter = NoRetrieval()
    answerer = GeneralOnly()
    app.state.answer_adapter = answerer
    first = await api.post(f"/api/conversations/{chat['id']}/messages", json={
        "client_message_id": str(uuid4()), "text": "请查询未选择的知识库", "mode": "auto",
    })
    assert first.status_code == 200, first.json()
    assert first.json()["route"] == "general" and first.json()["citations"] == []
    second = await api.post(f"/api/conversations/{chat['id']}/messages", json={
        "client_message_id": str(uuid4()), "text": "继续说说", "mode": "auto",
    })
    assert second.status_code == 200, second.json()
    assert answerer.contexts[1]["turns"][0]["user"] == "请查询未选择的知识库"
    assert len((await api.get(f"/api/conversations/{chat['id']}/messages")).json()["items"]) == 2
    assert (await api.post(f"/api/conversations/{chat['id']}/messages", json={
        "client_message_id": str(uuid4()), "text": "exact", "mode": "exact",
        "exact": {"doc_code": "A"},
    })).status_code == 422


@pytest.mark.asyncio
async def test_ordinary_failed_answer_retry_keeps_no_kb_boundary(m13_environment):  # noqa: F811
    api, _, app, _ = m13_environment
    chat = (await api.post("/api/conversations", json={"kb_id": None})).json()["id"]
    app.state.answer_enabled = True
    app.state.query_adapter = NoRetrieval()

    class FlakyGeneral(GeneralOnly):
        calls = 0

        async def general_answer(self, question, context):
            self.calls += 1
            if self.calls == 1:
                raise AnswerError("answer_unavailable")
            return await super().general_answer(question, context)

    app.state.answer_adapter = FlakyGeneral()
    first = await api.post(f"/api/conversations/{chat}/messages", json={
        "client_message_id": str(uuid4()), "text": "没有绑定库的提问", "mode": "auto",
    })
    assert first.status_code == 200 and first.json()["status"] == "failed"
    retry_id = str(uuid4())
    path = f"/api/conversations/{chat}/messages/{first.json()['message_id']}/retry"
    retry = await api.post(path, json={"attempt_id": retry_id})
    assert retry.status_code == 200 and retry.json()["status"] == "answered"
    assert retry.json()["route"] == "general" and retry.json()["citations"] == []
    assert (await api.post(path, json={"attempt_id": retry_id})).json() == retry.json()


@pytest.mark.asyncio
async def test_memory_is_scoped_to_current_kb_revision_and_source(m13_environment):  # noqa: F811
    api, db, app, _ = m13_environment
    kb = await new_kb(api)
    job = await finished(api, await upload(api, kb, data=b"The safe limit is 42 C.\n"))
    other = await new_kb(api)
    await finished(api, await upload(api, other, data=b"Unrelated knowledge.\n"))
    source_chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    target_chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    app.state.answer_enabled = True
    app.state.query_adapter = Query([RetrievedChunk(
        "chunk_a", "source_" + job["document_ids"][0].replace("-", ""),
        "The safe limit is 42 C.",
    )])
    answerer = ContextAnswer()
    app.state.answer_adapter = answerer
    source = (await api.post(f"/api/conversations/{source_chat}/messages", json={
        "client_message_id": str(uuid4()), "text": "What is the limit?",
    })).json()
    item = await api.post(f"/api/knowledge-bases/{kb}/memories", json={
        "source_message_id": source["message_id"], "kind": "preference",
        "content": "答复时请先说明限制条件。",
    })
    assert item.status_code == 201, item.json()
    memory = item.json()
    assert memory["valid"] and memory["source_conversation_id"] == source_chat
    assert (await api.post(f"/api/knowledge-bases/{other}/memories", json={
        "source_message_id": source["message_id"], "kind": "background",
        "content": "跨库记录",
    })).status_code == 404
    target = await api.post(f"/api/conversations/{target_chat}/messages", json={
        "client_message_id": str(uuid4()), "text": "What is the limit?",
    })
    assert target.status_code == 200, target.json()
    assert answerer.contexts[-1]["shared_memory"] == [{
        "kind": "preference", "content": "答复时请先说明限制条件。",
    }]
    async with db.sessions() as session:
        model = await session.get(KnowledgeBase, kb)
        model.revision += 1
        await session.commit()
    items = (await api.get(f"/api/knowledge-bases/{kb}/memories")).json()["items"]
    assert len(items) == 1 and not items[0]["valid"]
    stale = await api.post(f"/api/knowledge-bases/{kb}/memories", json={
        "source_message_id": source["message_id"], "kind": "preference",
        "content": "旧修订偏好",
    })
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "memory_source_stale"
    assert (await api.delete(f"/api/knowledge-bases/{kb}/memories/{memory['id']}")).json() == {
        "deleted": True,
    }
    assert (await api.get(f"/api/knowledge-bases/{kb}/memories")).json()["items"] == []


@pytest.mark.asyncio
async def test_deleting_source_chat_removes_shared_memory(m13_environment):  # noqa: F811
    api, _, app, _ = m13_environment
    kb = await new_kb(api)
    job = await finished(api, await upload(api, kb, data=b"The safe limit is 42 C.\n"))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    app.state.answer_enabled = True
    app.state.query_adapter = Query([RetrievedChunk(
        "chunk_a", "source_" + job["document_ids"][0].replace("-", ""),
        "The safe limit is 42 C.",
    )])
    app.state.answer_adapter = ContextAnswer()
    message = (await api.post(f"/api/conversations/{chat}/messages", json={
        "client_message_id": str(uuid4()), "text": "What is the limit?",
    })).json()["message_id"]
    assert (await api.post(f"/api/knowledge-bases/{kb}/memories", json={
        "source_message_id": message, "kind": "background", "content": "持续讨论限制条件",
    })).status_code == 201
    assert (await api.delete(f"/api/conversations/{chat}")).status_code == 200
    assert (await api.get(f"/api/knowledge-bases/{kb}/memories")).json()["items"] == []


@pytest.mark.asyncio
async def test_ordinary_chat_retained_while_old_knowledge_chat_expires(m13_environment):  # noqa: F811
    api, db, _, _ = m13_environment
    kb = await new_kb(api)
    await finished(api, await upload(api, kb, data=b"Synthetic source.\n"))
    ordinary = (await api.post("/api/conversations", json={"kb_id": None})).json()["id"]
    knowledge = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    old = datetime.now(UTC) - timedelta(days=181)
    async with db.engine.begin() as connection:
        await connection.execute(text(
            "UPDATE conversations SET created_at=:old WHERE id IN (:ordinary, :knowledge)"
        ), {"old": old, "ordinary": ordinary, "knowledge": knowledge})
    assert (await api.get(f"/api/conversations/{ordinary}")).status_code == 200
    assert (await api.get(f"/api/conversations/{knowledge}")).status_code == 404
    assert await sweep_expired_conversations(db) == 1
    assert (await api.get(f"/api/conversations/{ordinary}")).status_code == 200


@pytest.mark.asyncio
async def test_ordinary_voice_binding_and_image_question_use_general_path(m13_environment):  # noqa: F811
    from test_m3_images_api import Observer, synthetic_png

    api, db, app, _ = m13_environment
    chat = (await api.post("/api/conversations", json={"kb_id": None})).json()["id"]
    async with db.sessions() as session:
        from uuid import UUID

        owner = await session.scalar(select(LocalProfile.id))
        binding = await app.state.voice_runtime.binding(session, owner, UUID(chat))
    assert binding.kb is None and binding.workspace == "ordinary" and binding.revision == 0
    app.state.answer_enabled = True
    app.state.query_adapter = NoRetrieval()
    app.state.answer_adapter = GeneralOnly()
    observer = Observer()
    app.state.image_observer = observer
    image = await api.post(f"/api/conversations/{chat}/attachments", files={
        "file": ("synthetic.png", synthetic_png(), "image/png"),
    })
    assert image.status_code == 201, image.json()
    answer = await api.post(f"/api/conversations/{chat}/messages", json={
        "client_message_id": str(uuid4()), "text": "图中是什么？", "mode": "auto",
        "image_ids": [image.json()["id"]],
    })
    assert answer.status_code == 200, answer.json()
    assert answer.json()["route"] == "general" and answer.json()["citations"] == []
    assert observer.calls == 1


@pytest.mark.asyncio
async def test_image_message_cannot_become_shared_memory_source(m13_environment):  # noqa: F811
    from test_m3_images_api import Observer, synthetic_png

    api, _, app, _ = m13_environment
    kb = await new_kb(api)
    await finished(api, await upload(api, kb, data=b"Synthetic source.\n"))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    app.state.answer_enabled = True
    app.state.query_adapter = Query([])
    app.state.answer_adapter = ContextAnswer()
    app.state.image_observer = Observer()
    image = await api.post(f"/api/conversations/{chat}/attachments", files={
        "file": ("synthetic.png", synthetic_png(), "image/png"),
    })
    assert image.status_code == 201, image.json()
    answer = await api.post(f"/api/conversations/{chat}/messages", json={
        "client_message_id": str(uuid4()), "text": "What is shown?", "mode": "auto",
        "image_ids": [image.json()["id"]],
    })
    assert answer.status_code == 200, answer.json()
    memory = await api.post(f"/api/knowledge-bases/{kb}/memories", json={
        "source_message_id": answer.json()["message_id"], "kind": "background",
        "content": "Synthetic image observation",
    })
    assert memory.status_code == 422 and memory.json()["detail"]["code"] == "memory_image_source"


@pytest.mark.asyncio
async def test_migration_preserves_old_chat_and_downgrade_refuses_new_data(schema_database):
    database, _ = schema_database
    await migrate(database, "0009_conversation_archive")
    kb, owner = await seed_knowledge_base(database, "Synthetic legacy", "ready")
    old_chat = uuid4()
    async with database.engine.begin() as connection:
        await connection.execute(text(
            "INSERT INTO conversations(id, kb_id, local_owner_id, title) "
            "VALUES (:id, :kb, :owner, 'legacy')"
        ), {"id": old_chat, "kb": kb, "owner": owner})
    await migrate(database)
    async with database.engine.connect() as connection:
        assert await connection.scalar(text(
            "SELECT kb_id FROM conversations WHERE id=:id"
        ), {"id": old_chat}) == kb

    async def downgrade():
        async with database.engine.begin() as connection:
            def run(sync_connection):
                config = Config("alembic.ini")
                config.attributes["connection"] = sync_connection
                command.downgrade(config, "0009_conversation_archive")
            await connection.run_sync(run)

    await downgrade()
    await migrate(database)
    source_message = uuid4()
    async with database.engine.begin() as connection:
        await connection.execute(text(
            "INSERT INTO conversation_messages (id, conversation_id, client_message_id, "
            "content, mode) VALUES (:id, :chat, :request, 'synthetic source', 'auto')"
        ), {"id": source_message, "chat": old_chat, "request": uuid4()})
        await connection.execute(text(
            "INSERT INTO knowledge_memories (id, owner_id, kb_id, source_conversation_id, "
            "source_message_id, kind, content, kb_revision, workspace) "
            "SELECT :id, :owner, :kb, :chat, :message, 'preference', 'brief', revision, "
            "active_workspace FROM knowledge_bases WHERE id=:kb"
        ), {"id": uuid4(), "owner": owner, "kb": kb, "chat": old_chat,
            "message": source_message})
    with pytest.raises(RuntimeError, match="Delete or export ordinary chats"):
        await downgrade()
    async with database.engine.begin() as connection:
        await connection.execute(text("DELETE FROM knowledge_memories"))
        await connection.execute(text(
            "INSERT INTO conversations(id, kb_id, local_owner_id, title) "
            "VALUES (:id, NULL, :owner, 'ordinary')"
        ), {"id": uuid4(), "owner": owner})
    with pytest.raises(RuntimeError, match="Delete or export ordinary chats"):
        await downgrade()
