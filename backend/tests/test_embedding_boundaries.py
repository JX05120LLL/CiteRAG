"""Public synthetic embedding budget regressions: no database, credentials, or supplier calls."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.credentials import DashScopeConfig
from app.providers.types import EmbeddingBatch, ProviderUsage
from app.rag.database import APPROVED_IMAGE_DIGEST, RagDatabaseSettings
from app.rag.runtime import build_sdk_factory


class FakeClient:
    def __init__(self):
        self.config = DashScopeConfig(
            region="cn-beijing", workspace_id="public-synthetic-workspace",
            models={"answer": "qwen-flash", "engine": "qwen-plus", "summary": "qwen-max",
                    "embedding": "text-embedding-v4", "rerank": "qwen3-vl-rerank"},
            embedding_dimension=1024, api_key=SecretStr("synthetic-not-a-key"),
            credential_ciphertext_sha256="1" * 64,
        )
        self.calls = []

    async def embed(self, texts):
        self.calls.append(("embed", texts))
        return EmbeddingBatch("text-embedding-v4", tuple((0.25,) * 1024 for _ in texts),
                              "synthetic-request", ProviderUsage())


def database_settings():
    # A constructor contract only; no connection is made to this target.
    return RagDatabaseSettings(
        host="127.0.0.1", port=55433, database="assistant_rag", username="synthetic",
        password=SecretStr("synthetic-not-a-password"), container="citerag-rag-postgres",
        image="docker.io/pgvector/pgvector:0.8.1-pg17", image_digest=APPROVED_IMAGE_DIGEST,
        credential_ciphertext_sha256="2" * 64,
    )


@pytest.mark.parametrize("text", ["a" * 7448, "中" * 2483, "🙂" * 1862],
                         ids=["ascii", "chinese", "emoji"])
async def test_sdk_bridge_rejects_embedding_over_local_byte_budget_before_dispatch(
    monkeypatch, text,
):
    import app.rag.runtime as runtime
    from app.services.token_budget import TokenBudgetExceeded

    captured = {}
    monkeypatch.setattr(runtime, "sdk_factory", lambda **kwargs: captured.update(kwargs))
    client = FakeClient()
    build_sdk_factory(client, database_settings())

    with pytest.raises(TokenBudgetExceeded, match="embedding input budget exceeded") as error:
        await captured["embedding_func"](["public synthetic input", text])

    assert client.calls == []
    assert text not in str(error.value)


async def test_sdk_bridge_preserves_complete_inputs_within_local_byte_budget(monkeypatch):
    import app.rag.runtime as runtime

    captured = {}
    monkeypatch.setattr(runtime, "sdk_factory", lambda **kwargs: captured.update(kwargs))
    client = FakeClient()
    build_sdk_factory(client, database_settings())
    texts = ["a" * 7447, "🙂" * 1861 + "abc", "中" * 2482 + "a"]

    result = await captured["embedding_func"](texts)

    assert result.shape == (3, 1024)
    assert client.calls == [("embed", texts)]


async def test_sdk_bridge_rejects_embedding_batch_above_provider_limit_before_dispatch(monkeypatch):
    import app.rag.runtime as runtime

    captured = {}
    monkeypatch.setattr(runtime, "sdk_factory", lambda **kwargs: captured.update(kwargs))
    client = FakeClient()
    build_sdk_factory(client, database_settings())

    with pytest.raises(ValueError, match="embedding batch cannot exceed 10"):
        await captured["embedding_func"](["synthetic"] * 11)

    assert client.calls == []


@pytest.mark.parametrize("overlap", [0, 100])
def test_source_span_chunking_bounds_compressible_utf8_without_losing_original(overlap):
    from lightrag.utils import Tokenizer

    from app.rag.sdk import chunking_by_source_span
    from app.services.token_budget import estimate_text_tokens

    class CompressibleEncoding:
        def encode(self, content):
            # A deliberately compressible synthetic tokenizer: a 1200-token
            # window can carry far more bytes than the provider preflight budget.
            return list(range((len(content) + 15) // 16))

        def decode(self, _tokens):
            raise AssertionError("Source chunking must never reconstruct decoded token windows")

    tokenizer = Tokenizer("synthetic_compressible", CompressibleEncoding())
    content = ("公开合成𠮷🙂aaa\n" * 1600) + "尾端原文必须保留。"
    chunks = chunking_by_source_span(
        tokenizer, content, chunk_token_size=1200, chunk_overlap_token_size=overlap,
    )

    assert len(chunks) > 1
    assert all(chunk["content"] in content for chunk in chunks)
    assert all(estimate_text_tokens(chunk["content"]) <= 8192 for chunk in chunks)
    assert all(chunk["tokens"] == len(tokenizer.encode(chunk["content"])) <= 1200
               for chunk in chunks)
    assert [chunk["chunk_order_index"] for chunk in chunks] == list(range(len(chunks)))
    assert chunks[-1]["content"].endswith("尾端原文必须保留。")
    if overlap == 0:
        assert "".join(chunk["content"] for chunk in chunks) == content


def test_source_span_only_delimiter_mode_fails_before_sending_oversized_literal_segment():
    from lightrag.utils import Tokenizer

    from app.rag.sdk import chunking_by_source_span
    from app.services.token_budget import TokenBudgetExceeded

    class CompressibleEncoding:
        def encode(self, content):
            return list(range((len(content) + 15) // 16))

    tokenizer = Tokenizer("synthetic_compressible", CompressibleEncoding())
    with pytest.raises(TokenBudgetExceeded, match="embedding input budget exceeded"):
        chunking_by_source_span(tokenizer, "a" * 7448, "|", True, 0, 1200)


def test_embedding_split_rechecks_non_monotonic_substring_tokenization():
    from lightrag.utils import Tokenizer

    from app.rag.sdk import chunking_by_source_span
    from app.services.token_budget import estimate_text_tokens

    class NonMonotonicEncoding:
        def encode(self, content):
            # The complete input compresses to one synthetic token, but most
            # shorter prefixes cost more: byte slicing alone is not sufficient.
            count = 1 if content.endswith("Z") else (len(content) + 2) // 3
            return range(count)

    tokenizer = Tokenizer("synthetic_non_monotonic", NonMonotonicEncoding())
    content = "a" * 15000 + "Z"
    chunks = chunking_by_source_span(
        tokenizer, content, chunk_token_size=1200, chunk_overlap_token_size=0,
    )

    assert "".join(chunk["content"] for chunk in chunks) == content
    assert all(estimate_text_tokens(chunk["content"]) <= 8192 for chunk in chunks)
    assert all(chunk["tokens"] == len(tokenizer.encode(chunk["content"])) <= 1200
               for chunk in chunks)


async def test_compressible_raw_chunks_reach_bridge_intact_in_bounded_batches(monkeypatch):
    from lightrag.utils import Tokenizer

    import app.rag.runtime as runtime
    from app.rag.sdk import chunking_by_source_span
    from app.services.token_budget import estimate_text_tokens

    class CompressibleEncoding:
        def encode(self, content):
            return range((len(content) + 15) // 16)

    captured = {}
    monkeypatch.setattr(runtime, "sdk_factory", lambda **kwargs: captured.update(kwargs))
    client = FakeClient()
    build_sdk_factory(client, database_settings())
    tokenizer = Tokenizer("synthetic_compressible", CompressibleEncoding())
    content = "公开合成𠮷🙂原文及尾注。" * 5000
    chunks = chunking_by_source_span(
        tokenizer, content, chunk_token_size=1200, chunk_overlap_token_size=0,
    )
    texts = [chunk["content"] for chunk in chunks]
    assert len(texts) > 10
    for start in range(0, len(texts), 10):
        batch = texts[start:start + 10]
        vectors = await captured["embedding_func"](batch)
        assert vectors.shape == (len(batch), 1024)

    sent = [text for _kind, batch in client.calls for text in batch]
    assert sent == texts
    assert "".join(sent) == content
    assert all(len(batch) <= 10 for _kind, batch in client.calls)
    assert all(estimate_text_tokens(text) <= 8192 for text in sent)


def guarded_ingestion_case(monkeypatch, *, fail=False, wait_for=None, reached=None,
                           cancel=False, ordinary_error=False):
    """Real production bridge + adapter, with an SDK-style synthetic failure sink."""
    import app.rag.runtime as runtime
    from app.rag.ingestion_adapter import EngineDocument, LightRAGIngestionAdapter
    from app.services.token_budget import TokenBudgetExceeded

    captured = {}
    monkeypatch.setattr(runtime, "sdk_factory", lambda **kwargs: captured.update(kwargs))
    client = FakeClient()
    build_sdk_factory(client, database_settings())
    kb_id = uuid4()
    workspace = "kb_" + kb_id.hex
    document = EngineDocument("doc_synthetic", "source_synthetic", "公开合成入库正文")

    class Store:
        def __init__(self):
            self.workspace = workspace
            self.values = {}

        async def get_by_id(self, key):
            return self.values.get(key)

        async def get_docs_by_statuses(self, *_args, **_kwargs):
            return {}

    class Engine:
        def __init__(self):
            self.workspace = workspace
            self.doc_status, self.full_docs = Store(), Store()
            self.text_chunks, self.chunks_vdb = Store(), Store()

        async def apipeline_enqueue_documents(self, texts, *, ids, file_paths, track_id):
            for text, key, path in zip(texts, ids, file_paths, strict=True):
                self.full_docs.values[key] = {"content": text, "file_path": path}
                self.doc_status.values[key] = {
                    "status": "pending", "file_path": path, "content_length": len(text),
                }

        async def apipeline_process_enqueue_documents(self):
            async def sdk_child_task():
                if ordinary_error:
                    raise RuntimeError("SYNTHETIC_PRIVATE_SDK_BODY")
                try:
                    await captured["embedding_func"](["a" * 7448 if fail else "synthetic"])
                except TokenBudgetExceeded:
                    # Upstream can swallow the actual exception and persist only
                    # FAILED. Its unsafe free-form error text must not be parsed.
                    self.doc_status.values[document.id]["status"] = "failed"
                    self.doc_status.values[document.id]["error"] = "SYNTHETIC_PRIVATE_SDK_BODY"
                else:
                    self.doc_status.values[document.id]["status"] = "processed"
                if reached is not None:
                    reached.set()
                if wait_for is not None:
                    await wait_for.wait()
                if cancel:
                    raise asyncio.CancelledError("synthetic cancellation")

            await asyncio.create_task(sdk_child_task())

    engine = Engine()

    async def get_for_workspace(requested_kb, requested_workspace):
        assert (requested_kb, requested_workspace) == (kb_id, workspace)
        return engine

    adapter = LightRAGIngestionAdapter(SimpleNamespace(get_for_workspace=get_for_workspace))
    return adapter, document, kb_id, workspace, client, captured


async def test_ingestion_preserves_local_limit_when_sdk_swallows_exception_in_child(monkeypatch):
    from app.rag.ingestion_adapter import IngestionError

    adapter, document, kb_id, workspace, client, _ = guarded_ingestion_case(monkeypatch, fail=True)
    with pytest.raises(IngestionError, match="^embedding_input_limit$") as error:
        await adapter.insert(kb_id, workspace, [document], uuid4())

    assert error.value.category == "embedding_input_limit"
    assert "SYNTHETIC_PRIVATE_SDK_BODY" not in str(error.value)
    assert document.text not in repr(error.value)
    assert error.value.__cause__ is None
    assert client.calls == []


async def test_untrusted_sdk_failure_text_cannot_claim_local_embedding_limit(monkeypatch):
    from app.rag.ingestion_adapter import IngestionError

    adapter, document, kb_id, workspace, _, _ = guarded_ingestion_case(monkeypatch)
    engine = await adapter._runtime.get_for_workspace(kb_id, workspace)

    async def failed_without_guard():
        engine.doc_status.values[document.id]["status"] = "failed"
        engine.doc_status.values[document.id]["error"] = "embedding input budget exceeded"

    monkeypatch.setattr(engine, "apipeline_process_enqueue_documents", failed_without_guard)
    with pytest.raises(IngestionError, match="^rebuild_required$"):
        await adapter.insert(kb_id, workspace, [document], uuid4())


async def test_local_limit_failure_is_scoped_across_concurrent_and_later_ingestion(monkeypatch):
    from app.rag.ingestion_adapter import IngestionError

    reached, release = asyncio.Event(), asyncio.Event()
    failed, document, kb_id, workspace, _, _ = guarded_ingestion_case(
        monkeypatch, fail=True, reached=reached, wait_for=release,
    )
    task = asyncio.create_task(failed.insert(kb_id, workspace, [document], uuid4()))
    try:
        await asyncio.wait_for(reached.wait(), 2)
        for _ in range(2):
            healthy, good_document, good_kb, good_workspace, client, _ = guarded_ingestion_case(
                monkeypatch,
            )
            await healthy.insert(good_kb, good_workspace, [good_document], uuid4())
            assert client.calls == [("embed", ["synthetic"])]
    finally:
        release.set()
    with pytest.raises(IngestionError, match="^embedding_input_limit$"):
        await task

    healthy, good_document, good_kb, good_workspace, client, _ = guarded_ingestion_case(monkeypatch)
    await healthy.insert(good_kb, good_workspace, [good_document], uuid4())
    assert client.calls == [("embed", ["synthetic"])]


@pytest.mark.parametrize("mode", ["ordinary", "cancelled"])
async def test_local_limit_scope_does_not_relabel_unrelated_failures_or_cancellation(
    monkeypatch, mode,
):
    from app.rag.ingestion_adapter import IngestionError

    adapter, document, kb_id, workspace, _, _ = guarded_ingestion_case(
        monkeypatch, fail=mode == "cancelled", cancel=mode == "cancelled",
        ordinary_error=mode == "ordinary",
    )
    expected = asyncio.CancelledError if mode == "cancelled" else IngestionError
    with pytest.raises(expected) as error:
        await adapter.insert(kb_id, workspace, [document], uuid4())
    if mode == "ordinary":
        assert error.value.category == "engine_unavailable"
        assert "SYNTHETIC_PRIVATE_SDK_BODY" not in str(error.value)

    healthy, good_document, good_kb, good_workspace, _, _ = guarded_ingestion_case(monkeypatch)
    await healthy.insert(good_kb, good_workspace, [good_document], uuid4())


@pytest.mark.parametrize("swallow", [False, True])
async def test_verification_preserves_local_limit_even_if_upstream_returns_success(
    monkeypatch, swallow,
):
    from app.rag.ingestion_adapter import IngestionError, LightRAGIngestionAdapter
    from app.services.token_budget import TokenBudgetExceeded

    adapter, document, kb_id, workspace, client, captured = guarded_ingestion_case(monkeypatch)

    async def verification(_engine, _manifest):
        try:
            await captured["embedding_func"](["🙂" * 1862])
        except TokenBudgetExceeded:
            if not swallow:
                raise
        return {document.id: 1}

    monkeypatch.setattr(LightRAGIngestionAdapter, "_verify", staticmethod(verification))
    with pytest.raises(IngestionError, match="^embedding_input_limit$"):
        await adapter.verify(kb_id, workspace, [document])
    assert client.calls == []
