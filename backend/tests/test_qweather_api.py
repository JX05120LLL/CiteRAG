"""Actual tool/Agent APIs and PostgreSQL with public synthetic HTTP, never real weather calls."""

import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from test_agent_api import ready, settled
from test_postgres_local import environment, seed_knowledge_base  # noqa: F401
from test_qweather_tools import ATTRIBUTION, HOST, KEY, configured, current_response

from app.models import AgentEvent, ToolCall
from app.tools.qweather import qweather_tools

pytest_plugins = ["test_postgres_local"]
pytestmark = pytest.mark.postgres


def approval_weather(transport):
    """Keep explicit-approval gateway regressions separate from weather's default policy."""
    return {name: replace(spec, approval_required=True)
            for name, spec in qweather_tools(configured(), transport=transport).items()}


def test_exact_city_fallback_does_not_turn_a_forecast_into_current_weather():
    from app.agent.hooks import RunHooks

    decision = {"action": "request_input", "prompt": "Which county?",
                "fields": {"location": "string"}}
    results = [{"status": "succeeded", "tool_id": "weather.city_search", "data": {
        "kind": "city_search", "requires_selection": False, "exact_match": True,
        "resolved_candidate": {"latitude": 33.07767, "longitude": 107.02862},
    }}]
    question = "\u6c49\u4e2d\u4eca\u5929\u8d77\u672a\u6765\u4e09\u5929\u5929\u6c14\u9884\u62a5"
    assert RunHooks._resolved_current_weather(decision, question, results) == decision


@pytest.fixture(autouse=True)
def forbid_private_rag_configuration(monkeypatch):
    async def no_engine(self, assert_owner):
        assert_owner()
        return self

    monkeypatch.setattr("app.rag.runtime.RagRuntime.start", no_engine)


@pytest.mark.parametrize("knowledge", [False, True])
async def test_weather_approval_scope_idempotency_and_durable_result(environment, knowledge):  # noqa: F811
    api, database = environment
    kb = (await seed_knowledge_base(database, "Synthetic weather KB", "ready"))[0] if (
        knowledge
    ) else None
    chat = (await api.post("/api/conversations", json={
        "kb_id": str(kb) if kb else None})).json()["id"]
    calls = []

    async def respond(request):
        calls.append(request)
        return httpx.Response(200, json=current_response())

    api._transport.app.state.tool_registry.update(approval_weather(httpx.MockTransport(respond)))
    path = f"/api/conversations/{chat}/tools"
    catalog = (await api.get(path)).json()["items"]
    assert len([item for item in catalog if item["id"].startswith("weather.")]) == 3
    assert KEY not in json.dumps(catalog) and HOST not in json.dumps(catalog)
    body = {"request_id": str(uuid4()), "tool_id": "weather.current",
            "arguments": {"latitude": 30.245, "longitude": 120.125}}
    pending = (await api.post(f"{path}/calls", json=body)).json()
    assert pending["status"] == "pending_approval" and pending["result"] is None and calls == []
    assert pending["arguments"] == {"latitude": 30.25, "longitude": 120.13}
    assert (await api.post(f"{path}/calls", json=body)).json() == pending
    changed = await api.post(f"{path}/calls", json={
        **body, "arguments": {"latitude": 30.31, "longitude": 120.13},
    })
    assert changed.status_code == 409 and calls == []
    approved = await api.post(f"{path}/calls/{pending['id']}/decision", json={"approve": True})
    result = approved.json()
    assert result["status"] == "succeeded" and result["source_type"] == "tool"
    assert result["result"]["attributions"] == [ATTRIBUTION] and len(calls) == 1
    assert KEY not in approved.text and HOST not in approved.text
    replay = await api.post(f"{path}/calls", json=body)
    assert replay.json() == result and len(calls) == 1
    duplicate = await api.post(f"{path}/calls/{pending['id']}/decision", json={"approve": True})
    assert duplicate.status_code == 409
    rejected = (await api.post(f"{path}/calls", json={**body, "request_id": str(uuid4())})).json()
    refusal = await api.post(f"{path}/calls/{rejected['id']}/decision", json={"approve": False})
    assert refusal.json()["status"] == "rejected" and len(calls) == 1
    invalid = await api.post(f"{path}/calls", json={
        **body, "request_id": str(uuid4()), "arguments": {"latitude": 91, "longitude": 0},
    })
    assert invalid.status_code == 422 and len(calls) == 1
    async with database.sessions() as session:
        saved = await session.scalar(select(ToolCall).where(ToolCall.id == pending["id"]))
        assert saved.result == result["result"] and saved.kb_id == kb


