"""New schema and checkpoint tests only on the explicit disposable PostgreSQL URL."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from test_postgres_local import migrate, schema_database  # noqa: F401

pytest_plugins = ["test_postgres_local"]
pytestmark = pytest.mark.postgres


async def downgrade(database):
    from alembic import command
    from alembic.config import Config

    async with database.engine.begin() as connection:

        def run(sync):
            config = Config("alembic.ini")
            config.attributes["connection"] = sync
            command.downgrade(config, "0011_tool_gateway")

        await connection.run_sync(run)


async def test_agent_migration_preserves_legacy_and_waiting_is_active(schema_database):  # noqa: F811
    database, _ = schema_database
    await migrate(database, "0011_tool_gateway")
    chat, message, attempt = uuid4(), uuid4(), uuid4()
    async with database.engine.begin() as connection:
        owner = await connection.scalar(text("SELECT id FROM local_profiles"))
        await connection.execute(
            text(
                "INSERT INTO conversations(id,local_owner_id,title) "
                "VALUES (:id,:owner,'synthetic legacy')"
            ),
            {"id": chat, "owner": owner},
        )
        await connection.execute(
            text(
                "INSERT INTO conversation_messages"
                "(id,conversation_id,client_message_id,content,mode) "
                "VALUES (:id,:chat,:request,'synthetic original','auto')"
            ),
            {"id": message, "chat": chat, "request": uuid4()},
        )
        await connection.execute(
            text(
                "INSERT INTO answer_attempts"
                "(id,conversation_id,message_id,status,kb_revision,workspace,citations) "
                "VALUES (:id,:chat,:message,'answered',0,'ordinary','[]')"
            ),
            {"id": attempt, "chat": chat, "message": message},
        )
    await migrate(database)
    async with database.engine.begin() as connection:
        assert await connection.scalar(text("SELECT to_regclass('agent_runs')")) is not None
        assert (
            await connection.scalar(
                text("SELECT content FROM conversation_messages WHERE id=:id"), {"id": message}
            )
            == "synthetic original"
        )
        await connection.execute(
            text("UPDATE answer_attempts SET status='waiting_input' WHERE id=:id"), {"id": attempt}
        )
        index = await connection.scalar(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname=current_schema() AND indexname='uq_answer_attempt_active'"
            )
        )
        assert "waiting_input" in index and "waiting_approval" in index
        await connection.execute(
            text(
                "INSERT INTO agent_runs(id,owner_id,conversation_id,message_id,attempt_id,"
                "request_id,kb_revision,workspace,graph_version,status) "
                "VALUES(:id,:owner,:chat,:message,:attempt,:request,0,'ordinary','test','waiting_input')"
            ),
            {
                "id": uuid4(),
                "owner": owner,
                "chat": chat,
                "message": message,
                "attempt": attempt,
                "request": uuid4(),
            },
        )
    with pytest.raises(RuntimeError, match="Export Agent records"):
        await downgrade(database)
    async with database.engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM agent_runs")) == 1
        assert (
            await connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0012_agent_runs"
        )


async def test_postgres_interrupt_survives_new_connection_without_replaying(schema_database):  # noqa: F811
    from langgraph.types import Command
    from test_agent_graph import Hooks

    from app.agent.checkpoints import checkpoint_store
    from app.agent.graph import build_graph, initial_state

    database, _ = schema_database
    await migrate(database)
    hooks = Hooks(
        [
            {"action": "call_tool", "tool_id": "test.write", "arguments": {}},
            {"action": "finish", "answer": {"text": "done"}},
        ]
    )
    task_id = str(uuid4())
    config = {"configurable": {"thread_id": task_id}}
    async with checkpoint_store(database, setup=True) as saver:
        result = await build_graph(hooks, saver).ainvoke(initial_state(task_id), config)
        assert result["__interrupt__"] and hooks.executed == []
    async with checkpoint_store(database) as saver:
        result = await build_graph(hooks, saver).ainvoke(Command(resume={"approve": True}), config)
        assert result["answer"] == {"text": "done"}
    assert hooks.executed == ["test.write"]
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError, match="Export Agent checkpoints"):
        await downgrade(database)


async def test_empty_upgrade_can_rollback_and_upgrade_again(schema_database):  # noqa: F811
    database, _ = schema_database
    await migrate(database)

    await downgrade(database)
    await migrate(database)
    async with database.engine.connect() as connection:
        assert await connection.scalar(text("SELECT to_regclass('agent_runs')")) is not None
