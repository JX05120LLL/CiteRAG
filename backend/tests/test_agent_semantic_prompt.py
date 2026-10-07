"""Offline planning prompt regression; no model or supplier calls."""

import json
from types import SimpleNamespace

import pytest
from pydantic import SecretStr


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