async def test_failed_weather_is_persisted_safely_and_is_not_a_success(environment):  # noqa: F811
    api, database = environment
    chat = (await api.post("/api/conversations", json={"kb_id": None})).json()["id"]
    api._transport.app.state.tool_registry.update(qweather_tools(
        configured(), transport=httpx.MockTransport(
            lambda _request: httpx.Response(401, json={"error": {"detail": KEY}}))))
    path = f"/api/conversations/{chat}/tools/calls"
    response = await api.post(path, json={"request_id": str(uuid4()),
                              "tool_id": "weather.current",
                              "arguments": {"latitude": 0, "longitude": 0}})
    assert response.json()["status"] == "failed"
    assert response.json()["error_code"] == "weather_auth_failed"
    assert response.json()["result"] is None and KEY not in response.text
    async with database.sessions() as session:
        saved = await session.scalar(select(ToolCall))
        assert saved.status == "failed" and saved.error_code == "weather_auth_failed"
        assert saved.result is None


@pytest.mark.parametrize("knowledge", [False, True])
async def test_agent_city_then_weather_uses_one_history_and_no_kb_citations(environment, knowledge):  # noqa: F811
    api, database = environment
    app, chat, path = await ready(api, [
        {"action": "call_tool", "tool_id": "weather.city_search",
         "arguments": {"location": "杭州"}},
        {"action": "call_tool", "tool_id": "weather.current",
         "arguments": {"latitude": 30.25, "longitude": 120.13}},
        {"action": "finish", "answer": {"text": "合成天气结果：杭州少云，25 °C。"}},
    ])
    if knowledge:
        kb, _ = await seed_knowledge_base(database, "Synthetic weather library", "ready")
        chat = (await api.post("/api/conversations", json={"kb_id": str(kb)})).json()["id"]
        path = f"/api/conversations/{chat}/agent-runs"
    requests = []

    async def respond(request):
        requests.append(request)
        if request.url.path == "/geo/v2/city/lookup":
            return httpx.Response(200, json={"code": "200", "location": [
                {"id": "synthetic-HZ", "name": "杭州", "lat": "30.25", "lon": "120.13"},
            ], "refer": {"sources": [ATTRIBUTION]}})
        return httpx.Response(200, json=current_response())

    class ForbiddenRetriever:
        async def retrieve(self, *_args):
            pytest.fail("General external-weather answer must not query LightRAG")

    app.state.query_adapter = ForbiddenRetriever()
    app.state.tool_registry.update(approval_weather(httpx.MockTransport(respond)))
    try:
        accepted = await api.post(path, json={"client_message_id": str(uuid4()),
                                              "text": "合成问题：杭州现在天气怎么样？"})
        assert accepted.status_code == 202, accepted.text
        run = accepted.json()["id"]
        for count in range(2):
            waiting = await settled(api, path, run)
            assert waiting["status"] == "waiting_approval", waiting
            assert len(requests) == count
            resumed = await api.post(f"{path}/{run}/resume", json={"request_id": str(uuid4()),
                "generation": waiting["generation"], "input": {"approve": True}})
            assert resumed.status_code == 202, resumed.text
        completed = await settled(api, path, run)
        assert completed["status"] == "completed", completed
        assert completed["tool_attempts"] == 2 and len(requests) == 2
        model_results = app.state.answer_adapter.inputs[-1]["results"]
        assert all(item["source_type"] == "tool" for item in model_results)
        assert model_results[-1]["data"]["current"]["temperature"]["value"] == 25
        history = (await api.get(f"/api/conversations/{chat}/messages")).json()["items"]
        assert len(history) == 1 and history[0]["saved"] is True
        assert history[0]["route"] == "general" and history[0]["citations"] == []
        assert history[0]["text"] == "合成天气结果：杭州少云，25 °C。"
        async with database.sessions() as session:
            events = list(await session.scalars(select(AgentEvent).where(AgentEvent.run_id == run)))
            serialized = json.dumps([event.payload for event in events], default=str)
            assert KEY not in serialized and HOST not in serialized
    finally:
        await app.state.agent_runtime.close()


