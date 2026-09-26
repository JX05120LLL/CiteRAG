"""Managed ingestion: real isolated PostgreSQL, synthetic content, no providers."""
import asyncio
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from test_postgres_local import ORIGIN, migrate, seed_knowledge_base

from app.main import create_app

pytestmark = pytest.mark.postgres
pytest_plugins = ["test_postgres_local"]


@pytest.fixture
def unicode_tokenizer():
    from lightrag.utils import Tokenizer

    class ByteEncoding:
        def encode(self, content):
            return list(content.encode("utf-8"))

        def decode(self, tokens):
            return bytes(tokens).decode("utf-8", errors="replace")

    # Deliberately splits multibyte characters just like byte-level BPE can.
    # No tokenizer download or local private assets are needed in CI.
    return Tokenizer("synthetic_utf8", ByteEncoding())


@pytest.mark.parametrize("overlap", [0, 100])
def test_engine_factory_chunks_unicode_without_corrupting_original(
    monkeypatch, tmp_path, overlap, unicode_tokenizer,
):
    from lightrag.chunker import chunking_by_token_size

    from app.rag import sdk

    tokenizer = unicode_tokenizer
    content = "面试复习数据库事务索引并发处理𠮷🙂繁體漢字知识库来源必须核查" * 500
    # Reproduce the pinned SDK's byte-split boundary, without any provider calls.
    legacy = chunking_by_token_size(tokenizer, content, chunk_overlap_token_size=overlap)
    assert any(chunk["content"] not in content for chunk in legacy)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setattr(sdk, "_verified_tokenizer", lambda: tokenizer)
    monkeypatch.setitem(sys.modules, "lightrag", SimpleNamespace(
        LightRAG=lambda **options: options,
    ))

    async def no_provider(*args, **kwargs):
        pytest.fail("Chunking must not call a model")

    factory = sdk.sdk_factory(
        llm_model_func=no_provider, embedding_func=no_provider, rerank_model_func=no_provider,
    )
    options = factory("kb_00000000000040008000000000000001", tmp_path / "engine")
    chunks = options["chunking_func"](
        tokenizer, content, chunk_token_size=1200, chunk_overlap_token_size=overlap,
    )
    assert len(chunks) > 1
    assert all(chunk["content"] in content for chunk in chunks)
    assert all(chunk["tokens"] == len(tokenizer.encode(chunk["content"])) <= 1200
               for chunk in chunks)
    assert [chunk["chunk_order_index"] for chunk in chunks] == list(range(len(chunks)))
    if overlap == 0:
        assert "".join(chunk["content"] for chunk in chunks) == content


@pytest.mark.parametrize("content", ["", "  \n\t", " 中文🙂\n", "原文已有�字符" * 20])
def test_source_span_chunking_keeps_literal_text_and_skips_blank_chunks(
    content, unicode_tokenizer,
):
    from app.rag.sdk import chunking_by_source_span

    chunks = chunking_by_source_span(
        unicode_tokenizer, content, chunk_token_size=20, chunk_overlap_token_size=0,
    )
    if content.strip():
        assert "".join(chunk["content"] for chunk in chunks) == content
        assert all(chunk["tokens"] <= 20 for chunk in chunks)
    else:
        assert chunks == []


def test_source_span_chunking_preserves_delimiter_contract_and_limits(unicode_tokenizer):
    from lightrag.exceptions import ChunkTokenLimitExceededError

    from app.rag.sdk import chunking_by_source_span

    content = "中文🙂" * 20 + "|短句|"
    chunks = chunking_by_source_span(unicode_tokenizer, content, "|", False, 0, 20)
    assert "".join(chunk["content"] for chunk in chunks) == content.replace("|", "")
    assert all(chunk["tokens"] <= 20 for chunk in chunks)
    with pytest.raises(ChunkTokenLimitExceededError) as error:
        chunking_by_source_span(unicode_tokenizer, content, "|", True, 0, 20)
    assert error.value.chunk_preview is None
    assert chunking_by_source_span(unicode_tokenizer, "短句|中文", "|", True, 0, 20) == [
        {"content": "短句", "tokens": 6, "chunk_order_index": 0},
        {"content": "中文", "tokens": 6, "chunk_order_index": 1},
    ]


@pytest.mark.parametrize("size,overlap", [(0, 0), (20, -1), (20, 20), (20, 21)])
def test_source_span_chunking_rejects_invalid_token_limits(size, overlap, unicode_tokenizer):
    from app.rag.sdk import chunking_by_source_span

    with pytest.raises(ValueError, match="Invalid chunk token"):
        chunking_by_source_span(
            unicode_tokenizer, "中文", chunk_token_size=size, chunk_overlap_token_size=overlap,
        )


async def test_unicode_ingestion_passes_real_sdk_storage_and_source_verification(
    tmp_path, unicode_tokenizer, monkeypatch,
):
    import numpy as np
    from lightrag import LightRAG
    from lightrag.kg.shared_storage import initialize_pipeline_status
    from lightrag.utils import EmbeddingFunc

    from app.rag.ingestion_adapter import EngineDocument, LightRAGIngestionAdapter
    from app.rag.sdk import chunking_by_source_span

    # Real pinned SDK pipeline, isolated file stores and synthetic local models.
    # This proves ingestion wiring, not supplier/model or PostgreSQL acceptance.
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")

    async def completion(*args, **kwargs):
        return "<|COMPLETE|>"

    async def embedding(texts):
        return np.ones((len(texts), 8), dtype=np.float32)

    kb_id = uuid4()
    workspace = "kb_" + kb_id.hex
    engine = LightRAG(
        working_dir=str(tmp_path), workspace=workspace,
        llm_model_func=completion,
        embedding_func=EmbeddingFunc(embedding_dim=8, func=embedding),
        tokenizer=unicode_tokenizer, chunking_func=chunking_by_source_span,
        chunk_token_size=1200, chunk_overlap_token_size=100,
    )

    class Runtime:
        async def get_for_workspace(self, requested_kb, requested_workspace):
            assert (requested_kb, requested_workspace) == (kb_id, workspace)
            return engine

    adapter = LightRAGIngestionAdapter(Runtime())
    document = EngineDocument(
        "doc_unicode", "source_unicode",
        "  # 面试复习\n数据库事务索引并发𠮷🙂繁體漢字\n\n" * 50,
    )
    await engine.initialize_storages()
    try:
        await initialize_pipeline_status(workspace)
        await adapter.insert(kb_id, workspace, [document], uuid4())
        result = await adapter.verify(kb_id, workspace, [document])
        assert result[document.id] > 1
        assert (await engine.doc_status.get_by_id(document.id))["status"] == "processed"
    finally:
        await engine.finalize_storages()


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
