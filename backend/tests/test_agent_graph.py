"""Real LangGraph with synthetic decisions and locally executed functions."""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest


def engine():
    from app.agent import graph

    assert hasattr(graph, "build_graph")
    return graph


class Hooks:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.executed, self.seen = [], []

    async def guard(self, state):
        pass

    async def plan(self, state):
        self.seen.append(state.get("results", []))
        return self.decisions.pop(0)

    async def prepare(self, state):
        decision = state["decision"]
        if decision["action"] == "request_input":
            return {"kind": "input", "prompt": decision["prompt"]}
        if decision.get("tool_id") == "test.write":
            return {"kind": "approval", "call_id": "synthetic", "arguments": {}}
        return None

    async def execute(self, state):
        self.executed.append(state["decision"]["tool_id"])
        return {
            "status": "succeeded",
            "data": {"number": len(self.executed)},
            "source_type": "tool",
        }

    async def finish(self, state):
        return state["decision"]["answer"]


async def test_two_tools_feed_results_back_without_second_formal_question():
    graph = engine()
    hooks = Hooks(
        [
            {"action": "call_tool", "tool_id": "test.read", "arguments": {}},
            {"action": "call_tool", "tool_id": "test.read2", "arguments": {}},
            {"action": "finish", "answer": {"text": "done"}},
        ]
    )
    result = await graph.build_graph(hooks).ainvoke(graph.initial_state(str(uuid4())))
    assert hooks.executed == ["test.read", "test.read2"]
    assert len(hooks.seen[-1]) == 2
    assert result["answer"] == {"text": "done"}


async def test_direct_answer_has_one_model_round_and_no_tool():
    graph = engine()
    hooks = Hooks([{"action": "finish", "answer": {"text": "你好"}}])
    result = await graph.build_graph(hooks).ainvoke(graph.initial_state(str(uuid4())))
    assert result["model_rounds"] == 1
    assert not hooks.executed


