"""Real migrations and ownership queries on an isolated, disposable PostgreSQL schema."""

import os
import socket
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.database import Database
from app.main import create_app

pytestmark = pytest.mark.postgres
ORIGIN = "http://127.0.0.1:5173"


async def migrate(database, revision="head"):
    async with database.engine.begin() as connection:
        def run(sync_connection):
            config = Config("alembic.ini")
            config.attributes["connection"] = sync_connection
            command.upgrade(config, revision)
        await connection.run_sync(run)


@pytest_asyncio.fixture
async def schema_database():
    dsn = os.environ.get("CITERAG_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set CITERAG_TEST_DATABASE_URL to a disposable, isolated PostgreSQL instance")
    schema = "citerag_test_" + uuid4().hex
    control = create_async_engine(dsn, echo=False, hide_parameters=True)
    async with control.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        dsn, echo=False, hide_parameters=True,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        yield Database(engine), Settings(database_url=SecretStr(dsn), allowed_origins=(ORIGIN,))
    finally:
        await engine.dispose()
        async with control.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await control.dispose()


@pytest_asyncio.fixture
async def environment(schema_database, monkeypatch):
    # Business DB tests do not depend on optional model/engine credentials.
    async def unconfigured(_state):
        return {"models": "not_configured", "rag": "not_configured"}

    monkeypatch.setattr(
        "app.api.routes.load_capability_status",
        unconfigured,
    )
    database, settings = schema_database
    await migrate(database)
    app = create_app(settings, database=database)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app, raise_app_exceptions=False),
            base_url=ORIGIN, headers={"Origin": ORIGIN}
        ) as client:
            yield client, database


async def test_empty_migration_seeds_one_local_profile_and_no_demo_data(schema_database):
    database, _ = schema_database
    await migrate(database)
    async with database.engine.connect() as connection:
        assert await connection.scalar(text("SELECT to_regclass('local_profiles')")) is not None
        original = list(await connection.scalars(text("SELECT id FROM local_profiles")))
        assert len(original) == 1
        for table in ("users", "auth_sessions", "knowledge_bases", "conversations"):
            assert await connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    await migrate(database)
    async with database.engine.connect() as connection:
        assert list(await connection.scalars(text("SELECT id FROM local_profiles"))) == original
    with pytest.raises(IntegrityError) as rejected:
        async with database.engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO local_profiles (id) VALUES (:id)"), {"id": uuid4()}
            )
    assert "uq_local_profiles_singleton" in str(rejected.value.orig)


async def test_archive_migration_keeps_existing_local_chat_active(schema_database):
    database, _ = schema_database
    await migrate(database, "0008_image_attachments")
    kb_id, owner_id = await seed_knowledge_base(database, "Existing", "ready")
    chat_id = uuid4()
    async with database.engine.begin() as connection:
        await connection.execute(text(
            "INSERT INTO conversations (id, kb_id, local_owner_id, title) "
            "VALUES (:id, :kb, :owner, :title)"
        ), {"id": chat_id, "kb": kb_id, "owner": owner_id, "title": "Existing chat"})
    await migrate(database)
    async with database.engine.connect() as connection:
        row = (await connection.execute(text(
            "SELECT title, archived_at FROM conversations WHERE id = :id"
        ), {"id": chat_id})).one()
    assert row.title == "Existing chat" and row.archived_at is None


async def test_empty_local_workbench_needs_no_account(environment):
    client, _ = environment
    for path in ("/api/knowledge-bases", "/api/conversations"):
        response = await client.get(path)
        assert response.status_code == 200
        assert response.json() == {"items": []}
        assert "set-cookie" not in response.headers
    assert (await client.get("/api/status")).json() == {
        "status": "partial", "mode": "local_single_user", "database": "available",
        "rag": "not_configured", "models": "not_configured",
        "backup": "disabled", "retention": "available",
    }


async def seed_knowledge_base(database, name, status, *, owned=True):
    kb_id = uuid4()
    async with database.engine.begin() as connection:
        local_id = await connection.scalar(text("SELECT id FROM local_profiles"))
        await connection.execute(
            text("INSERT INTO knowledge_bases (id, name, status, active_workspace, local_owner_id) "
                 "VALUES (:id, :name, :status, :workspace, :owner)"),
            {"id": kb_id, "name": name, "status": status, "workspace": "test_" + kb_id.hex,
             "owner": local_id if owned else None},
        )
    return kb_id, local_id


