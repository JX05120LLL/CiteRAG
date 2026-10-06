"""One admission, approval and audit path for all registered tools.

Built-in tools and explicitly configured weather tools are read-only. An MCP
server or write adapter must be reviewed before registration; request input
never names an arbitrary URL, command, path or process.
"""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from jsonschema import Draft202012Validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentRun, AnswerAttempt, Conversation, Document, KnowledgeBase, ToolCall
from app.services.conversation_retention import active_conversation
from app.services.errors import ServiceError
from app.tools.contracts import arguments_fingerprint, normalize_mcp_result  # noqa: F401


@dataclass(frozen=True)
class ToolContext:
    owner_id: UUID
    conversation_id: UUID
    kb_id: UUID | None
    kb_revision: int
    workspace: str


@dataclass(frozen=True)
class ToolDefinition:
    id: str
    title: str
    scope: str  # any | knowledge
    approval_required: bool
    impact: str
    validate: Callable[[dict], dict | None]
    run: Callable[[AsyncSession, ToolContext, dict], Awaitable[dict]]
    version: str = "1"
    input_schema: dict = field(default_factory=lambda: {
        "type": "object", "properties": {}, "additionalProperties": False,
    })
    effect: str = "read_only"
    timeout_seconds: float = 15
    backend: str = "local"
    destination: str = "local"
    unattended_read_review: dict | None = None

    def __post_init__(self):
        if (not self.version or len(self.version) > 80
            or self.scope not in {"any", "knowledge"}
            or self.effect not in {"read_only", "write"}
            or self.backend not in {"local", "mcp"}
            or not 0 < self.timeout_seconds <= 15
            or self.effect == "write" and not self.approval_required):
            raise ValueError("Invalid reviewed tool policy")

    @property
    def policy_hash(self) -> str:
        value = {"id": self.id, "version": self.version, "scope": self.scope,
                 "effect": self.effect, "approval": self.approval_required,
                 "impact": self.impact, "destination": self.destination,
                 "backend": self.backend, "schema": self.input_schema,
                 "unattended_read_review": self.unattended_read_review}
        return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                          ensure_ascii=False).encode()).hexdigest()

    def requires_approval(self, arguments: dict) -> bool:
        if (self.backend == "mcp" and self.effect == "read_only"
            and self.unattended_read_review is not None):
            schema = self.unattended_read_review["allowed_arguments_schema"]
            return not Draft202012Validator(schema).is_valid(arguments)
        return self.approval_required


def _no_arguments(arguments: dict) -> dict | None:
    return {} if arguments == {} else None


async def _local_time(_session: AsyncSession, _context: ToolContext, _arguments: dict) -> dict:
    return {"time": datetime.now(UTC).isoformat()}


async def _kb_documents(session: AsyncSession, context: ToolContext, _arguments: dict) -> dict:
    documents = list(await session.scalars(select(Document).where(
        Document.kb_id == context.kb_id, Document.status != "deleted",
    ).order_by(Document.created_at.desc(), Document.id.desc()).limit(21)))
    return {"items": [{"id": str(item.id), "filename": item.filename,
                       "status": item.status} for item in documents[:20]],
            "truncated": len(documents) > 20} if documents else {"items": []}


def built_in_tools() -> dict[str, ToolDefinition]:
    from app.tools.calculator import INPUT_SCHEMA, calculate, validate

    definitions = (
        ToolDefinition("local.time", "本机当前时间", "any", False,
                       "读取本机当前 UTC 时间；不访问知识库或外部服务。",
                       _no_arguments, _local_time),
        ToolDefinition("kb.documents", "当前知识库资料目录", "knowledge", False,
                       "只读取当前聊天绑定知识库的资料名称与处理状态；不读取正文。",
                       _no_arguments, _kb_documents),
        ToolDefinition("local.calculate", "本地计算器", "any", False,
                       "本地十进制四则运算，28位有效数字；rounded 标记近似结果。"
                       "不执行代码、不访问文件、知识库或外部服务。",
                       validate, calculate, input_schema=INPUT_SCHEMA),
    )
    return {item.id: item for item in definitions}


def call_view(call: ToolCall) -> dict:
    return {"id": call.id, "conversation_id": call.conversation_id,
            "request_id": call.request_id, "kb_id": call.kb_id,
            "tool_id": call.tool_id, "arguments": call.arguments,
            "impact": call.impact, "status": call.status, "result": call.result,
            "error_code": call.error_code, "created_at": call.created_at,
            "approved_at": call.approved_at, "finished_at": call.finished_at,
            "source_type": "tool", "run_id": call.run_id, "step_id": call.step_id,
            "tool_version": call.tool_version, "arguments_hash": call.arguments_hash,
            "effect": call.effect}


