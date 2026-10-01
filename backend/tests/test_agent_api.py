"""Public synthetic Agent regressions using the actual API and isolated business records."""

import asyncio
from uuid import uuid4

import pytest
from test_postgres_local import environment  # noqa: F401

pytest_plugins = ["test_postgres_local"]
pytestmark = pytest.mark.postgres


class SyntheticAnswerer:
    requires_support_verification = True

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.inputs = []

    async def route_with_context(self, *args):
        return '{"mode":"general"}'

    async def agent_decision(self, question, evidence, context, tools, results, general):
        self.inputs.append(
            {
                "question": question,
                "evidence": evidence,
                "context": context,
                "tools": tools,
                "results": results,
                "general": general,
            }
        )
        return self.decisions.pop(0)


async def ready(api, decisions):
    from langgraph.checkpoint.memory import InMemorySaver

    from app.agent.runner import AgentRunner

    app = api._transport.app
    app.state.answer_enabled = True
    app.state.agent_enabled = True
    app.state.answer_adapter = SyntheticAnswerer(decisions)
    app.state.agent_runtime = AgentRunner(app, InMemorySaver())
    chat = (await api.post("/api/conversations", json={"kb_id": None})).json()["id"]
    return app, chat, f"/api/conversations/{chat}/agent-runs"


async def settled(api, path, run):
    for _ in range(100):
        response = await api.get(f"{path}/{run}")
        assert response.status_code == 200, response.text
        data = response.json()
        if data["status"] != "running":
            return data
        await asyncio.sleep(0.02)
    pytest.fail("Synthetic Agent did not settle")


async def test_real_local_tool_and_direct_answer_reuse_answer_service(environment):  # noqa: F811
    api, database = environment
    app, chat, path = await ready(
        api,
        [
            {"action": "call_tool", "tool_id": "local.time", "arguments": {}},
            {"action": "finish", "answer": {"text": "已读取本机时间。"}},
        ],
    )

    class ForbiddenRetriever:
        async def retrieve(self, *args):
            pytest.fail("Ordinary Agent must never call LightRAG")

    app.state.query_adapter = ForbiddenRetriever()
    try:
        body = {"client_message_id": str(uuid4()), "text": "当前几点？"}
        accepted = await api.post(path, json=body)
        assert accepted.status_code == 202, accepted.text
        run = accepted.json()["id"]
        result = await settled(api, path, run)
        assert result["status"] == "completed", result
        assert app.state.answer_adapter.inputs[-1]["results"][0]["data"]["time"]
        messages = (await api.get(f"/api/conversations/{chat}/messages")).json()["items"]
        assert len(messages) == 1 and messages[0]["status"] == "answered"
        assert messages[0]["citations"] == [] and messages[0]["route"] == "general"
        assert (await api.post(path, json=body)).json()["id"] == run
        assert (await api.post(path, json={**body, "text": "changed"})).status_code == 409
        events = await api.get(f"{path}/{run}/events")
        assert events.status_code == 200 and "event: answer_saved" in events.text
        assert "event: tool_result" in events.text and "event: snapshot" in events.text
        assert "event: tool_requested" in events.text
    finally:
        await app.state.agent_runtime.close()


async def test_waiting_input_is_durable_and_blocks_new_messages(environment):  # noqa: F811
    api, _ = environment
    app, chat, path = await ready(
        api,
        [
            {
                "action": "request_input",
                "prompt": "持续多少分钟？",
                "fields": {"minutes": "integer"},
            },
            {"action": "finish", "answer": {"text": "安排三十分钟。"}},
        ],
    )
    try:
        accepted = await api.post(
            path, json={"client_message_id": str(uuid4()), "text": "安排学习"}
        )
        assert accepted.status_code == 202, accepted.text
        run = accepted.json()["id"]
        waiting = await settled(api, path, run)
        assert waiting["status"] == "waiting_input"
        blocked = await api.post(
            f"/api/conversations/{chat}/messages",
            json={"client_message_id": str(uuid4()), "text": "另一个问题", "mode": "auto"},
        )
        assert blocked.status_code == 409
        response = await api.post(
            f"{path}/{run}/resume",
            json={
                "request_id": str(uuid4()),
                "generation": waiting["generation"],
                "input": {"minutes": 30},
            },
        )
        assert response.status_code == 202, response.text
        assert (await settled(api, path, run))["status"] == "completed"
        assert app.state.answer_adapter.inputs[-1]["context"]["task_supplements"] == [
            {"minutes": 30}
        ]
    finally:
        await app.state.agent_runtime.close()


