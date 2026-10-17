"""Offline planning prompt regression; no model or supplier calls."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import SecretStr


@pytest.mark.parametrize("entrypoint", [
    "ordinary", "voice", "agent_ordinary", "knowledge", "agent_knowledge",
])
async def test_answer_quality_policy_reaches_every_generation_path(monkeypatch, entrypoint):
    from app.rag.runtime import RagRuntime

    captured = []

    class Client:
        async def complete(self, _model, messages, **_options):
            captured.append(messages)
            if len(captured) == 2:
                return SimpleNamespace(content=json.dumps({
                    "version": 2, "accepted": True, "issues": [], "revision": None,
                    "checks": [{"requirement_id": name, "verdict": "met",
                                "quote": "公开合成回应", "reason": "离线协议替身"}
                               for name in ("request", "scope", "precision")],
                }, ensure_ascii=False))
            return SimpleNamespace(content='{"text":"公开合成回应"}')

        async def stream_complete(self, _model, messages, **_options):
            captured.append(messages)
            yield "公开合成回应"

    async def client(_self):
        return Client()

    monkeypatch.setattr(RagRuntime, "_get_client", client)
    runtime = object.__new__(RagRuntime)
    question = "我只剩15分钟，如何继续？"
    context = {"turns": [{"user": "请帮我整理读书笔记，简短些", "assistant": "先选一页"}]}
    evidence = [{"id": "E1", "text": "公开合成比例从20%变为25%。"}]
    if entrypoint == "ordinary":
        await runtime.complete_general(question, context)
    elif entrypoint == "voice":
        assert "".join([part async for part in runtime.stream_general(question, context)])
    elif entrypoint.startswith("agent"):
        await runtime.complete_agent(question, evidence, context, [], [],
                                     entrypoint == "agent_ordinary")
    else:
        await runtime.complete_answer(question, evidence, context)
    assert len(captured) == (2 if entrypoint in {"ordinary", "voice"} else 1)
    system = captured[0][0].content
    for requirement in ("可执行", "限制", "无关比较", "适用条件", "百分点", "旧值",
                        "可检查产物", "时间分配", "口号"):
        assert requirement in system
    payload = json.loads(captured[0][1].content)
    assert payload["question"] == question
    assert payload["conversation_context"] == context


@pytest.mark.parametrize("agent", [False, True])
async def test_knowledge_generation_selects_integer_spans_without_copying_hashes(
    monkeypatch, agent,
):
    """Generation chooses a local ordinal; current identity is restored by the server."""
    from app.rag.evidence_spans import expand_span_support
    from app.rag.runtime import RagRuntime

    captured = []

    class Client:
        async def complete(self, _model, messages, **_options):
            captured.append(messages)
            return SimpleNamespace(content='{}')

    async def client(_self):
        return Client()

    monkeypatch.setattr(RagRuntime, "_get_client", client)
    runtime = object.__new__(RagRuntime)
    evidence = [{"id": "E1", "text": "新规范允许42℃。\n\n试行记录写47℃，尚未批准。"}]
    if agent:
        await runtime.complete_agent("逐行比较温度并说明来源。", evidence, {}, [], [], False)
    else:
        await runtime.complete_answer("逐行比较温度并说明来源。", evidence, {})
    payload = json.loads(captured[0][1].content)
    item = payload["evidence"][0]
    assert "text" not in item and len(item["spans"]) == 2
    assert item["spans"] == [
        {"span_index": 1, "text": "新规范允许42℃。\n\n"},
        {"span_index": 2, "text": "试行记录写47℃，尚未批准。"}]
    assert "span_index" in captured[0][0].content and "整数" in captured[0][0].content
    assert "span_id" not in captured[0][0].content
    assert "不生成引用标号" not in captured[0][0].content
    assert "conflicting_evidence且三者留空" not in captured[0][0].content
    raw = json.dumps({"status": "answered", "text": "现规范42℃。", "evidence_ids": ["E1"],
                      "support": [{"evidence_id": "E1",
                                   "span_index": item["spans"][0]["span_index"]}]})
    canonical = json.loads(expand_span_support(raw, evidence))
    assert canonical["support"] == [{"evidence_id": "E1", "quote": "新规范允许42℃。\n\n"}]


async def test_fact_verifier_checks_percentage_units_and_scopes_gap_notes(monkeypatch):
    from app.rag.runtime import RagRuntime

    captured = []

    class Client:
        async def complete(self, _model, messages, **_options):
            captured.append(messages)
            payload = json.loads(messages[1].content)
            return SimpleNamespace(content=json.dumps({"units": [
                {"unit_id": unit["id"], "verdict": "unsupported", "support": []}
                for unit in payload["answer_units"]]}))

    async def client(_self):
        return Client()

    monkeypatch.setattr(RagRuntime, "_get_client", client)
    evidence = [{"id": "E1", "text": "比例从20%到25%。"}]
    await object.__new__(RagRuntime).verify_answer("增加5%，并且未找到售价。", evidence)
    system = captured[0][0].content
    assert all(term in system for term in ("百分点", "旧值", "缺项"))
    from app.rag.evidence_spans import model_evidence

    assert json.loads(captured[0][1].content)["evidence"] == model_evidence(evidence)


@pytest.mark.parametrize("general", [True, False])
async def test_agent_finish_example_is_a_complete_nested_object(monkeypatch, general):
    from app.rag.runtime import RagRuntime

    captured = {}

    class Client:
        async def complete(self, _model, messages, **options):
            captured.update(system=messages[0].content, options=options)
            return SimpleNamespace(content='{"action":"finish","answer":{"text":"ok"}}')

    async def client(_self):
        return Client()

    monkeypatch.setattr(RagRuntime, "_get_client", client)
    await object.__new__(RagRuntime).complete_agent("公开合成问题", [], {}, [], [], general)
    example = json.loads(captured["system"].split("最终答案结构示例：", 1)[1].splitlines()[0])
    assert example["action"] == "finish" and isinstance(example["answer"], dict)
    assert set(example["answer"]) == (
        {"text"} if general else {"status", "text", "evidence_ids", "support"}
    )
    assert "不得把 answer 再编码成字符串" in captured["system"]
    assert "前述回答格式的JSON对象" not in captured["system"]
    assert captured["options"]["response_format"] == "json_object"
    if not general:
        assert example["answer"]["support"] == [{"evidence_id": "E1", "span_index": 1}]
        assert "[E1]" in example["answer"]["text"]


async def test_agent_prompt_uses_supplied_parameters_and_available_catalog(monkeypatch):
    from app.rag.runtime import RagRuntime

    captured = {}

    class FakeClient:
        async def complete(self, _model, messages, **_kwargs):
            captured["system"] = messages[0].content
            captured["user"] = messages[-1].content
            return SimpleNamespace(content='{"action":"finish","answer":{"text":"ok"}}')

    async def fake_client(_self):
        return FakeClient()

    monkeypatch.setattr(RagRuntime, "_get_client", fake_client)
    runtime = object.__new__(RagRuntime)
    await runtime.complete_agent(
        "What is the weather in Hanzhong today?", [],
        {"task_supplements": [{"reply_text": "Hanzhong City, Hantai District"}]},
        [], [], True,
    )
    assert "Hanzhong" in captured["user"]
    assert "task_supplements" in captured["user"]
    assert "已经提供" in captured["system"]
    assert "没有匹配工具" in captured["system"]


async def test_general_agent_six_tools_and_two_weather_results_fit_bounded_input(
    monkeypatch,
):
    from app.config import Settings
    from app.main import create_app
    from app.rag.runtime import RagRuntime
    from app.services.token_budget import ANSWER_INPUT_TOKENS, estimate_messages

    settings = Settings(
        qweather_enabled=True,
        qweather_api_host="synthetic.weather.qweatherapi.com",
        qweather_api_key=SecretStr("synthetic-key-do-not-use"),
    )
    registry = create_app(settings).state.tool_registry
    assert len(registry) == 6
    tools = [
        {"id": spec.id, "title": spec.title, "scope": spec.scope,
         "approval_required": spec.approval_required, "impact": spec.impact,
         "version": spec.version, "input_schema": spec.input_schema,
         "effect": spec.effect, "backend": spec.backend,
         "destination": spec.destination}
        for spec in registry.values()
    ]
    # The real Hanzhong failure happened after city_search and current
    # succeeded. This fixture keeps the same shape without storing user data.
    results = [
        {"call_id": f"synthetic-{index}", "tool_id": tool_id,
         "status": "succeeded", "source_type": "tool",
         "data": {"public_weather_sample": "synthetic weather data " * 48}}
        for index, tool_id in enumerate(("weather.city_search", "weather.current"))
    ]
    captured = {}

    class FakeClient:
        async def complete(self, _model, messages, **_kwargs):
            captured["messages"] = messages
            return SimpleNamespace(content='{"action":"finish","answer":{"text":"ok"}}')

    async def fake_client(_self):
        return FakeClient()

    monkeypatch.setattr(RagRuntime, "_get_client", fake_client)
    runtime = object.__new__(RagRuntime)
    await runtime.complete_agent(
        "你好，能听到吗？", [], {"turns": [{"role": "user", "text": "x" * 3000}]},
        tools, results, True,
    )
    messages = captured["messages"]
    assert estimate_messages(messages) <= ANSWER_INPUT_TOKENS
    payload = json.loads(messages[1].content)
    assert payload["question"] == "你好，能听到吗？"
    assert payload["conversation_context"]["turns"] == []
    assert len(payload["allowed_tools"]) == 6
    assert {tool["id"] for tool in payload["allowed_tools"]} == {
        tool["id"] for tool in tools
    }
    assert all("input_schema" in tool for tool in payload["allowed_tools"])
    assert len(payload["tool_results"]) == 2


async def test_agent_still_rejects_oversized_current_question(monkeypatch):
    from app.rag.runtime import RagRuntime
    from app.services.token_budget import TokenBudgetExceeded

    async def fake_client(_self):
        pytest.fail("An oversized question must be rejected before any model call")

    monkeypatch.setattr(RagRuntime, "_get_client", fake_client)
    runtime = object.__new__(RagRuntime)
    with pytest.raises(TokenBudgetExceeded):
        await runtime.complete_agent("问题" * 5000, [], {}, [], [], True)


async def test_knowledge_spans_and_full_local_catalog_fit_realistic_evidence_budget(monkeypatch):
    from app.config import Settings
    from app.main import create_app
    from app.rag.runtime import RagRuntime
    from app.services.token_budget import ANSWER_INPUT_TOKENS, estimate_messages

    captured = []

    class Client:
        async def complete(self, _model, messages, **_options):
            captured.append(messages)
            return SimpleNamespace(content='{}')

    async def client(_self):
        return Client()

    monkeypatch.setattr(RagRuntime, "_get_client", client)
    evidence = [{"id": f"E{index}", "text": (
        "公开合成规范条件仅用于检查输入预算，不对应真实设备。" * 2 + "\n\n") * 6}
        for index in range(1, 4)]
    registry = create_app(Settings()).state.tool_registry
    tools = [{"id": spec.id, "title": spec.title, "impact": spec.impact,
              "input_schema": spec.input_schema} for spec in registry.values()]
    question = "请逐项对比当前有效规范与历史记录并说明来源，保留所有对象、数值与条件。"
    await object.__new__(RagRuntime).complete_agent(question, evidence, {}, tools, [], False)
    assert estimate_messages(captured[0]) <= ANSWER_INPUT_TOKENS
    payload = json.loads(captured[0][1].content)
    assert payload["question"] == question
    assert len(payload["evidence"]) == 3 and len(payload["allowed_tools"]) == len(registry)
    for original, catalog in zip(evidence, payload["evidence"], strict=True):
        assert "".join(span["text"] for span in catalog["spans"]) == original["text"]
        assert [span["span_index"] for span in catalog["spans"]] == list(
            range(1, len(catalog["spans"]) + 1))
        assert all(set(span) == {"span_index", "text"} for span in catalog["spans"])


@pytest.mark.parametrize("general", [True, False])
@pytest.mark.parametrize("outcome", ["missing", "failed", "rejected", "truncated"])
async def test_agent_realtime_prompt_preserves_clock_and_failed_tool_state(
    monkeypatch, general, outcome,
):
    """Prompt contract coverage; it does not certify a real model's answer quality."""
    import app.rag.runtime as module

    zone = timezone(timedelta(hours=8))

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 10, 12, 0, 15, tzinfo=zone)
            return value if tz is None else value.astimezone(tz)

        def astimezone(self, tz=None):
            return self if tz is None else super().astimezone(tz)

    monkeypatch.setattr(module, "datetime", FrozenDateTime, raising=False)
    captured = []

    class Client:
        async def complete(self, _model, messages, **_options):
            captured.append(messages)
            return SimpleNamespace(content='{"action":"finish","answer":{"text":"ok"}}')

    async def client(_self):
        return Client()

    monkeypatch.setattr(module.RagRuntime, "_get_client", client)
    runtime = object.__new__(module.RagRuntime)
    tools = [] if outcome == "missing" else [{
        "id": "weather.current", "title": "公开合成实时天气",
        "impact": "Synthetic only, no network", "input_schema": {"type": "object"},
    }]
    results = [] if outcome == "missing" else [{
        "tool_id": "weather.current",
        "status": "succeeded" if outcome == "truncated" else outcome,
        "error_code": "weather_auth_failed" if outcome == "failed" else None,
        "truncated": outcome == "truncated", "data": None,
    }]
    await runtime.complete_agent(
        "今天是几号？北京天气如何？", [],
        {"server_clock": {"local_date": "2024-06-28"}}, tools, results, general,
    )
    assert len(captured) == 1
    system = captured[0][0].content
    assert "服务端可信时钟：" in system
    clock = json.loads(system.split("服务端可信时钟：", 1)[1].splitlines()[0])
    assert clock["local_date"] == "2026-10-12"
    assert clock["local_time"].endswith("+08:00")
    assert clock["utc_time"].startswith("2026-10-11")
    assert "2024-06-28" not in system
    assert "不是工具调用" in system
    assert "本机时钟，未联网校准" in system
    assert "天气等实时外部事实必须" in system
    assert "status=succeeded" in system and "未截断" in system
    assert "缺少匹配工具" in system and "具体失败原因" in system
    assert "不得编造" in system
    payload = json.loads(captured[0][1].content)
    assert payload["tool_results"] == results
    assert {tool["id"] for tool in payload["allowed_tools"]} == {
        tool["id"] for tool in tools
    }
