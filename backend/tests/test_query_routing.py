"""Offline checks for model-selected query routing; no provider or business DB."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.credentials import DashScopeConfig
from app.providers.dashscope import DashScopeClient
from app.providers.types import Message
from app.rag.answer_adapter import AnswerError, LightRAGAnswerAdapter, checked_answer, checked_route
from app.rag.runtime import RagRuntime
from app.services.answers import AnswerService, matching_candidates


def test_context_route_resolves_followup_and_accepts_general_without_fake_filters():
    context = {"turns": [{"user": "介绍 Nimbus 项目", "assistant": "旧回答不是证据"}]}
    assert checked_route('{"mode":"general"}', [], "什么是 RAG？") == {"mode": "general"}
    with pytest.raises(AnswerError):
        checked_route('{"mode":"chat"}', [], "谢谢")
    assert checked_route(
        '{"mode":"semantic","query":"Nimbus 项目的并发控制"}', [], "它如何控制并发？",
        context=context,
    ) == {"mode": "semantic", "query": "Nimbus 项目的并发控制"}
    with pytest.raises(AnswerError):
        checked_route('{"mode":"general","evidence_ids":["E1"]}', [], "什么是 RAG？")


def test_summary_can_paraphrase_multiple_real_quotes_but_not_forge_support():
    evidence = {"E1": "Nimbus uses PostgreSQL.", "E2": "Nimbus queues work with Redis."}
    data = {"status": "answered", "text": "Nimbus combines a database and a work queue.",
            "evidence_ids": ["E1", "E2"], "support": [
                {"evidence_id": "E1", "quote": evidence["E1"]},
                {"evidence_id": "E2", "quote": evidence["E2"]},
            ]}
    assert checked_answer(json.dumps(data), evidence) == (
        "answered", data["text"], ["E1", "E2"],
    )
    for support in [[], [{"evidence_id": "E9", "quote": evidence["E1"]}],
                    [{"evidence_id": "E1", "quote": "Nimbus uses MySQL."}],
                    [{"evidence_id": "E1", "quote": evidence["E1"]}]]:
        with pytest.raises(AnswerError):
            checked_answer(json.dumps({**data, "support": support}), evidence)
    with pytest.raises(AnswerError, match="answer_source_mismatch"):
        checked_answer(json.dumps({**data, "text": "See https://invented.invalid"}), evidence)


def test_summary_urls_must_match_complete_source_urls_not_a_prefix():
    source = "Project URL: https://example.invalid/nimbus"
    data = {"status": "answered", "text": "See https://example.invalid/nimbus",
            "evidence_ids": ["E1"], "support": [{"evidence_id": "E1", "quote": source}]}
    assert checked_answer(json.dumps(data), {"E1": source})[0] == "answered"
    with pytest.raises(AnswerError, match="answer_source_mismatch"):
        checked_answer(json.dumps({**data, "text": "See https://example.invalid"}), {"E1": source})


@pytest.mark.asyncio
@pytest.mark.parametrize("supported", [True, False])
async def test_summary_requires_separate_fact_support_check_before_publication(supported):
    from app.services.answers import AnswerService

    evidence = [{"id": "E1", "text": "Nimbus uses PostgreSQL."}]
    raw = json.dumps({"status": "answered", "text": "Nimbus stores data in a database.",
                      "evidence_ids": ["E1"], "support": [
                          {"evidence_id": "E1", "quote": evidence[0]["text"]}]})

    class Verifier:
        calls = 0

        async def verify_answer(self, text, sources):
            self.calls += 1
            assert text == "Nimbus stores data in a database." and sources == evidence
            return json.dumps({"supported": supported})

    verifier = Verifier()
    if supported:
        result = await AnswerService._checked_result(verifier, raw, evidence, [])
        assert result[:2] == ("answered", "Nimbus stores data in a database.")
    else:
        with pytest.raises(AnswerError, match="^answer_unsupported_claims$"):
            await AnswerService._checked_result(verifier, raw, evidence, [])
    assert verifier.calls == 1


@pytest.mark.asyncio
async def test_runtime_route_receives_bounded_context_and_document_names():
    class Client:
        async def complete(self, model, messages, *, max_tokens):
            payload = json.loads(messages[1].content)
            assert payload["conversation_context"]["turns"][0]["user"] == "介绍 Nimbus 项目"
            assert payload["documents"] == [{"filename": "synthetic.txt", "status": "ready"}]
            assert "general" in messages[0].content and "近期对话" in messages[0].content
            assert "简历" not in messages[0].content and "项目" not in messages[0].content
            assert "当前库" in messages[0].content and "优先知识库检索" in messages[0].content
            return SimpleNamespace(content='{"mode":"semantic","query":"Nimbus 并发控制"}')

    runtime = RagRuntime(Settings())
    runtime._started = True
    runtime._owner_assertion = lambda: None
    runtime._client = Client()
    result = await runtime.route_question("它如何控制并发？", [],
        {"turns": [{"user": "介绍 Nimbus 项目"}]},
        [{"filename": "synthetic.txt", "status": "ready"}])
    assert json.loads(result)["query"] == "Nimbus 并发控制"


@pytest.mark.asyncio
async def test_legacy_chat_route_uses_model_with_recent_context():
    context = {"turns": [{"user": "我喜欢简洁回答", "assistant": "收到", "answer_kind": "general"}]}

    class Model:
        async def general_answer(self, question, received):
            assert question == "你好" and received == context
            return '{"text":"你好，今天想聊什么？我会尽量简洁。"}'

    status, text, citations = await AnswerService(None)._resolve(
        uuid4(), "synthetic_workspace", "你好", None, Model(), "auto",
        {"mode": "chat"}, context,
    )
    assert (status, text, citations) == ("answered", "你好，今天想聊什么？我会尽量简洁。", [])


@pytest.mark.asyncio
async def test_general_answer_keeps_dialogue_but_not_old_knowledge_answer_text():
    context = {"summary": "用户关心温度", "turns": [
        {"user": "手册规定多少度？", "assistant": "旧资料声称 42 度", "answer_kind": "knowledge"},
        {"user": "谢谢", "assistant": "不客气", "answer_kind": "chat"},
    ]}

    class Model:
        async def general_answer(self, question, received):
            assert question == "聊聊温度的常见单位"
            assert received["summary"] == "用户关心温度"
            assert received["turns"][0] == {
                "user": "手册规定多少度？", "assistant": "", "answer_kind": "knowledge",
            }
            assert received["turns"][1] == context["turns"][1]
            return '{"text":"摄氏度和华氏度是两种常见温度单位。"}'

    result = await AnswerService(None)._resolve(
        uuid4(), "synthetic_workspace", "聊聊温度的常见单位", None, Model(), "auto",
        {"mode": "general"}, context,
    )
    assert result == ("answered", "摄氏度和华氏度是两种常见温度单位。", [])


@pytest.mark.asyncio
async def test_general_runtime_sends_bounded_history_to_model():
    context = {"summary": "用户希望简洁", "turns": [
        {"user": "什么是 RAG？", "assistant": "检索增强生成。", "answer_kind": "general"},
    ]}

    class Client:
        async def complete(self, model, messages, *, max_tokens):
            assert model == "qwen-flash" and max_tokens == 1024
            payload = json.loads(messages[1].content)
            assert payload == {"question": "能举个例子吗？", "conversation_context": context}
            assert "未检索知识库" in messages[0].content
            return SimpleNamespace(content='{"text":"可以用技术文档说明 RAG 的检索步骤。"}')

    runtime = RagRuntime(Settings())
    runtime._started = True
    runtime._owner_assertion = lambda: None
    runtime._client = Client()
    assert json.loads(await runtime.complete_general("能举个例子吗？", context)) == {
        "text": "可以用技术文档说明 RAG 的检索步骤。",
    }


def test_only_literal_confirmed_attributes_become_candidates():
    attributes = [
        ("doc_code", "A001"), ("doc_code", "A0010"),
        ("model_code", "X100"), ("model_code", "X100 Pro"),
    ]
    assert matching_candidates("查 X100 Pro 的 A0010", attributes) == [
        {"id": "C1", "field": "doc_code", "value": "A0010"},
        {"id": "C2", "field": "model_code", "value": "X100 Pro"},
    ]
    assert matching_candidates("查 A0010", [("doc_code", "A001")]) == []
    assert matching_candidates("查 X100 Pro", [("model_code", "X100")]) == []
    assert matching_candidates("What is the X100 limit?", [("model_code", "X100")]) == [
        {"id": "C1", "field": "model_code", "value": "X100"},
    ]


def test_model_route_accepts_only_known_candidate_ids_and_fields():
    candidates = [
        {"id": "C1", "field": "doc_code", "value": "A001"},
        {"id": "C2", "field": "model_code", "value": "X100"},
    ]
    question = "查 ORD-001 中的 A001 和 X100"
    assert checked_route('{"mode":"semantic"}', candidates, question) == {"mode": "semantic"}
    assert checked_route('{"mode":"exact","candidate_ids":["C1","C2"]}', candidates, question) == {
        "mode": "exact", "filters": {"doc_code": "A001", "model_code": "X100"},
    }
    with pytest.raises(AnswerError):
        checked_route('{"mode":"unsupported"}', [], question)
    assert checked_route('{"mode":"literal","phrase":"ORD-001"}', [], question) == {
        "mode": "literal", "phrase": "ORD-001",
    }
    for raw in [
        '{"mode":"exact","candidate_ids":["C9"]}',
        '{"mode":"exact","candidate_ids":[]}',
        '{"mode":"exact","candidate_ids":["C1","C1"]}',
        '{"mode":"exact","candidate_ids":["C1"],"extra":"text"}',
        '{"mode":"semantic","candidate_ids":["C1"]}',
        '{"mode":"exact","field":"order_id","value":"A001"}',
        '{"mode":"literal","phrase":"ORD-002"}',
        '{"mode":"literal","phrase":"ORD-001","candidate_ids":["C1"]}',
    ]:
        with pytest.raises(AnswerError):
            checked_route(raw, candidates, question)


@pytest.mark.asyncio
async def test_runtime_routes_with_answer_model_and_bounded_output():
    class FakeClient:
        async def complete(self, model, messages, *, max_tokens):
            assert model == "qwen-flash"
            assert max_tokens == 384
            assert messages[1].content == (
                '{"question": "查 X100 的上限", "confirmed_candidates": '
                '[{"id": "C1", "field": "model_code", "value": "X100"}], '
                '"conversation_context": {}, "documents": []}'
            )
            return type("Completion", (), {"content": '{"mode":"exact","candidate_ids":["C1"]}'})()

    runtime = RagRuntime(Settings())
    runtime._started = True
    runtime._owner_assertion = lambda: None
    runtime._client = FakeClient()
    assert await runtime.route_question("查 X100 的上限", [
        {"id": "C1", "field": "model_code", "value": "X100"},
    ]) == '{"mode":"exact","candidate_ids":["C1"]}'


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["route", "answer", "stream", "summary", "general", "verify"])
async def test_cold_runtime_can_use_models_before_any_engine_is_opened(
    tmp_path, monkeypatch, operation,
):
    import app.rag.runtime as module

    created = []

    class Client:
        def __init__(self, config):
            self.calls = 0
            self.closed = 0
            created.append(self)

        async def complete(self, *args, **kwargs):
            self.calls += 1
            return SimpleNamespace(content="synthetic output")

        async def stream_complete(self, *args, **kwargs):
            self.calls += 1
            yield "synthetic output"

        async def aclose(self):
            self.closed += 1

    monkeypatch.setattr(module, "load_dashscope_config", lambda path: object())
    monkeypatch.setattr(module, "DashScopeClient", Client)
    monkeypatch.setattr(module, "build_sdk_factory",
                        lambda *a, **k: pytest.fail("No engine needed"))
    monkeypatch.setattr(RagRuntime, "_apply_database_environment",
                        lambda *args: pytest.fail("No engine database environment needed"))
    instance = RagRuntime(Settings.model_construct(rag_database_record=tmp_path / "absent.xml"))
    owned = True

    def assert_owner():
        if not owned:
            raise RuntimeError("owner lost")

    await instance.start(assert_owner)
    assert created == []  # Startup stays free of model credentials and provider requests.

    async def call():
        if operation == "route":
            return await instance.route_question("synthetic question", [])
        if operation == "answer":
            return await instance.complete_answer("synthetic question", [])
        if operation == "summary":
            return await instance.complete_summary("", [])
        if operation == "general":
            return await instance.complete_general("synthetic question", {})
        if operation == "verify":
            return await instance.verify_answer("synthetic answer", [])
        return "".join([part async for part in instance.stream_answer("synthetic question", [])])

    assert await asyncio.gather(call(), call()) == ["synthetic output"] * 2
    assert len(created) == 1 and created[0].calls == 2
    assert instance._manager is None
    owned = False
    with pytest.raises(RuntimeError, match="owner lost"):
        await call()
    assert created[0].calls == 2
    await instance.close()
    await instance.close()
    assert created[0].closed == 1
    with pytest.raises(RuntimeError, match="closed"):
        await call()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_answer_output_budget_supports_summary_without_expanding_engine_budget(stream):
    config = DashScopeConfig(
        region="cn-beijing", workspace_id="disposable-workspace",
        models={"answer": "qwen-flash", "engine": "qwen-plus", "summary": "qwen-max",
                "embedding": "text-embedding-v4", "rerank": "qwen3-vl-rerank"},
        embedding_dimension=1024, api_key=SecretStr("synthetic-secret"),
        credential_ciphertext_sha256="1" * 64,
    )
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        assert calls[-1]["max_tokens"] == 2048
        if stream:
            chunks = [{"choices": [{"index": 0, "delta": {"content": "synthetic"},
                                     "finish_reason": None}]},
                      {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
            body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
            return httpx.Response(200, text=body + "data: [DONE]\n\n",
                                  headers={"Content-Type": "text/event-stream"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "synthetic"},
                                                     "finish_reason": "stop"}]})

    async with DashScopeClient(config, transport=httpx.MockTransport(handler)) as client:
        messages = [Message("user", "synthetic")]
        if stream:
            assert "".join([part async for part in client.stream_complete(
                "qwen-flash", messages, max_tokens=2048)]) == "synthetic"
        else:
            assert (await client.complete("qwen-flash", messages, max_tokens=2048)).content
        with pytest.raises(ValueError):
            await client.complete("qwen-plus", messages, max_tokens=513)
        with pytest.raises(ValueError):
            await client.complete("qwen-flash", messages, max_tokens=2049)
    assert len(calls) == 1


def test_answer_prompt_separates_summary_from_exact_support_quotes():
    messages = RagRuntime._answer_messages(
        "synthetic overview", [{"id": "E1", "text": "x" * 5000}], {},
    )
    assert "最多1500字符" in messages[0].content
    assert "可忠实解释、概括" in messages[0].content
    assert "逐字复制对应证据" in messages[0].content


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_output_limit_is_a_specific_safe_answer_error(stream):
    config = DashScopeConfig(
        region="cn-beijing", workspace_id="disposable-workspace",
        models={"answer": "qwen-flash", "engine": "qwen-plus", "summary": "qwen-max",
                "embedding": "text-embedding-v4", "rerank": "qwen3-vl-rerank"},
        embedding_dimension=1024, api_key=SecretStr("synthetic-secret"),
        credential_ciphertext_sha256="1" * 64,
    )

    def handler(request):
        assert json.loads(request.content)["temperature"] == 0
        if stream:
            payload = {"choices": [{"index": 0, "delta": {"content": "synthetic fragment"},
                                    "finish_reason": "length"}]}
            return httpx.Response(200, text=f"data: {json.dumps(payload)}\n\ndata: [DONE]\n\n",
                                  headers={"Content-Type": "text/event-stream"})
        return httpx.Response(200, json={"choices": [{
            "message": {"content": "synthetic fragment"}, "finish_reason": "length",
        }]})

    async with DashScopeClient(config, transport=httpx.MockTransport(handler)) as client:
        class Runtime:
            async def complete_answer(self, *args):
                result = await client.complete("qwen-flash", [Message("user", "synthetic")],
                                               max_tokens=512)
                return result.content

            async def stream_answer(self, *args):
                async for fragment in client.stream_complete(
                    "qwen-flash", [Message("user", "synthetic")], max_tokens=512,
                ):
                    yield fragment

        adapter = LightRAGAnswerAdapter(Runtime())
        with pytest.raises(AnswerError, match="^answer_output_limit$"):
            if stream:
                _ = [fragment async for fragment in adapter.stream_with_context(
                    "synthetic", [], {},
                )]
            else:
                await adapter.answer_with_context("synthetic", [], {})