async def test_ordinary_model_cannot_request_knowledge_tool(environment):  # noqa: F811
    api, _ = environment
    app, _, path = await ready(
        api, [{"action": "call_tool", "tool_id": "kb.documents", "arguments": {}}]
    )
    try:
        response = await api.post(
            path, json={"client_message_id": str(uuid4()), "text": "查另一个库"}
        )
        assert response.status_code == 202, response.text
        result = await settled(api, path, response.json()["id"])
        assert result["status"] == "failed" and result["error_code"] == "tool_scope_forbidden"
        assert [item["id"] for item in app.state.answer_adapter.inputs[0]["tools"]] == [
            "local.time"
        ]
    finally:
        await app.state.agent_runtime.close()


async def test_approval_rebuild_and_duplicate_resume_execute_once(environment):  # noqa: F811
    from app.agent.runner import AgentRunner
    from app.tools.gateway import ToolDefinition

    api, _ = environment
    app, _, path = await ready(
        api,
        [
            {"action": "call_tool", "tool_id": "test.write", "arguments": {}},
            {"action": "finish", "answer": {"text": "合成操作已执行。"}},
        ],
    )
    executed = []

    async def operation(session, context, args):
        executed.append(True)
        return {"synthetic": "completed"}

    app.state.tool_registry["test.write"] = ToolDefinition(
        "test.write",
        "合成敏感操作",
        "any",
        True,
        "只用于隔离验证",
        lambda x: {} if x == {} else None,
        operation,
        effect="write",
    )
    try:
        response = await api.post(
            path, json={"client_message_id": str(uuid4()), "text": "执行合成"}
        )
        run = response.json()["id"]
        waiting = await settled(api, path, run)
        assert waiting["status"] == "waiting_approval" and not executed
        previous = app.state.agent_runtime
        await previous.close()
        app.state.agent_runtime = AgentRunner(app, previous.saver)
        body = {
            "request_id": str(uuid4()),
            "generation": waiting["generation"],
            "input": {"approve": True},
        }
        approved, repeated = await asyncio.gather(
            api.post(f"{path}/{run}/resume", json=body), api.post(f"{path}/{run}/resume", json=body)
        )
        assert approved.status_code == 202 and repeated.status_code == 202
        assert (await settled(api, path, run))["status"] == "completed"
        assert executed == [True]
        assert (
            await api.post(f"{path}/{run}/resume", json={**body, "input": {"approve": False}})
        ).status_code == 409
    finally:
        await app.state.agent_runtime.close()


async def test_cancel_stops_actual_task_and_late_completion_cannot_save(environment):  # noqa: F811
    api, _ = environment
    app, chat, path = await ready(api, [])
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def blocked(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    app.state.answer_adapter.agent_decision = blocked
    try:
        response = await api.post(
            path, json={"client_message_id": str(uuid4()), "text": "持续任务"}
        )
        assert response.status_code == 202
        run = response.json()["id"]
        await asyncio.wait_for(entered.wait(), 2)
        result = await api.post(f"{path}/{run}/cancel")
        assert result.json()["status"] == "cancelled"
        assert cancelled.is_set() and not app.state.agent_runtime.tasks
        messages = (await api.get(f"/api/conversations/{chat}/messages")).json()["items"]
        assert messages[-1]["status"] == "interrupted" and not messages[-1]["citations"]
        assert (
            await api.post(
                f"{path}/{run}/resume",
                json={
                    "request_id": str(uuid4()),
                    "generation": result.json()["generation"],
                    "input": {},
                },
            )
        ).status_code == 409
    finally:
        await app.state.agent_runtime.close()
