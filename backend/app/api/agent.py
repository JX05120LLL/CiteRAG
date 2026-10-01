"""Task APIs are additive; detaching GET SSE never cancels execution."""

import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select

from app.agent.repository import TERMINAL, owned, snapshot
from app.api.answers import AskRequest
from app.api.dependencies import LocalOwner, Session
from app.models import AgentEvent
from app.services.errors import ServiceError
from app.tools.contracts import arguments_fingerprint

router = APIRouter(prefix="/api/conversations/{conversation_id}/agent-runs")
capability_router = APIRouter(prefix="/api/agent")


@capability_router.get("/capability")
async def capability(request: Request, owner: LocalOwner):
    enabled = bool(
        request.app.state.agent_enabled
        and request.app.state.agent_runtime
        and request.app.state.answer_enabled
    )
    return {
        "enabled": enabled,
        "model_rounds": 6,
        "tool_attempts": 4,
        "active_seconds": 60,
        "waiting_hours": 24,
        "mcp_enabled": bool(request.app.state.settings.mcp_enabled),
    }


class RetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    message_id: UUID


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    generation: int = Field(ge=1)
    input: dict = Field(default_factory=dict)
    voice_session_id: UUID | None = None
    control_token: SecretStr | None = None


def runner(request):
    runtime = request.app.state.agent_runtime
    if runtime is None or not request.app.state.agent_enabled:
        raise ServiceError(503, "agent_disabled", "自动工具尚未启用")
    return runtime


@router.post("", status_code=202)
async def start(conversation_id: UUID, body: AskRequest, request: Request, owner: LocalOwner):
    request.app.state.voice_runtime.registry.require_text(conversation_id)
    if body.mode == "exact" or body.exact is not None:
        raise ServiceError(422, "agent_mode_invalid", "工具任务使用现有自动路由")
    return await runner(request).start(
        owner, conversation_id, body.client_message_id, body.text, image_ids=body.image_ids
    )


@router.get("")
async def list_runs(conversation_id: UUID, owner: LocalOwner, session: Session):
    from app.models import AgentRun
    from app.services.answers import AnswerService

    await AnswerService(session)._owned_conversation(owner, conversation_id)
    rows = list(
        await session.scalars(
            select(AgentRun)
            .where(
                AgentRun.owner_id == owner,
                AgentRun.conversation_id == conversation_id,
            )
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .limit(20)
        )
    )
    return {"items": [snapshot(run) for run in rows]}


@router.get("/{run_id}")
async def get(conversation_id: UUID, run_id: UUID, owner: LocalOwner, session: Session):
    return snapshot(await owned(session, owner, conversation_id, run_id))


@router.post("/retry", status_code=202)
async def retry(conversation_id: UUID, body: RetryRequest, request: Request, owner: LocalOwner):
    request.app.state.voice_runtime.registry.require_text(conversation_id)
    return await runner(request).start(
        owner, conversation_id, body.request_id, "", retry_message_id=body.message_id
    )


async def control(request, session, owner, conversation_id, run_id, body=None):
    run = await owned(session, owner, conversation_id, run_id)
    if run.voice_session_id is not None:
        registry = request.app.state.voice_runtime.registry
        if (
            body is None
            or body.voice_session_id != run.voice_session_id
            or body.control_token is None
        ):
            raise ServiceError(409, "voice_control_required", "请在当前通话控制端操作任务")
        call = registry.authorize(
            run.voice_session_id, owner, body.control_token.get_secret_value()
        )
        if call.binding.owner != owner or call.binding.conversation != conversation_id:
            raise ServiceError(403, "voice_control_forbidden", "通话与任务归属不一致")
    else:
        request.app.state.voice_runtime.registry.require_text(conversation_id)


@router.post("/{run_id}/resume", status_code=202)
async def resume(
    conversation_id: UUID,
    run_id: UUID,
    body: ResumeRequest,
    request: Request,
    owner: LocalOwner,
    session: Session,
):
    if not request.app.state.answer_enabled:
        raise ServiceError(503, "answer_disabled", "问答尚未启用")
    try:
        arguments_fingerprint(body.input)
    except (TypeError, ValueError):
        raise ServiceError(422, "agent_input_invalid", "补充内容超出输入预算") from None
    await control(request, session, owner, conversation_id, run_id, body)
    await session.rollback()
    return await runner(request).resume(
        owner, conversation_id, run_id, body.request_id, body.generation, body.input
    )


@router.post("/{run_id}/cancel")
async def cancel(
    conversation_id: UUID,
    run_id: UUID,
    request: Request,
    owner: LocalOwner,
    session: Session,
    body: ResumeRequest | None = None,
):
    await control(request, session, owner, conversation_id, run_id, body)
    await session.rollback()
    return await runner(request).cancel(owner, conversation_id, run_id)


@router.get("/{run_id}/events")
async def events(
    conversation_id: UUID,
    run_id: UUID,
    request: Request,
    owner: LocalOwner,
    session: Session,
    after: int = 0,
):
    existing = await owned(session, owner, conversation_id, run_id)
    try:
        header_cursor = int(request.headers.get("last-event-id", "0"))
        cursor = max(after, header_cursor)
        if after < 0 or header_cursor < 0 or cursor > existing.event_seq:
            raise ValueError
    except ValueError:
        raise ServiceError(422, "agent_cursor_invalid", "事件游标无效") from None
    await session.rollback()

    async def stream():
        nonlocal cursor
        while not await request.is_disconnected():
            async with request.app.state.database.sessions() as current:
                run = await owned(current, owner, conversation_id, run_id)
                rows = list(
                    await current.scalars(
                        select(AgentEvent)
                        .where(
                            AgentEvent.run_id == run_id,
                            AgentEvent.seq > cursor,
                        )
                        .order_by(AgentEvent.seq)
                        .limit(100)
                    )
                )
                state = snapshot(run)
            for item in rows:
                cursor = item.seq
                content = item.payload
                if item.type == "answer_saved":
                    # Saved events must obey today's revision and source visibility gates too.
                    from app.models import AnswerAttempt, ConversationMessage
                    from app.services.answers import AnswerService

                    async with request.app.state.database.sessions() as current:
                        message = await current.get(ConversationMessage, run.message_id)
                        attempt = await current.get(AnswerAttempt, run.attempt_id)
                        content = {"answer": await AnswerService(current).history_view(
                            owner, conversation_id, message, attempt)}
                payload = {
                    "run_id": str(run_id),
                    "attempt_id": str(run.attempt_id),
                    "generation": item.generation,
                    "seq": item.seq,
                    "type": item.type,
                    **content,
                }
                yield (
                    f"id: {cursor}\nevent: {item.type}\ndata: "
                    + json.dumps(jsonable_encoder(payload), ensure_ascii=False)
                    + "\n\n"
                )
            if state["status"] in TERMINAL:
                yield "event: snapshot\ndata: " + json.dumps(jsonable_encoder(state)) + "\n\n"
                return
            yield ": keepalive\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