async def test_encoded_finish_is_normalized_before_journal_and_checkpoint(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver

    from app.agent import hooks as module
    from app.models import AgentStep, ToolCall
    from app.rag.task_constraints import make_task_contract

    answer = {"text": "公开合成回答"}
    expected = {"action": "finish", "answer": answer}
    run_id, runner_id, owner_id, conversation_id = (uuid4() for _ in range(4))
    run = SimpleNamespace(
        id=run_id, generation=1, graph_version="citerag-agent-1", active_ms=0,
        voice_session_id=None, model_rounds=0, event_seq=0,
        owner_id=owner_id, conversation_id=conversation_id,
        prepared={"question": "公开合成问题", "evidence": [], "context": {
            "_task_contract": make_task_contract("公开合成问题")}, "general": True},
    )
    journal, model_calls, catalog_reads, reviews = [], [], [], []

    class Session:
        async def scalar(self, query):
            key = query.compile().params["key_1"]
            return next((item for item in journal
                         if isinstance(item, AgentStep) and item.key == key), None)

        async def get(self, model, identifier):
            assert model is AgentStep
            return next(item for item in journal if isinstance(item, AgentStep)
                        and item.id == identifier)

        def add(self, value):
            if isinstance(value, AgentStep):
                value.id = uuid4()  # An actual DB assigns this default when flushing.
            journal.append(value)

        async def commit(self):
            pass

    @asynccontextmanager
    async def sessions():
        yield Session()

    async def fenced(_session, identifier, generation, runner):
        assert (identifier, generation, runner) == (run_id, 1, runner_id)
        return run

    class Gateway:
        def __init__(self, _session, registry):
            assert registry == {}

        async def catalog(self, owner, conversation):
            catalog_reads.append((owner, conversation))
            return []

    async def decision(*args):
        model_calls.append(args)
        return json.dumps({"action": "finish", "answer": json.dumps(answer)})

    async def review(*args, **kwargs):
        assert kwargs == {"response_channel": "text"}
        reviews.append(args)
        return json.dumps({"version": 2, "accepted": True, "issues": [], "revision": None,
            "checks": [{"requirement_id": key, "verdict": "met", "quote": answer["text"],
                        "reason": "完整合成覆盖。"} for key in ("request", "scope", "precision")]})

    monkeypatch.setattr(module, "fenced", fenced)
    monkeypatch.setattr(module, "ToolGateway", Gateway)
    runner = SimpleNamespace(
        id=runner_id, db=SimpleNamespace(sessions=sessions), registry={},
        assert_owned=lambda: None,
    )
    hooks = module.RunHooks(runner, run_id, 1, SimpleNamespace(
        agent_decision=decision, review_general=review))
    config = {"configurable": {"thread_id": str(run_id)}}
    compiled = engine().build_graph(hooks, InMemorySaver())
    result = await compiled.ainvoke(engine().initial_state(str(run_id)), config)
    assert result["answer"] == answer
    assert result["model_rounds"] == len(model_calls) == len(reviews) == 1
    assert run.model_rounds == 2  # Planning and the separately journaled task review.
    assert result["tool_attempts"] == 0
    assert not any(isinstance(item, ToolCall) for item in journal)
    step = next(item for item in journal if isinstance(item, AgentStep))
    assert (step.key, step.status, step.data) == ("plan:0", "succeeded", expected)
    checkpoint = await compiled.aget_state(config)
    assert checkpoint.values["decision"] == expected
    assert checkpoint.values["answer"] == answer
    # Replaying a persisted plan does not request another model response.
    assert await hooks.plan(engine().initial_state(str(run_id))) == expected
    assert len(model_calls) == 1
    assert catalog_reads == [(owner_id, conversation_id)]


@pytest.mark.parametrize("encoded", [False, True])
@pytest.mark.parametrize("excess", [0, 1])
def test_finish_decision_size_boundary(encoded, excess):
    graph = engine()
    value = {"action": "finish", "answer": {"text": ""}}
    overhead = len(json.dumps(value, ensure_ascii=False, allow_nan=False))
    value["answer"]["text"] = "x" * (graph.MAX_DECISION_CHARS - overhead + excess)
    expected = {**value, "answer": dict(value["answer"])}
    assert len(json.dumps(value, ensure_ascii=False)) == graph.MAX_DECISION_CHARS + excess
    if encoded:
        value["answer"] = json.dumps(value["answer"], ensure_ascii=False)
    if excess:
        with pytest.raises(graph.AgentError, match="^agent_decision_invalid$"):
            graph.checked_decision(value)
    else:
        assert graph.checked_decision(value) == expected


async def test_approval_uses_real_interrupt_and_resume_without_replanning():
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    graph = engine()
    hooks = Hooks(
        [
            {"action": "call_tool", "tool_id": "test.write", "arguments": {}},
            {"action": "finish", "answer": {"text": "done"}},
        ]
    )
    saver = InMemorySaver()
    task_id = str(uuid4())
    config = {"configurable": {"thread_id": task_id}}
    compiled = graph.build_graph(hooks, saver)
    waiting = await compiled.ainvoke(graph.initial_state(task_id), config)
    assert waiting["__interrupt__"] and hooks.executed == []
    # Rebuild the graph as after an API restart, preserving the saver.
    resumed = await graph.build_graph(hooks, saver).ainvoke(
        Command(resume={"approve": True}), config
    )
    assert resumed["answer"] == {"text": "done"}
    assert hooks.executed == ["test.write"]
    assert len(hooks.seen) == 2


async def test_rejection_never_executes_and_is_returned_to_model():
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    graph = engine()
    hooks = Hooks(
        [
            {"action": "call_tool", "tool_id": "test.write", "arguments": {}},
            {"action": "finish", "answer": {"text": "未执行"}},
        ]
    )
    compiled = graph.build_graph(hooks, InMemorySaver())
    task_id = str(uuid4())
    config = {"configurable": {"thread_id": task_id}}
    await compiled.ainvoke(graph.initial_state(task_id), config)
    await compiled.ainvoke(Command(resume={"approve": False}), config)
    assert hooks.executed == []
    assert hooks.seen[-1][-1]["status"] == "rejected"


async def test_tool_budget_and_unknown_stop_without_false_success():
    graph = engine()
    hooks = Hooks(
        [{"action": "call_tool", "tool_id": f"test.read{i}", "arguments": {}} for i in range(7)]
    )
    with pytest.raises(graph.AgentError, match="agent_tool_budget"):
        await graph.build_graph(hooks).ainvoke(graph.initial_state(str(uuid4())))
    assert len(hooks.executed) == 4

    async def unknown(state):
        return {"status": "unknown", "error_code": "tool_result_unknown"}

    hooks = Hooks([{"action": "call_tool", "tool_id": "test.write", "arguments": {}}])
    hooks.prepare = lambda state: no_wait()
    hooks.execute = unknown
    with pytest.raises(graph.AgentError, match="tool_result_unknown"):
        await graph.build_graph(hooks).ainvoke(graph.initial_state(str(uuid4())))


async def no_wait():
    return None


async def test_tool_budget_and_repeat_detection_bound_the_actual_loop():
    graph = engine()
    hooks = Hooks(
        [{"action": "call_tool", "tool_id": f"test.read{x}", "arguments": {}} for x in range(5)]
    )
    with pytest.raises(graph.AgentError, match="agent_tool_budget"):
        await graph.build_graph(hooks).ainvoke(graph.initial_state(str(uuid4())))
    assert len(hooks.executed) == 4
    repeated = Hooks([{"action": "call_tool", "tool_id": "test.read", "arguments": {}}] * 2)
    with pytest.raises(graph.AgentError, match="agent_no_progress"):
        await graph.build_graph(repeated).ainvoke(graph.initial_state(str(uuid4())))
    assert repeated.executed == ["test.read"]


async def test_input_resume_cannot_reset_model_round_budget():
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    graph = engine()
    hooks = Hooks([{"action": "request_input", "prompt": "补充"}] * 7)
    compiled = graph.build_graph(hooks, InMemorySaver())
    task_id = str(uuid4())
    config = {"configurable": {"thread_id": task_id}}
    await compiled.ainvoke(graph.initial_state(task_id), config)
    for _ in range(5):
        await compiled.ainvoke(Command(resume={"detail": "合成"}), config)
    with pytest.raises(graph.AgentError, match="agent_model_budget"):
        await compiled.ainvoke(Command(resume={"detail": "合成"}), config)
    assert len(hooks.seen) == 6


async def test_missing_tool_natural_text_is_replanned_without_blind_field_mapping():
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    class MissingToolHooks(Hooks):
        async def plan(self, state):
            self.seen.append(state.get("supplements", []))
            return self.decisions.pop(0)

        async def prepare(self, state):
            if "location" not in state["decision"]["arguments"]:
                return {"kind": "input", "fields": {"location": "string"},
                        "tool_id": "test.read", "arguments": {}}
            return None

    hooks = MissingToolHooks([
        {"action": "call_tool", "tool_id": "test.read", "arguments": {}},
        {"action": "call_tool", "tool_id": "test.read",
         "arguments": {"location": "Hanzhong"}},
        {"action": "finish", "answer": {"text": "done"}},
    ])
    task_id = str(uuid4())
    config = {"configurable": {"thread_id": task_id}}
    compiled = engine().build_graph(hooks, InMemorySaver())
    waiting = await compiled.ainvoke(engine().initial_state(task_id), config)
    assert waiting["__interrupt__"] and len(hooks.seen) == 1
    result = await compiled.ainvoke(Command(resume={"detail": "Hanzhong"}), config)
    assert result["model_rounds"] == 3
    assert hooks.executed == ["test.read"]
    assert hooks.seen[1][0]["reply_text"] == "Hanzhong"
    assert hooks.seen[1][0]["requested_fields"] == {"location": "string"}