async def test_library_listing_includes_all_owned_states_but_excludes_unclaimed(environment):
    client, database = environment
    expected = set()
    for status in ("ready", "empty", "maintaining", "blocked"):
        kb_id, _ = await seed_knowledge_base(database, status, status)
        expected.add(str(kb_id))
    await seed_knowledge_base(database, "legacy", "ready", owned=False)
    response = await client.get("/api/knowledge-bases")
    assert response.status_code == 200
    assert {item["id"] for item in response.json()["items"]} == expected
    assert {item["status"] for item in response.json()["items"]} == {
        "ready", "empty", "maintaining", "blocked"
    }


async def test_conversations_use_server_owner_and_ready_owned_library(environment):
    client, database = environment
    ready_id, local_id = await seed_knowledge_base(database, "Ready", "ready")
    forged_id = uuid4()
    response = await client.post(
        "/api/conversations", json={"kb_id": str(ready_id), "owner_id": str(forged_id),
                                    "title": "Local conversation"},
    )
    assert response.status_code == 201, response.text
    conversation = response.json()
    assert conversation["owner_id"] == str(local_id)
    assert conversation["kb_id"] == str(ready_id)
    assert (await client.get(f"/api/conversations/{conversation['id']}")).json() == conversation
    assert (await client.get("/api/conversations")).json() == {"items": [conversation]}
    async with database.engine.connect() as connection:
        assert await connection.scalar(
            text("SELECT owner_id FROM conversations WHERE id = :id"), {"id": conversation["id"]}
        ) is None
    for status in ("empty", "maintaining", "blocked"):
        kb_id, _ = await seed_knowledge_base(database, status, status)
        rejected = await client.post("/api/conversations", json={"kb_id": str(kb_id)})
        assert rejected.status_code == 409
        assert rejected.json()["detail"]["code"] == "kb_not_ready"
    legacy_id, _ = await seed_knowledge_base(database, "Legacy", "ready", owned=False)
    for kb_id in (legacy_id, uuid4()):
        rejected = await client.post("/api/conversations", json={"kb_id": str(kb_id)})
        assert rejected.status_code == 404


async def test_upgrade_preserves_old_records_without_exposing_or_adopting_them(schema_database):
    database, settings = schema_database
    await migrate(database, "0001_m0_accounts")
    ids = {key: uuid4() for key in ("user", "kb", "conversation")}
    async with database.engine.begin() as connection:
        await connection.execute(text(
            "INSERT INTO users (id, username, password_hash, role) "
            "VALUES (:user, 'legacy_user', 'legacy-synthetic-hash', 'admin')"
        ), ids)
        await connection.execute(text(
            "INSERT INTO auth_sessions (token_hash, user_id, expires_at) "
            "VALUES ('legacy-synthetic-token-hash', :user, now() + interval '1 day')"
        ), ids)
        await connection.execute(text(
            "INSERT INTO knowledge_bases (id, name, status, active_workspace) "
            "VALUES (:kb, 'Legacy library', 'ready', 'legacy_workspace')"
        ), ids)
        await connection.execute(text(
            "INSERT INTO conversations (id, owner_id, kb_id, title) "
            "VALUES (:conversation, :user, :kb, 'Legacy conversation')"
        ), ids)
    await migrate(database)
    async with database.engine.connect() as connection:
        assert await connection.scalar(text("SELECT to_regclass('local_profiles')")) is not None
        password_hash = await connection.scalar(text("SELECT password_hash FROM users"))
        token_hash = await connection.scalar(text("SELECT token_hash FROM auth_sessions"))
        assert password_hash == "legacy-synthetic-hash"
        assert token_hash == "legacy-synthetic-token-hash"
        assert await connection.scalar(text("SELECT owner_id FROM conversations")) == ids["user"]
        assert await connection.scalar(text("SELECT local_owner_id FROM conversations")) is None
        assert await connection.scalar(text("SELECT local_owner_id FROM knowledge_bases")) is None
    app = create_app(settings, database=database)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app), base_url=ORIGIN, headers={"Origin": ORIGIN}
        ) as client:
            assert (await client.get("/api/conversations")).json() == {"items": []}
            assert (await client.get("/api/knowledge-bases")).json() == {"items": []}
            hidden = await client.get(f"/api/conversations/{ids['conversation']}")
            assert hidden.status_code == 404
            rejected = await client.post("/api/conversations", json={"kb_id": str(ids["kb"])})
            assert rejected.status_code == 404


