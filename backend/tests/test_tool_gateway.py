"""Tool calls use the chat binding, persist outcomes, and never become citations."""

from uuid import uuid4

import pytest
from sqlalchemy import select, text
from test_postgres_local import (  # noqa: F401
    environment,
    migrate,
    schema_database,
    seed_knowledge_base,
)

from app.models import LocalProfile, ToolCall
from app.tools.gateway import ToolDefinition

pytest_plugins = ["test_postgres_local"]
pytestmark = pytest.mark.postgres


async def test_calculator_has_persistent_results_errors_and_no_knowledge_reads(environment):  # noqa: F811
    api, _ = environment
    chat = (await api.post("/api/conversations", json={"kb_id": None})).json()["id"]
    path = f"/api/conversations/{chat}/tools/calls"
    body = {"request_id": str(uuid4()), "tool_id": "local.calculate",
            "arguments": {"expression": " 0.1 + 0.2 "}}
    first = await api.post(path, json=body)
    assert first.status_code == 201, first.text
    assert first.json()["result"]["value"] == "0.3"
    assert first.json()["kb_id"] is None and first.json()["source_type"] == "tool"
    assert (await api.post(path, json=body)).json() == first.json()
    bad = await api.post(path, json={**body, "request_id": str(uuid4()),
                                   "arguments": {"expression": "open('private')"}})
    assert bad.status_code == 422
    failure = (await api.post(path, json={**body, "request_id": str(uuid4()),
                                         "arguments": {"expression": "1 / 0"}})).json()
    assert failure["status"] == "failed"
    assert failure["error_code"] == "calculation_zero_division"
    assert failure["result"] is None
    assert len((await api.get(path)).json()["items"]) == 2
    assert (await api.get(f"/api/conversations/{chat}/messages")).json()["items"] == []


async def test_actual_mcp_protocol_requires_approval_and_replays_saved_result(environment):  # noqa: F811
    import json

    from test_agent_mcp import synthetic_server

    from app.tools.local_mcp import create_server, reviewed_registry
    from app.tools.mcp import MCPAdapter, ReviewedMCPTool

    api, _ = environment
    chat = (await api.post("/api/conversations", json={"kb_id": None})).json()["id"]
    path = f"/api/conversations/{chat}/tools/calls"
    async with synthetic_server(create_server()) as url:
        entry = (await reviewed_registry(create_server(), url))[0]
        spec = MCPAdapter(url, entry["version"]).definition(ReviewedMCPTool(**entry["tools"][0]))
        api._transport.app.state.tool_registry[spec.id] = spec
        body = {"request_id": str(uuid4()), "tool_id": spec.id,
                "arguments": {"expression": "0.1 + 0.2"}}
        invalid = await api.post(path, json={**body, "arguments": {"expression": 123}})
        assert invalid.status_code == 422
        pending = (await api.post(path, json=body)).json()
        assert pending["status"] == "pending_approval" and pending["result"] is None
        approved = await api.post(f"{path}/{pending['id']}/decision", json={"approve": True})
        assert approved.status_code == 200, approved.text
        result = approved.json()
        assert result["status"] == "succeeded" and result["effect"] == "read_only"
        assert json.loads(result["result"]["text"])["value"] == "0.3"
        assert (await api.post(path, json=body)).json() == result
        assert (await api.post(f"{path}/{pending['id']}/decision",
                               json={"approve": True})).status_code == 409


async def test_ordinary_chat_catalog_call_idempotency_and_scope(environment):  # noqa: F811
    api, database = environment
    kb, _ = await seed_knowledge_base(database, "Synthetic", "ready")
    ordinary = (await api.post("/api/conversations", json={"kb_id": None})).json()["id"]
    path = f"/api/conversations/{ordinary}/tools"
    catalog = (await api.get(path)).json()["items"]
    assert [item["id"] for item in catalog] == ["local.time", "local.calculate"]
    assert (await api.post(f"{path}/calls", json={
        "request_id": str(uuid4()), "tool_id": "kb.documents", "arguments": {},
    })).status_code == 403
    request_id = str(uuid4())
    body = {"request_id": request_id, "tool_id": "local.time", "arguments": {}}
    first = await api.post(f"{path}/calls", json=body)
    assert first.status_code == 201, first.json()
    assert first.json()["status"] == "succeeded"
    assert first.json()["result"]["time"]
    assert first.json()["kb_id"] is None
    assert (await api.post(f"{path}/calls", json=body)).json() == first.json()
    conflict = await api.post(f"{path}/calls", json={**body, "tool_id": "kb.documents"})
    assert conflict.status_code == 409
    assert len((await api.get(f"{path}/calls")).json()["items"]) == 1
    assert (await api.get(f"/api/conversations/{uuid4()}/tools")).status_code == 404
    knowledge = (await api.post("/api/conversations", json={"kb_id": str(kb)})).json()["id"]
    knowledge_catalog = (await api.get(f"/api/conversations/{knowledge}/tools")).json()
    assert [item["id"] for item in knowledge_catalog["items"]] == [
        "local.time", "kb.documents", "local.calculate",
    ]


