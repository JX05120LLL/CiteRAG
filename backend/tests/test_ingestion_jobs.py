"""Managed ingestion: real isolated PostgreSQL, synthetic content, no providers."""
import asyncio
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from test_postgres_local import ORIGIN, migrate, seed_knowledge_base

from app.main import create_app

pytestmark = pytest.mark.postgres
pytest_plugins = ["test_postgres_local"]


@pytest_asyncio.fixture
async def ingestion_environment(schema_database, tmp_path, monkeypatch):
    async def start(_runtime, _assert_owned):
        return None
    monkeypatch.setattr("app.rag.runtime.RagRuntime.start", start)
    database, settings = schema_database
    await migrate(database)
    app = create_app(settings, database=database)
    # Root is overridden before lifespan, without reading any real files.
    app.state.source_root = tmp_path / "sources"
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app, raise_app_exceptions=False),
            base_url=ORIGIN, headers={"Origin": ORIGIN},
        ) as api:
            yield api, database, app


async def new_kb(api):
    response = await api.post("/api/knowledge-bases", json={
        "name": "Synthetic library", "client_request_id": str(uuid4()),
    })
    assert response.status_code == 201
    return response.json()["id"]


async def upload(api, kb, key=None, data=b"Synthetic manual A001.\nOnly test content."):
    return await api.post(f"/api/knowledge-bases/{kb}/documents",
                          data={"client_request_id": str(key or uuid4())},
                          files=[("files", ("manual.txt", data, "text/plain"))])


async def wait_job(api, identifier, expected):
    for _ in range(150):
        response = await api.get(f"/api/jobs/{identifier}")
        assert response.status_code == 200
        job = response.json()
        if expected(job):
            return job
        await asyncio.sleep(.05)
    pytest.fail("job did not reach expected safe state")


async def test_upload_persists_separate_parsed_state_and_positions(ingestion_environment):
    api, database, app = ingestion_environment
    kb = await new_kb(api)
    response = await upload(api, kb)
    assert response.status_code == 202
    job = await wait_job(api, response.json()["id"], lambda job: job["stage"] == "parsed")
    assert job["status"] == "queued" and not job["engine_mutated"]
    assert (await api.get("/api/knowledge-bases")).json()["items"][0]["status"] == "empty"
    docs = (await api.get(f"/api/knowledge-bases/{kb}/documents")).json()["items"]
    assert len(docs) == 1 and docs[0]["status"] == "parsed"
    blocks = await api.get(f"/api/documents/{docs[0]['id']}/blocks")
    assert blocks.status_code == 200
    assert blocks.json()["items"][0]["locator"]["line_start"] == 1
    original = await api.get(f"/api/documents/{docs[0]['id']}/original")
    assert original.content == b"Synthetic manual A001.\nOnly test content."
    assert "attachment" in original.headers["Content-Disposition"]
    async with database.engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM ingestion_jobs")) == 1
        assert await conn.scalar(text("SELECT count(*) FROM parsed_blocks")) > 0


async def test_same_request_replay_and_hash_duplicates(ingestion_environment):
    api, database, _ = ingestion_environment
    kb = await new_kb(api)
    key = uuid4()
    results = await asyncio.gather(*(upload(api, kb, key) for _ in range(4)))
    assert all(result.status_code == 202 for result in results)
    assert len({result.json()["id"] for result in results}) == 1
    conflict = await upload(api, kb, key, b"Different synthetic content.")
    assert conflict.status_code == 409
    duplicate = await upload(api, kb)
    assert duplicate.status_code == 409
    async with database.engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM documents")) == 1


async def test_precheck_failure_does_not_block_ready_library(ingestion_environment):
    api, database, _ = ingestion_environment
    kb, _ = await seed_knowledge_base(database, "Already ready", "ready")
    response = await upload(api, kb, data=b"\xff\xfe\x00\x00")
    assert response.status_code == 202
    job = await wait_job(api, response.json()["id"], lambda job: job["status"] == "failed")
    assert not job["engine_mutated"]
    assert (await api.get("/api/knowledge-bases")).json()["items"][0]["status"] == "ready"


async def test_ownership_and_local_boundary_on_file_routes(ingestion_environment):
    api, database, _ = ingestion_environment
    legacy, _ = await seed_knowledge_base(database, "Legacy", "ready", owned=False)
    assert (await upload(api, legacy)).status_code == 404
    kb = await new_kb(api)
    assert (await api.get(f"/api/knowledge-bases/{kb}/documents",
                          headers={"Origin": "https://outside.invalid"})).status_code == 403
    assert (await api.get(f"/api/documents/{uuid4()}/original")).status_code == 404


async def test_disk_failure_is_not_accepted_or_reported_as_database_error(
    ingestion_environment, monkeypatch,
):
    api, database, app = ingestion_environment
    kb = await new_kb(api)

    async def fail(*_args):
        raise OSError("synthetic private disk details")

    monkeypatch.setattr(app.state.source_store, "stage", fail)
    response = await upload(api, kb)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "storage_unavailable"
    assert "synthetic private" not in response.text
    async with database.engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM documents")) == 0
        assert await connection.scalar(text("SELECT count(*) FROM ingestion_jobs")) == 0


def test_preflight_text_identity_matches_locked_sdk():
    import hashlib

    from lightrag.utils import sanitize_text_for_encoding

    from app.ingestion.content import engine_content_hash

    for text_value in (" a &amp; b\n", "\t&amp;#x7f;\r", "\ufffftext", "&NewLine;x"):
        assert engine_content_hash(text_value) == hashlib.sha256(
            sanitize_text_for_encoding(text_value).encode()
        ).hexdigest()