async def test_missing_local_profile_refuses_startup_without_recreating_it(schema_database):
    database, settings = schema_database
    await migrate(database)
    async with database.engine.begin() as connection:
        await connection.execute(text("DELETE FROM local_profiles"))
    app = create_app(settings, database=database)
    with pytest.raises(RuntimeError, match="local profile"):
        async with app.router.lifespan_context(app):
            pytest.fail("Startup accepted a missing local profile")
    async with database.engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM local_profiles")) == 0


async def test_healthy_owner_does_not_mask_unavailable_business_database(environment):
    client, database = environment
    # Break a query table, keeping the real PG ownership connection intact.
    async with database.engine.begin() as connection:
        await connection.execute(text("ALTER TABLE local_profiles RENAME TO unavailable_profile"))
    response = await client.get("/api/status")
    assert response.status_code == 200
    assert response.json()["database"] == "unavailable"
    assert "unavailable_profile" not in response.text
    assert "asyncpg" not in response.text
    listing = await client.get("/api/knowledge-bases")
    assert listing.status_code == 503
    assert listing.json()["detail"]["code"] == "persistence_failed"


async def test_unreachable_business_connection_returns_unavailable_not_internal_error(environment):
    client, database = environment
    # Reserve, but do not listen on, a local port. Only the business pool is
    # redirected; the real PostgreSQL ownership connection remains healthy.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        unreachable = create_async_engine(
            database.engine.url.set(host="127.0.0.1", port=reserved.getsockname()[1]),
            echo=False, hide_parameters=True,
            connect_args={"timeout": 1, "command_timeout": 1},
        )
        database.sessions.configure(bind=unreachable)
        try:
            status = await client.get("/api/status")
            assert status.status_code == 200
            assert status.json()["database"] == "unavailable"
            listing = await client.get("/api/knowledge-bases")
            assert listing.status_code == 503
            assert listing.json()["detail"]["code"] == "persistence_failed"
        finally:
            database.sessions.configure(bind=database.engine)
            await unreachable.dispose()


async def test_partial_claim_cannot_expose_conversation_from_unowned_library(environment):
    client, database = environment
    kb_id, local_id = await seed_knowledge_base(database, "Unclaimed", "ready", owned=False)
    conversation_id = uuid4()
    async with database.engine.begin() as connection:
        await connection.execute(text(
            "INSERT INTO conversations (id, local_owner_id, kb_id, title) "
            "VALUES (:id, :owner, :kb, 'Unclaimed library conversation')"
        ), {"id": conversation_id, "owner": local_id, "kb": kb_id})
    assert (await client.get("/api/conversations")).json() == {"items": []}
    assert (await client.get(f"/api/conversations/{conversation_id}")).status_code == 404


async def test_downgrade_refuses_to_erase_local_ownership(environment):
    _, database = environment
    kb_id, local_id = await seed_knowledge_base(database, "Local", "empty")
    with pytest.raises(DBAPIError, match="Cannot downgrade while local-owned data exists"):
        async with database.engine.begin() as connection:
            def downgrade(sync_connection):
                config = Config("alembic.ini")
                config.attributes["connection"] = sync_connection
                command.downgrade(config, "0001_m0_accounts")
            await connection.run_sync(downgrade)
    async with database.engine.connect() as connection:
        assert await connection.scalar(text(
            "SELECT local_owner_id FROM knowledge_bases WHERE id = :id"
        ), {"id": kb_id}) == local_id


async def test_failed_database_write_cannot_appear_saved(environment):
    client, database = environment
    kb_id, _ = await seed_knowledge_base(database, "Ready", "ready")
    async with database.engine.begin() as connection:
        await connection.execute(text(
            "CREATE FUNCTION reject_test_insert() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN RAISE EXCEPTION 'synthetic persistence failure'; END $$"
        ))
        await connection.execute(text(
            "CREATE TRIGGER reject_test_insert BEFORE INSERT ON conversations "
            "FOR EACH ROW EXECUTE FUNCTION reject_test_insert()"
        ))
    response = await client.post("/api/conversations", json={"kb_id": str(kb_id)})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "persistence_failed"
    assert "synthetic persistence failure" not in response.text
    assert (await client.get("/api/conversations")).json() == {"items": []}