async def test_kb_tool_obeys_binding_and_maintenance(environment):  # noqa: F811
    api, database = environment
    kb, _ = await seed_knowledge_base(database, "Synthetic", "ready")
    chat = (await api.post("/api/conversations", json={"kb_id": str(kb)})).json()["id"]
    path = f"/api/conversations/{chat}/tools"
    result = await api.post(f"{path}/calls", json={
        "request_id": str(uuid4()), "tool_id": "kb.documents", "arguments": {},
    })
    assert result.status_code == 201, result.json()
    assert result.json()["status"] == "succeeded"
    assert result.json()["kb_id"] == str(kb)
    assert result.json()["result"] == {"items": []}
    async with database.engine.begin() as connection:
        await connection.execute(
            text("UPDATE knowledge_bases SET status='maintaining' WHERE id=:id"), {"id": kb})
    denied = await api.post(f"{path}/calls", json={
        "request_id": str(uuid4()), "tool_id": "kb.documents", "arguments": {},
    })
    assert denied.status_code == 409
    assert denied.json()["detail"]["code"] == "kb_not_ready"


async def test_approval_is_paused_and_requires_explicit_resume(environment):  # noqa: F811
    api, database = environment
    calls = []

    async def synthetic_operation(_session, _context, arguments):
        calls.append(arguments)
        return {"text": "synthetic completed"}

    api._transport.app.state.tool_registry["test.sensitive"] = ToolDefinition(
        id="test.sensitive", title="合成审批操作", scope="any", approval_required=True,
        impact="合成测试操作；不访问外部服务",
        validate=lambda args: args if args == {"value": 1} else None,
        run=synthetic_operation,
    )
    chat = (await api.post("/api/conversations", json={"kb_id": None})).json()["id"]
    path = f"/api/conversations/{chat}/tools/calls"
    pending = await api.post(path, json={
        "request_id": str(uuid4()), "tool_id": "test.sensitive", "arguments": {"value": 1},
    })
    assert pending.status_code == 201 and pending.json()["status"] == "pending_approval"
    assert calls == []
    assert pending.json()["arguments"] == {"value": 1}
    approved = await api.post(f"{path}/{pending.json()['id']}/decision", json={"approve": True})
    assert approved.status_code == 200 and approved.json()["status"] == "succeeded"
    assert calls == [{"value": 1}]
    assert (await api.post(f"{path}/{pending.json()['id']}/decision", json={
        "approve": True,
    })).status_code == 409
    rejected = (await api.post(path, json={
        "request_id": str(uuid4()), "tool_id": "test.sensitive", "arguments": {"value": 1},
    })).json()
    refusal = await api.post(f"{path}/{rejected['id']}/decision", json={"approve": False})
    assert refusal.json()["status"] == "rejected" and calls == [{"value": 1}]
    async with database.sessions() as session:
        assert await session.scalar(select(LocalProfile.id)) is not None


async def test_tool_failure_is_sanitized_and_active_call_blocks_chat_delete(environment):  # noqa: F811
    api, _ = environment

    async def broken(_session, _context, _arguments):
        raise ValueError("synthetic secret that must not cross API")

    api._transport.app.state.tool_registry["test.broken"] = ToolDefinition(
        id="test.broken", title="合成失败", scope="any", approval_required=True,
        impact="合成失败验证", validate=lambda args: {} if args == {} else None, run=broken,
    )
    chat = (await api.post("/api/conversations", json={"kb_id": None})).json()["id"]
    path = f"/api/conversations/{chat}/tools/calls"
    pending = (await api.post(path, json={
        "request_id": str(uuid4()), "tool_id": "test.broken", "arguments": {},
    })).json()
    assert (await api.delete(f"/api/conversations/{chat}")).status_code == 409
    completed = await api.post(f"{path}/{pending['id']}/decision", json={"approve": True})
    assert completed.status_code == 200 and completed.json()["status"] == "failed"
    assert completed.json()["error_code"] == "tool_execution_failed"
    assert "synthetic secret" not in completed.text
    assert (await api.delete(f"/api/conversations/{chat}")).json() == {"deleted": True}


