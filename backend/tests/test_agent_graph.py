"""Real LangGraph with synthetic decisions and locally executed functions."""

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


async def test_single_missing_tool_text_continues_same_decision_without_replanning():
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    class MissingToolHooks(Hooks):
        async def prepare(self, state):
            if "location" not in state["decision"]["arguments"]:
                return {"kind": "input", "fields": {"location": "string"},
                        "tool_id": "test.read", "arguments": {}}
            return None

    hooks = MissingToolHooks([
        {"action": "call_tool", "tool_id": "test.read", "arguments": {}},
        {"action": "finish", "answer": {"text": "done"}},
    ])
    task_id = str(uuid4())
    config = {"configurable": {"thread_id": task_id}}
    compiled = engine().build_graph(hooks, InMemorySaver())
    waiting = await compiled.ainvoke(engine().initial_state(task_id), config)
    assert waiting["__interrupt__"] and len(hooks.seen) == 1
    result = await compiled.ainvoke(Command(resume={"detail": "Hanzhong"}), config)
    assert result["model_rounds"] == 2
    assert hooks.executed == ["test.read"]
    assert len(hooks.seen) == 2