async def test_agent_cancel_stops_weather_http_and_persists_interruption(environment):  # noqa: F811
    api, database = environment
    app, _chat, path = await ready(api, [{"action": "call_tool", "tool_id": "weather.current",
                                         "arguments": {"latitude": 0, "longitude": 0}}])
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def waiting(_request):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    app.state.tool_registry.update(approval_weather(httpx.MockTransport(waiting)))
    try:
        run = (await api.post(path, json={"client_message_id": str(uuid4()),
                      "text": "合成问题：查询零度坐标天气"})).json()["id"]
        paused = await settled(api, path, run)
        assert paused["status"] == "waiting_approval"
        approved = await api.post(f"{path}/{run}/resume", json={"request_id": str(uuid4()),
            "generation": paused["generation"], "input": {"approve": True}})
        assert approved.status_code == 202
        await asyncio.wait_for(started.wait(), 2)
        stopped = await api.post(f"{path}/{run}/cancel")
        assert stopped.status_code == 200 and stopped.json()["status"] == "cancelled"
        assert cancelled.is_set()
        async with database.sessions() as session:
            call = await session.scalar(select(ToolCall))
            assert call.status == "interrupted" and call.result is None
        assert (await api.get(f"{path}/{run}")).json()["status"] == "cancelled"
    finally:
        await app.state.agent_runtime.close()


async def test_exact_city_weather_completes_without_approval_or_repeated_clarification(environment):  # noqa: F811
    api, database = environment
    app, chat, path = await ready(api, [
        {"action": "call_tool", "tool_id": "weather.city_search",
         "arguments": {"location": "\u6c49\u4e2d"}},
        {"action": "request_input", "prompt": "Which county?",
         "fields": {"location": "string"}},
        {"action": "finish", "answer": {"text": "Synthetic Hanzhong weather."}},
    ])
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == "/geo/v2/city/lookup":
            return httpx.Response(200, json={"code": "200", "location": [
                {"id": "city", "name": "\u6c49\u4e2d", "lat": "33.07767",
                 "lon": "107.02862", "adm1": "\u9655\u897f\u7701",
                 "adm2": "\u6c49\u4e2d", "country": "\u4e2d\u56fd"},
                {"id": "county", "name": "\u7565\u9633", "lat": "33.32964",
                 "lon": "106.1539", "adm1": "\u9655\u897f\u7701",
                 "adm2": "\u6c49\u4e2d", "country": "\u4e2d\u56fd"},
            ], "refer": {"sources": [ATTRIBUTION]}})
        assert request.url.path == "/weather/v1/current/33.08/107.03"
        return httpx.Response(200, json=current_response())

    app.state.tool_registry.update(qweather_tools(
        configured(), transport=httpx.MockTransport(respond)))
    try:
        accepted = await api.post(path, json={
            "client_message_id": str(uuid4()),
            "text": "\u6c49\u4e2d\u4eca\u5929\u4ec0\u4e48\u5929\u6c14",
        })
        assert accepted.status_code == 202, accepted.text
        run = await settled(api, path, accepted.json()["id"])
        assert run["status"] == "completed", run
        assert run["tool_attempts"] == 2 and run["model_rounds"] == 3
        assert len(requests) == 2
        async with database.sessions() as session:
            calls = list(await session.scalars(
                select(ToolCall).where(ToolCall.run_id == run["id"])
            ))
            assert [call.tool_id for call in calls] == ["weather.city_search", "weather.current"]
            assert all(call.status == "succeeded" and call.approved_at is None for call in calls)
    finally:
        await app.state.agent_runtime.close()
