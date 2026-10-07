"""Graph hooks journal before checkpointing; stable tool requests survive graph replay."""

import asyncio
import json
from uuid import UUID

from jsonschema import Draft202012Validator
from sqlalchemy import select

from app.agent.graph import AgentError, checked_decision
from app.agent.repository import emit, fenced
from app.models import AgentStep, ToolCall
from app.services.errors import ServiceError
from app.tools.contracts import bounded_model_results
from app.tools.gateway import ToolContext, ToolGateway, call_view


class RunHooks:
    def __init__(self, runner, run_id, generation, base):
        self.runner, self.run_id, self.generation, self.base = runner, run_id, generation, base

    async def guard(self, state):
        self.runner.assert_owned()
        async with self.runner.db.sessions() as session:
            run = await fenced(session, self.run_id, self.generation, self.runner.id)
            if run.graph_version != "citerag-agent-1":
                raise AgentError("agent_version_changed")
            if run.active_ms >= 60_000:
                raise AgentError("agent_time_budget")
            if run.voice_session_id is not None:
                await self.runner.check_voice(run)
            await session.commit()

    async def plan(self, state):
        key = f"plan:{state['model_rounds']}"
        async with self.runner.db.sessions() as session:
            run = await fenced(session, self.run_id, self.generation, self.runner.id)
            step = await session.scalar(
                select(AgentStep).where(AgentStep.run_id == self.run_id, AgentStep.key == key)
            )
            if step is not None and step.status == "succeeded":
                return step.data
            if run.model_rounds >= 6:
                raise AgentError("agent_model_budget")
            run.model_rounds += 1
            if step is None:
                step = AgentStep(run_id=self.run_id, key=key, kind="model", status="running")
                session.add(step)
            prepared = run.prepared
            tools = await ToolGateway(session, self.runner.registry).catalog(
                run.owner_id, run.conversation_id
            )
            emit(session, run, "phase", {"phase": "planning"})
            await session.commit()
        decision = getattr(self.base, "agent_decision", None)
        if decision is None:
            raise AgentError("agent_model_not_supported")
        context = {**prepared["context"], "task_supplements": state["supplements"]}
        async with asyncio.timeout(30):
            value = await decision(
                prepared["question"],
                prepared["evidence"],
                context,
                tools,
                bounded_model_results(state["results"]),
                prepared["general"],
            )
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                raise AgentError("agent_decision_invalid") from None
        if (prepared["general"] and isinstance(value, dict)
            and set(value) == {"text"} and isinstance(value["text"], str)):
            value = {"action": "finish", "answer": value}
        value = checked_decision(value)
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False)) > 16000:
            raise AgentError("agent_decision_invalid")
        async with self.runner.db.sessions() as session:
            await fenced(session, self.run_id, self.generation, self.runner.id)
            saved = await session.get(AgentStep, step.id)
            saved.data, saved.status = value, "succeeded"
            await session.commit()
        return value

    async def prepare(self, state):
        decision = state["decision"]
        if decision["action"] == "request_input":
            return {
                "kind": "input",
                "prompt": decision["prompt"],
                "fields": decision.get("fields", {}),
            }
        spec = self.runner.registry.get(decision["tool_id"])
        async with self.runner.db.sessions() as session:
            run = await fenced(session, self.run_id, self.generation, self.runner.id)
            gateway = ToolGateway(session, self.runner.registry)
            _, kb = await gateway._conversation(run.owner_id, run.conversation_id)
            gateway._check_spec(spec, kb)
            errors = list(
                Draft202012Validator(spec.input_schema).iter_errors(decision["arguments"])
            )
            if errors:
                if all(error.validator == "required" for error in errors):
                    missing = {
                        key: schema.get("type", "string")
                        for key, schema in spec.input_schema.get("properties", {}).items()
                        if key in spec.input_schema.get("required", [])
                        and key not in decision["arguments"]
                    }
                    return {"kind": "input", "prompt": "请补充工具所需参数。", "fields": missing,
                            "tool_id": spec.id, "tool_version": spec.version,
                            "policy_hash": spec.policy_hash,
                            "arguments": decision["arguments"]}
                raise ServiceError(422, "tool_arguments_invalid", "工具参数不符合登记格式")
            key = state["call_key"]
            step = await session.scalar(
                select(AgentStep).where(AgentStep.run_id == run.id, AgentStep.key == key)
            )
            if step is None:
                if run.tool_attempts >= 4:
                    raise AgentError("agent_tool_budget")
                run.tool_attempts += 1
                step = AgentStep(
                    run_id=run.id,
                    key=key,
                    kind="tool",
                    status="prepared",
                    data={"policy_hash": spec.policy_hash},
                )
                session.add(step)
                await session.flush()
                emit(
                    session,
                    run,
                    "tool_requested",
                    {
                        "tool_id": spec.id,
                        "tool_version": spec.version,
                        "arguments": decision["arguments"],
                        "destination": spec.destination,
                        "impact": spec.impact,
                    },
                )
            await session.commit()
            view = await gateway.invoke(
                run.owner_id,
                run.conversation_id,
                UUID(key),
                spec.id,
                decision["arguments"],
                run_id=run.id,
                step_id=step.id,
                defer_execution=True,
            )
            if view["status"] == "pending_approval":
                return {
                    "kind": "approval",
                    "call_id": str(view["id"]),
                    "tool_id": spec.id,
                    "tool_version": spec.version,
                    "arguments": view["arguments"],
                    "impact": spec.impact,
                    "destination": spec.destination,
                    "policy_hash": spec.policy_hash,
                }
            return None

    async def execute(self, state):
        async with self.runner.db.sessions() as session:
            run = await fenced(session, self.run_id, self.generation, self.runner.id)
            call = await session.scalar(
                select(ToolCall)
                .where(
                    ToolCall.run_id == run.id,
                    ToolCall.request_id == UUID(state["call_key"]),
                )
                .with_for_update()
            )
            if call is None:
                raise AgentError("tool_call_not_found")
            # A committed result is reused even if its graph checkpoint was lost.
            if call.status in {"succeeded", "failed", "rejected", "interrupted", "unknown"}:
                return self.result(call)
            spec = self.runner.registry.get(call.tool_id)
            if spec is None or call.tool_version != spec.version:
                raise AgentError("tool_version_changed")
            from app.tools.contracts import arguments_fingerprint

            if call.arguments_hash != arguments_fingerprint(call.arguments):
                raise AgentError("tool_arguments_changed")
            if call.status == "pending_approval":
                if call.approved_at is None:
                    raise AgentError("tool_approval_required")
                call.status = "running"
            step = await session.get(AgentStep, call.step_id)
            gateway = ToolGateway(session, self.runner.registry)
            _, kb = await gateway._conversation(run.owner_id, run.conversation_id)
            gateway._check_spec(spec, kb)
            if not step.data or step.data.get("policy_hash") != spec.policy_hash:
                raise AgentError("tool_policy_changed")
            if step.status == "running":
                # Execution started without a durable outcome; never blindly replay it.
                raise AgentError("tool_result_unknown")
            step.status = "running"
            context = ToolContext(
                run.owner_id, run.conversation_id, run.kb_id, run.kb_revision, run.workspace
            )
            await session.commit()
            await ToolGateway(session, self.runner.registry)._execute(call, spec, context)
            run = await fenced(session, self.run_id, self.generation, self.runner.id)
            step = await session.get(AgentStep, call.step_id)
            step.status = call.status
            result = self.result(call)
            emit(session, run, "tool_result", {"call": call_view(call), "result": result})
            await session.commit()
            return result

    @staticmethod
    def result(call):
        raw = json.dumps(call.result, ensure_ascii=False) if call.result is not None else ""
        return {
            "call_id": str(call.id),
            "tool_id": call.tool_id,
            "status": call.status,
            "error_code": call.error_code,
            "source_type": "tool",
            "data": call.result if len(raw) <= 4000 else {"text": raw[:4000]},
            "truncated": len(raw) > 4000,
        }

    async def finish(self, state):
        return state["decision"]["answer"]
