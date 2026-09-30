import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, Header, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.dependencies import LocalOwner, Session
from app.models import AnswerAttempt, KnowledgeBase
from app.services.conversations import ConversationService
from app.services.errors import ServiceError
from app.voice.transport import connection_details, transport_status

router = APIRouter(prefix="/api")


@router.get("/voice/status")
async def status(request: Request):
    value = transport_status(request.app.state.settings)
    if request.app.state.settings.voice_assistant_enabled:
        from app.voice.runtime import assistant_configured

        value = {**value, "purpose": "voice_assistant",
                 "assistant": "configured" if assistant_configured(
                     request.app.state.settings) else "not_configured"}
    return value


@router.post("/conversations/{conversation_id}/voice/token")
async def token(conversation_id: UUID, request: Request, owner: LocalOwner, session: Session):
    runtime = getattr(request.app.state, "voice_runtime", None)
    if runtime:
        runtime.registry.require_text(conversation_id)
    conversation = await ConversationService(session).get_owned(owner, conversation_id)
    if conversation.archived_at is not None:
        raise ServiceError(409, "conversation_archived", "聊天已归档，请恢复后再通话")
    state = (await session.scalar(select(KnowledgeBase.status).where(
        KnowledgeBase.id == conversation.kb_id, KnowledgeBase.owner_id == owner,
    )) if conversation.kb_id is not None else "ready")
    if state != "ready":
        raise ServiceError(409, "kb_not_ready", "知识库未就绪，暂不能进行音频连接测试")
    running = await session.scalar(select(AnswerAttempt.id).where(
        AnswerAttempt.conversation_id == conversation_id, AnswerAttempt.status == "running",
    ).limit(1))
    if running is not None:
        raise ServiceError(409, "answer_running", "请等待当前回答结束后再测试音频连接")
    return connection_details(request.app.state.settings, conversation_id)


class StartVoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: UUID


class RenewVoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reconnect: bool = False


class CorrectVoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    utterance: int = Field(ge=1, le=200)
    revision: int = Field(ge=2, le=200)
    text: str = Field(min_length=1, max_length=1000)


@router.post("/conversations/{conversation_id}/voice/sessions", status_code=201)
async def start_voice(conversation_id: UUID, body: StartVoice, request: Request,
                      owner: LocalOwner, session: Session):
    return await request.app.state.voice_runtime.begin(
        session, owner, conversation_id, body.client_request_id)


def controlled(request, session_id, owner, control):
    return request.app.state.voice_runtime.registry.authorize(session_id, owner, control)


@router.post("/voice/sessions/{session_id}/renew")
async def renew_voice(session_id: UUID, body: RenewVoice, request: Request, owner: LocalOwner,
                      control: str = Header(alias="X-CiteRAG-Voice-Control", default="")):
    runtime = request.app.state.voice_runtime
    call = controlled(request, session_id, owner, control)
    await runtime.check(call)
    runtime.registry.renew(call)
    if body.reconnect:
        runtime.registry.interrupt(call, "reconnected")
        worker = runtime.workers.get(call.id)
        if worker:
            worker.reset_input()
        runtime.registry.emit(call, "phase", phase="listening")
    return {"session_id": str(call.id), "generation": call.generation,
            "lease_seconds": runtime.registry.LEASE_SECONDS}


@router.post("/voice/sessions/{session_id}/stop")
async def stop_voice(session_id: UUID, request: Request, owner: LocalOwner,
                     control: str = Header(alias="X-CiteRAG-Voice-Control", default="")):
    runtime = request.app.state.voice_runtime
    call = controlled(request, session_id, owner, control)
    await runtime.check(call)
    runtime.registry.interrupt(call, "user_stop")
    worker = runtime.workers.get(call.id)
    if worker:
        worker.reset_input()
    return {"generation": call.generation, "status": "stopped"}


@router.post("/voice/sessions/{session_id}/correction")
async def correct_voice(session_id: UUID, body: CorrectVoice, request: Request, owner: LocalOwner,
                        control: str = Header(alias="X-CiteRAG-Voice-Control", default="")):
    runtime = request.app.state.voice_runtime
    call = controlled(request, session_id, owner, control)
    await runtime.check(call)
    if (body.utterance != call.last_utterance
        or body.revision not in {call.last_revision, call.last_revision + 1}):
        raise ServiceError(409, "transcript_stale", "仅可纠正当前最新转写，请先刷新")
    worker = runtime.workers.get(call.id)
    if worker is None:
        raise ServiceError(503, "voice_worker_failed", "语音 worker 不可用，请挂断后重试")
    worker.reset_input()
    await worker.turns.submit(body.utterance, body.text.strip(), body.revision)
    return {"generation": call.generation, "status": "accepted"}


@router.post("/voice/sessions/{session_id}/end")
async def end_voice(session_id: UUID, request: Request, owner: LocalOwner,
                    control: str = Header(alias="X-CiteRAG-Voice-Control", default="")):
    runtime = request.app.state.voice_runtime
    call = controlled(request, session_id, owner, control)
    try:
        await runtime.registry.close_call(call, "hangup")
    except Exception:
        raise ServiceError(503, "voice_cleanup_failed",
                           "本地采集已停止，房间撤销尚未确认，请再次挂断") from None
    return {"status": "ended"}


@router.get("/voice/sessions/{session_id}/events")
async def voice_events(session_id: UUID, request: Request, owner: LocalOwner,
                       control: str = Header(alias="X-CiteRAG-Voice-Control", default="")):
    runtime = request.app.state.voice_runtime
    call = controlled(request, session_id, owner, control)
    await runtime.check(call)

    async def events():
        # Reconnection never replays historical audio or answers. UI reads durable messages.
        sequence = call.seq
        snapshot = {"type": "phase", "phase": call.phase, "seq": sequence,
                    "session_id": str(call.id), "generation": call.generation}
        yield "event: voice\ndata: " + json.dumps(snapshot) + "\n\n"
        while not call.closed:
            call.wake.clear()
            for event in list(call.events):
                if event["seq"] <= sequence:
                    continue
                if event["seq"] != sequence + 1:
                    yield 'event: voice\ndata: {"type":"error","code":"voice_event_gap"}\n\n'
                    return
                sequence = event["seq"]
                yield "event: voice\ndata: " + json.dumps(
                    jsonable_encoder(event), ensure_ascii=False) + "\n\n"
            if call.closed:
                return
            try:
                await asyncio.wait_for(call.wake.wait(), timeout=10)
            except TimeoutError:
                yield ": keepalive\n\n"
        for event in list(call.events):
            if event["seq"] > sequence:
                yield "event: voice\ndata: " + json.dumps(
                    jsonable_encoder(event), ensure_ascii=False) + "\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-store", "X-Accel-Buffering": "no"})
