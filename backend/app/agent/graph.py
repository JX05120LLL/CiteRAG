"""A small sequential graph. Business state, permission and side effects live in hooks."""

from typing import Protocol, TypedDict
from uuid import NAMESPACE_URL, uuid5

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agent.errors import AgentError
from app.tools.contracts import arguments_fingerprint

GRAPH_VERSION = "citerag-agent-1"


class AgentState(TypedDict, total=False):
    run_id: str
    model_rounds: int
    tool_attempts: int
    results: list[dict]
    decision: dict
    waiting: dict | None
    response: dict
    answer: dict
    call_key: str
    fingerprints: list[str]
    supplements: list[dict]
    retry_prepare: bool


class AgentHooks(Protocol):
    async def guard(self, state: AgentState) -> None: ...
    async def plan(self, state: AgentState) -> dict: ...
    async def prepare(self, state: AgentState) -> dict | None: ...
    async def execute(self, state: AgentState) -> dict: ...
    async def finish(self, state: AgentState) -> dict: ...


def initial_state(run_id: str) -> AgentState:
    return {
        "run_id": run_id,
        "model_rounds": 0,
        "tool_attempts": 0,
        "results": [],
        "fingerprints": [],
        "supplements": [],
    }


def checked_decision(value: dict) -> dict:
    if not isinstance(value, dict):
        raise AgentError("agent_decision_invalid")
    action = value.get("action")
    if action == "finish" and set(value) == {"action", "answer"}:
        if isinstance(value["answer"], dict):
            return value
    if action == "call_tool" and set(value) == {"action", "tool_id", "arguments"}:
        if isinstance(value["tool_id"], str) and 1 <= len(value["tool_id"]) <= 80:
            try:
                arguments_fingerprint(value["arguments"])
                return value
            except (TypeError, ValueError):
                pass
    if action == "request_input" and set(value) <= {"action", "prompt", "fields"}:
        if isinstance(value.get("prompt"), str) and 1 <= len(value["prompt"]) <= 500:
            fields = value.get("fields", {})
            if (
                isinstance(fields, dict)
                and len(fields) <= 8
                and all(
                    isinstance(key, str)
                    and 1 <= len(key) <= 80
                    and kind in {"string", "number", "integer", "boolean"}
                    for key, kind in fields.items()
                )
            ):
                return value
    raise AgentError("agent_decision_invalid")


def build_graph(hooks: AgentHooks, checkpointer=None):
    async def plan(state: AgentState):
        await hooks.guard(state)
        if state["model_rounds"] >= 6:
            raise AgentError("agent_model_budget")
        decision = checked_decision(await hooks.plan(state))
        return {
            "decision": decision,
            "model_rounds": state["model_rounds"] + 1,
            "response": {},
            "waiting": None,
            "retry_prepare": False,
        }

    async def prepare(state: AgentState):
        await hooks.guard(state)
        decision = state["decision"]
        if decision["action"] == "call_tool":
            if state["tool_attempts"] >= 4:
                raise AgentError("agent_tool_budget")
            key = decision["tool_id"] + ":" + arguments_fingerprint(decision["arguments"])
            if key in state["fingerprints"]:
                raise AgentError("agent_no_progress")
        # Deterministic key remains stable when prepare/execute is replayed.
        state = {
            **state,
            "call_key": str(
                uuid5(NAMESPACE_URL, f"{state['run_id']}:round:{state['model_rounds']}")
            ),
        }
        waiting = await hooks.prepare(state)
        return {"call_key": state["call_key"], "waiting": waiting}

    async def wait(state: AgentState):
        # No side effect before interrupt; resume restarts this node.
        response = interrupt(state["waiting"])
        await hooks.guard(state)
        if not isinstance(response, dict):
            raise AgentError("agent_resume_invalid")
        return {"response": response}

    async def execute(state: AgentState):
        await hooks.guard(state)
        waiting = state.get("waiting") or {}
        if waiting.get("kind") == "input":
            response = state["response"]
            fields = waiting.get("fields") or {}
            if set(response) == {"detail"}:
                clarification = {
                    "reply_text": response["detail"],
                    "requested_fields": fields,
                    "tool_id": waiting.get("tool_id"),
                    "prior_arguments": waiting.get("arguments", {}),
                }
                return {"supplements": state["supplements"] + [clarification],
                        "waiting": None, "retry_prepare": False}
            supplement = {"supplements": state["supplements"] + [response], "waiting": None}
            decision = state["decision"]
            if (decision["action"] == "call_tool"
                and waiting.get("tool_id") == decision["tool_id"]
                and waiting.get("arguments") == decision["arguments"]
                and set(response) == set(fields)):
                supplement["decision"] = checked_decision({
                    **decision, "arguments": {**decision["arguments"], **response},
                })
                supplement["retry_prepare"] = True
            return supplement
        if waiting.get("kind") == "approval" and state["response"].get("approve") is not True:
            result = {"status": "rejected", "source_type": "tool", "data": None}
        else:
            result = await hooks.execute(state)
        if result.get("status") == "unknown":
            raise AgentError("tool_result_unknown")
        key = (
            state["decision"]["tool_id"]
            + ":"
            + arguments_fingerprint(state["decision"]["arguments"])
        )
        return {
            "results": state["results"] + [result],
            "tool_attempts": state["tool_attempts"] + 1,
            "fingerprints": state["fingerprints"] + [key],
            "waiting": None,
            "retry_prepare": False,
        }

    async def finish(state: AgentState):
        await hooks.guard(state)
        return {"answer": await hooks.finish(state)}

    graph = StateGraph(AgentState)
    for name, node in (
        ("plan", plan),
        ("prepare", prepare),
        ("wait", wait),
        ("execute", execute),
        ("finish", finish),
    ):
        graph.add_node(name, node)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges(
        "plan", lambda s: "finish" if s["decision"]["action"] == "finish" else "prepare"
    )
    graph.add_conditional_edges("prepare", lambda s: "wait" if s["waiting"] else "execute")
    graph.add_edge("wait", "execute")
    graph.add_conditional_edges(
        "execute", lambda s: "prepare" if s.get("retry_prepare") else "plan"
    )
    graph.add_edge("finish", END)
    return graph.compile(checkpointer=checkpointer)