class ToolGateway:
    def __init__(self, session: AsyncSession, registry: dict[str, ToolDefinition]):
        self.session = session
        self.registry = registry

    async def _conversation(self, owner: UUID, conversation_id: UUID, *, lock=False):
        query = select(Conversation).where(
            Conversation.id == conversation_id, Conversation.owner_id == owner,
            active_conversation(),
        )
        conversation = await self.session.scalar(query.with_for_update() if lock else query)
        if conversation is None:
            raise ServiceError(404, "conversation_not_found", "聊天不存在或不可访问")
        kb = None
        if conversation.kb_id is not None:
            kb = await self.session.scalar(select(KnowledgeBase).where(
                KnowledgeBase.id == conversation.kb_id, KnowledgeBase.owner_id == owner,
            ))
            if kb is None:
                raise ServiceError(404, "conversation_not_found", "聊天不存在或不可访问")
        return conversation, kb

    @staticmethod
    def _check_spec(spec: ToolDefinition | None, kb: KnowledgeBase | None):
        if spec is None or spec.scope not in {"any", "knowledge"}:
            raise ServiceError(404, "tool_not_found", "工具未注册或不可用")
        if spec.scope == "knowledge" and kb is None:
            raise ServiceError(403, "tool_scope_forbidden", "普通聊天不能调用知识库工具")
        if spec.scope == "knowledge" and kb.status != "ready":
            raise ServiceError(409, "kb_not_ready", "知识库未就绪，工具调用已暂停")

    async def catalog(self, owner: UUID, conversation_id: UUID) -> list[dict]:
        conversation, kb = await self._conversation(owner, conversation_id)
        if conversation.archived_at is not None:
            raise ServiceError(409, "conversation_archived", "聊天已归档")
        return [{"id": spec.id, "title": spec.title, "scope": spec.scope,
                 "approval_required": spec.approval_required, "impact": spec.impact,
                 "version": spec.version, "input_schema": spec.input_schema,
                 "effect": spec.effect, "backend": spec.backend,
                 "destination": spec.destination}
                for spec in self.registry.values()
                if (spec.scope == "any" or
                    spec.scope == "knowledge" and kb is not None and kb.status == "ready")]

    async def list_calls(self, owner: UUID, conversation_id: UUID) -> list[dict]:
        await self._conversation(owner, conversation_id)
        calls = list(await self.session.scalars(select(ToolCall).where(
            ToolCall.conversation_id == conversation_id, ToolCall.owner_id == owner,
        ).order_by(ToolCall.created_at.desc(), ToolCall.id.desc()).limit(50)))
        return [call_view(call) for call in calls]

    async def invoke(self, owner: UUID, conversation_id: UUID, request_id: UUID,
                     tool_id: str, arguments: dict, *, run_id: UUID | None = None,
                     step_id: UUID | None = None, defer_execution: bool = False) -> dict:
        conversation, kb = await self._conversation(owner, conversation_id, lock=True)
        if conversation.archived_at is not None:
            raise ServiceError(409, "conversation_archived", "聊天已归档")
        existing = await self.session.scalar(select(ToolCall).where(
            ToolCall.conversation_id == conversation_id, ToolCall.request_id == request_id,
        ))
        if existing is not None:
            spec = self.registry.get(tool_id)
            replayed = spec.validate(arguments) if spec is not None else arguments
            if (existing.tool_id != tool_id or existing.arguments != replayed
                or existing.run_id != run_id):
                raise ServiceError(409, "idempotency_conflict", "同一工具请求不能更换工具或参数")
            return call_view(existing)
        spec = self.registry.get(tool_id)
        self._check_spec(spec, kb)
        validated = spec.validate(arguments)
        try:
            fingerprint = arguments_fingerprint(validated)
        except (TypeError, ValueError):
            raise ServiceError(422, "tool_arguments_invalid", "工具参数不符合要求") from None
        requires_approval = spec.requires_approval(validated)
        active_run = await self.session.scalar(select(AgentRun).where(
            AgentRun.conversation_id == conversation_id,
            AgentRun.status.in_(("running", "waiting_input", "waiting_approval")),
        ).limit(1))
        if active_run is not None and active_run.id != run_id:
            raise ServiceError(409, "agent_in_progress", "请先结束当前任务")
        if run_id is not None and (active_run is None or active_run.owner_id != owner):
            raise ServiceError(409, "agent_interrupted", "任务已失效")
        if run_id is None and await self.session.scalar(select(AnswerAttempt.id).where(
            AnswerAttempt.conversation_id == conversation_id,
            AnswerAttempt.status.in_(("running", "waiting_input", "waiting_approval")),
        ).limit(1)):
            raise ServiceError(409, "answer_in_progress", "当前聊天已有回答或等待任务")
        if spec.effect == "write" and await self.session.scalar(select(ToolCall.id).where(
            ToolCall.owner_id == owner, ToolCall.tool_id == tool_id,
            ToolCall.status == "unknown",
        ).limit(1)):
            raise ServiceError(409, "tool_result_unknown", "先核查此前写入结果，不能重复执行")
        if await self.session.scalar(select(ToolCall.id).where(
            ToolCall.conversation_id == conversation_id,
            ToolCall.status.in_(("pending_approval", "running")),
        ).limit(1)):
            raise ServiceError(409, "tool_in_progress", "请先完成当前工具调用或审批")
        context = ToolContext(owner, conversation_id, conversation.kb_id,
                              kb.revision if kb else 0,
                              kb.active_workspace if kb else "ordinary")
        call = ToolCall(owner_id=owner, conversation_id=conversation_id,
                        request_id=request_id, kb_id=context.kb_id,
                        kb_revision=context.kb_revision, workspace=context.workspace,
                        tool_id=tool_id, arguments=validated, impact=spec.impact,
                        run_id=run_id, step_id=step_id, tool_version=spec.version,
                        arguments_hash=fingerprint, effect=spec.effect,
                        status="pending_approval" if requires_approval else "running")
        self.session.add(call)
        await self.session.commit()
        if requires_approval or defer_execution:
            return call_view(call)
        return await self._execute(call, spec, context)

    async def decide(self, owner: UUID, conversation_id: UUID, call_id: UUID,
                     approve: bool) -> dict:
        conversation, kb = await self._conversation(owner, conversation_id, lock=True)
        call = await self.session.scalar(select(ToolCall).where(
            ToolCall.id == call_id, ToolCall.owner_id == owner,
            ToolCall.conversation_id == conversation_id,
        ).with_for_update())
        if call is None:
            raise ServiceError(404, "tool_call_not_found", "工具调用不存在或不可访问")
        if call.run_id is not None:
            raise ServiceError(409, "agent_decision_required", "请通过所属任务审批并继续")
        if call.status != "pending_approval":
            raise ServiceError(409, "tool_decision_closed", "此工具调用已结束或正在执行")
        if not approve:
            call.status, call.finished_at = "rejected", datetime.now(UTC)
            await self.session.commit()
            return call_view(call)
        if conversation.archived_at is not None:
            raise ServiceError(409, "conversation_archived", "聊天已归档")
        spec = self.registry.get(call.tool_id)
        if (spec is None or call.tool_version != spec.version
            or call.arguments_hash != arguments_fingerprint(call.arguments)
            or call.kb_id != conversation.kb_id or (kb is not None and
            (kb.status != "ready" and spec.scope == "knowledge" or
             (call.kb_revision, call.workspace) != (kb.revision, kb.active_workspace)))):
            call.status = "failed"
            call.error_code = "tool_scope_changed" if spec is not None else "tool_unavailable"
            call.finished_at = datetime.now(UTC)
            await self.session.commit()
            return call_view(call)
        self._check_spec(spec, kb)
        call.status, call.approved_at = "running", datetime.now(UTC)
        await self.session.commit()
        context = ToolContext(owner, conversation_id, call.kb_id,
                              call.kb_revision, call.workspace)
        return await self._execute(call, spec, context)

    async def _execute(self, call: ToolCall, spec: ToolDefinition,
                       context: ToolContext) -> dict:
        call_id = call.id
        try:
            async with asyncio.timeout(spec.timeout_seconds):
                result = await spec.run(self.session, context, call.arguments)
            if (not isinstance(result, dict)
                or len(json.dumps(result, ensure_ascii=False, allow_nan=False)) > 16000):
                raise ValueError("Tool result shape or size is invalid")
            if spec.scope == "knowledge":
                kb = await self.session.scalar(select(KnowledgeBase).where(
                    KnowledgeBase.id == context.kb_id, KnowledgeBase.owner_id == context.owner_id,
                ))
                if (kb is None or kb.status != "ready" or
                    (kb.revision, kb.active_workspace) !=
                    (context.kb_revision, context.workspace)):
                    raise ServiceError(409, "tool_scope_changed", "知识库已变化")
            call.result, call.status = result, "succeeded"
        except asyncio.CancelledError:
            await self.session.rollback()
            call = await self.session.get(ToolCall, call_id)
            call.status = "unknown" if spec.effect == "write" else "interrupted"
            call.error_code = (
                "tool_result_unknown" if spec.effect == "write" else "tool_interrupted")
            call.finished_at = datetime.now(UTC)
            await self.session.commit()
            raise
        except TimeoutError:
            await self.session.rollback()
            call = await self.session.get(ToolCall, call_id)
            call.status = "unknown" if spec.effect == "write" else "failed"
            call.error_code = "tool_result_unknown" if spec.effect == "write" else "tool_timeout"
        except ServiceError as error:
            await self.session.rollback()
            call = await self.session.get(ToolCall, call_id)
            call.status = "unknown" if spec.effect == "write" else "failed"
            call.error_code = "tool_result_unknown" if spec.effect == "write" else error.code
        except Exception:
            await self.session.rollback()
            call = await self.session.get(ToolCall, call_id)
            call.status = "unknown" if spec.effect == "write" else "failed"
            call.error_code = (
                "tool_result_unknown" if spec.effect == "write" else "tool_execution_failed")
        call.finished_at = datetime.now(UTC)
        await self.session.commit()
        return call_view(call)