async def test_expired_chat_clears_pending_approval(environment):  # noqa: F811
    from datetime import UTC, datetime, timedelta

    from app.services.conversation_retention import sweep_expired_conversations

    api, database = environment

    async def unused(_session, _context, _arguments):
        raise AssertionError("unapproved tool must not run")

    api._transport.app.state.tool_registry["test.pending"] = ToolDefinition(
        id="test.pending", title="合成待审批", scope="knowledge", approval_required=True,
        impact="合成审批验证", validate=lambda args: {} if args == {} else None, run=unused,
    )
    kb, _ = await seed_knowledge_base(database, "Synthetic", "ready")
    chat = (await api.post("/api/conversations", json={"kb_id": str(kb)})).json()["id"]
    pending = await api.post(f"/api/conversations/{chat}/tools/calls", json={
        "request_id": str(uuid4()), "tool_id": "test.pending", "arguments": {},
    })
    assert pending.json()["status"] == "pending_approval"
    async with database.engine.begin() as connection:
        await connection.execute(text("UPDATE conversations SET created_at=:old WHERE id=:id"),
                                 {"old": datetime.now(UTC) - timedelta(days=181), "id": chat})
    assert await sweep_expired_conversations(database) == 1
    assert (await api.get(f"/api/conversations/{chat}/tools/calls")).status_code == 404


async def test_tool_migration_preserves_old_chat_and_refuses_data_loss(schema_database):  # noqa: F811
    from alembic import command
    from alembic.config import Config

    database, _ = schema_database
    await migrate(database, "0010_conversation_modes_memory")
    chat = uuid4()
    async with database.engine.begin() as connection:
        owner = await connection.scalar(text("SELECT id FROM local_profiles"))
        await connection.execute(text(
            "INSERT INTO conversations(id, local_owner_id, kb_id, title) "
            "VALUES (:id, :owner, NULL, 'synthetic old chat')"
        ), {"id": chat, "owner": owner})
    await migrate(database)
    async with database.engine.connect() as connection:
        assert await connection.scalar(text("SELECT title FROM conversations WHERE id=:id"),
                                       {"id": chat}) == "synthetic old chat"
        assert await connection.scalar(text("SELECT count(*) FROM tool_calls")) == 0

    async def downgrade():
        async with database.engine.begin() as connection:
            def run(sync_connection):
                config = Config("alembic.ini")
                config.attributes["connection"] = sync_connection
                command.downgrade(config, "0010_conversation_modes_memory")
            await connection.run_sync(run)

    await downgrade()
    await migrate(database)
    async with database.sessions() as session:
        session.add(ToolCall(owner_id=owner, conversation_id=chat, request_id=uuid4(),
                             kb_id=None, kb_revision=0, workspace="ordinary",
                             tool_id="local.time", arguments={}, impact="synthetic read",
                             status="succeeded", result={"time": "synthetic"}))
        await session.commit()
    with pytest.raises(RuntimeError, match="Export or remove tool call records"):
        await downgrade()
    async with database.engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM tool_calls")) == 1


async def test_startup_marks_interrupted_tool_without_replaying(schema_database):  # noqa: F811
    from app.main import create_app

    database, settings = schema_database
    await migrate(database)
    chat = uuid4()
    async with database.engine.begin() as connection:
        owner = await connection.scalar(text("SELECT id FROM local_profiles"))
        await connection.execute(text(
            "INSERT INTO conversations(id, local_owner_id, kb_id, title) "
            "VALUES (:id, :owner, NULL, 'synthetic restart')"
        ), {"id": chat, "owner": owner})
    async with database.sessions() as session:
        session.add(ToolCall(owner_id=owner, conversation_id=chat, request_id=uuid4(),
                             kb_id=None, kb_revision=0, workspace="ordinary",
                             tool_id="local.time", arguments={}, impact="synthetic read",
                             status="running"))
        await session.commit()
    app = create_app(settings, database=database)
    async with app.router.lifespan_context(app):
        async with database.sessions() as session:
            call = await session.scalar(select(ToolCall))
            assert call.status == "interrupted" and call.error_code == "server_restarted"
            assert call.result is None
