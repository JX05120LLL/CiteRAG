from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.api.capabilities import load_capability_status
from app.api.dependencies import LocalOwner, Session
from app.api.schemas import (
    ConversationArchive,
    ConversationCreate,
    ConversationRename,
    ConversationView,
    KnowledgeBaseCreate,
    KnowledgeBaseRename,
    KnowledgeBaseView,
    KnowledgeMemoryCreate,
)
from app.api.status_checks import local_check, probe_loopback
from app.models import KnowledgeBase, LocalProfile
from app.services.conversations import ConversationService
from app.services.knowledge import KnowledgeService
from app.services.knowledge_memory import KnowledgeMemoryService

router = APIRouter(prefix="/api")


class FunctionalCheckRequest(BaseModel):
    request_id: UUID
    accept_cost: Literal[True]


class FunctionalCancelRequest(BaseModel):
    request_id: UUID


@router.get("/health")
async def health(request: Request):
    return {"status": "alive", "database_configured": request.app.state.database is not None}


@router.get("/status")
async def status(request: Request):
    database = request.app.state.database
    database_status = "not_configured"
    if database is not None:
        try:
            async with database.sessions() as session:
                owner = await session.scalar(select(LocalProfile.id))
                database_status = "available" if owner is not None else "unavailable"
        except (SQLAlchemyError, OSError, TimeoutError):
            database_status = "unavailable"
    result = {
        "status": "partial",
        "mode": "local_single_user",
        "database": database_status,
    }
    result.update(await load_capability_status(request.app.state))
    backup = request.app.state.backup_runner
    result["backup"] = backup.state if backup is not None else "disabled"
    if backup is not None and backup.last_success_at is not None:
        result["last_backup_at"] = backup.last_success_at.isoformat()
    if backup is not None and backup.error_code is not None:
        result["backup_error_code"] = backup.error_code
    retention = request.app.state.retention_runner
    result["retention"] = ("disabled" if retention is None else
                           "available" if retention.available else "unavailable")
    return result


@router.post("/status/check")
async def check_status(request: Request):
    """Check local dependencies once on explicit action, without provider calls."""
    from app.voice.transport import transport_status

    base = await status(request)
    checked_at = datetime.now(UTC).isoformat()
    database_ok = base["database"] == "available"
    model_ok = base["models"] in {"available", "unverified"}
    rag_ok = base["rag"] in {"available", "unverified"}
    transport = transport_status(request.app.state.settings)["transport"]
    voice_ok = (transport == "configured"
                and await probe_loopback(request.app.state.settings.livekit_url))
    functional = request.app.state.functional_checks.read_all()["checks"]

    def observed(kind: str) -> dict[str, str]:
        item = functional[kind]
        state = item["state"] if item["state"] in {"available", "unavailable"} else "not_checked"
        return local_check(state, f"Functional check: {item['reason']}",
                           item["checked_at"] or checked_at)

    asr, tts = functional["asr"], functional["tts"]
    speech_state = ("available" if asr["state"] == tts["state"] == "available"
                    else "unavailable" if "unavailable" in {asr["state"], tts["state"]}
                    else "not_checked")
    checks = {
        "business_database": local_check(
            "available" if database_ok else "unavailable",
            "Business database query succeeded" if database_ok
            else "Business database query failed or is not configured",
            checked_at,
        ),
        "model_configuration": local_check(
            "available" if model_ok else "unavailable",
            "Local model configuration is readable; provider function was not tested" if model_ok
            else "Local model configuration is missing or unreadable", checked_at,
        ),
        "rag_database": local_check(
            "available" if rag_ok else "unavailable",
            "Local RAG database probe succeeded; retrieval was not tested" if rag_ok
            else "Local RAG database probe failed or is not configured", checked_at,
        ),
        "voice_transport": local_check(
            "available" if voice_ok else "unavailable",
            "Local media TCP port accepted a connection; voice function was not tested" if voice_ok
            else "Local media transport is unconfigured or its port is unreachable", checked_at,
        ),
        "model_provider": observed("model"),
        "speech_providers": local_check(
            speech_state, f"ASR: {asr['reason']}; TTS: {tts['reason']}",
            asr["checked_at"] or tts["checked_at"] or checked_at,
        ),
        "knowledge_engine": observed("knowledge"),
    }
    return {"checked_at": checked_at, "checks": checks}


@router.get("/status/functional")
async def functional_status(request: Request):
    """Read local evidence only; never send a supplier request."""
    return request.app.state.functional_checks.read_all()


@router.post("/status/functional/{kind}", status_code=202)
async def start_functional_check(kind: str, body: FunctionalCheckRequest, request: Request):
    try:
        return await request.app.state.functional_checks.start(kind, body.request_id)
    except ValueError as error:
        code = str(error)
        raise HTTPException(status_code=409 if code == "check_in_progress" else 422,
                            detail={"code": code, "message": "功能检测暂不可执行"}) from None


@router.post("/status/functional/{kind}/cancel")
async def cancel_functional_check(kind: str, body: FunctionalCancelRequest, request: Request):
    try:
        return await request.app.state.functional_checks.cancel(kind, body.request_id)
    except ValueError as error:
        code = str(error)
        raise HTTPException(status_code=404 if code == "check_not_found" else 422,
                            detail={"code": code, "message": "检测请求不存在或无效"}) from None


@router.get("/knowledge-bases")
async def knowledge_bases(owner: LocalOwner, session: Session):
    items = await session.scalars(
        select(KnowledgeBase)
        .where(KnowledgeBase.owner_id == owner)
        .order_by(KnowledgeBase.name, KnowledgeBase.id)
        .limit(100)
    )
    return {"items": [KnowledgeBaseView.model_validate(item) for item in items]}


@router.post("/knowledge-bases", status_code=201, response_model=KnowledgeBaseView)
async def create_knowledge_base(body: KnowledgeBaseCreate, owner: LocalOwner, session: Session):
    return await KnowledgeService(session).create(owner, body.name, body.client_request_id)


@router.patch("/knowledge-bases/{kb_id}", response_model=KnowledgeBaseView)
async def rename_knowledge_base(
    kb_id: UUID, body: KnowledgeBaseRename, owner: LocalOwner, session: Session,
):
    return await KnowledgeService(session).rename(owner, kb_id, body.name)


@router.post("/conversations", status_code=201, response_model=ConversationView)
async def create_conversation(body: ConversationCreate, owner: LocalOwner, session: Session):
    return await ConversationService(session).create(owner, body.kb_id, body.title)


@router.get("/conversations")
async def list_conversations(
    owner: LocalOwner,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    archived: bool = False,
):
    items = await ConversationService(session).list_owned(owner, limit, offset, archived=archived)
    return {"items": [ConversationView.model_validate(item) for item in items]}


@router.get("/conversations/{conversation_id}", response_model=ConversationView)
async def get_conversation(conversation_id: UUID, owner: LocalOwner, session: Session):
    return await ConversationService(session).get_owned(owner, conversation_id)


@router.patch("/conversations/{conversation_id}", response_model=ConversationView)
async def rename_conversation(conversation_id: UUID, body: ConversationRename,
                              owner: LocalOwner, session: Session):
    return await ConversationService(session).rename(owner, conversation_id, body.title)


@router.patch("/conversations/{conversation_id}/archive", response_model=ConversationView)
async def archive_conversation(conversation_id: UUID, body: ConversationArchive,
                               request: Request, owner: LocalOwner, session: Session):
    runtime = getattr(request.app.state, "voice_runtime", None)
    if runtime is not None:
        runtime.registry.require_text(conversation_id)
    return await ConversationService(session).set_archived(owner, conversation_id, body.archived)


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: UUID, request: Request,
                              owner: LocalOwner, session: Session):
    await ConversationService(session, image_store=request.app.state.image_store).delete(
        owner, conversation_id)
    return {"deleted": True}


@router.get("/knowledge-bases/{kb_id}/memories")
async def list_knowledge_memories(kb_id: UUID, owner: LocalOwner, session: Session):
    return {"items": await KnowledgeMemoryService(session).list(owner, kb_id)}


@router.post("/knowledge-bases/{kb_id}/memories", status_code=201)
async def create_knowledge_memory(kb_id: UUID, body: KnowledgeMemoryCreate,
                                  owner: LocalOwner, session: Session):
    return await KnowledgeMemoryService(session).create(
        owner, kb_id, body.source_message_id, body.kind, body.content)


@router.delete("/knowledge-bases/{kb_id}/memories/{memory_id}")
async def delete_knowledge_memory(kb_id: UUID, memory_id: UUID,
                                  owner: LocalOwner, session: Session):
    await KnowledgeMemoryService(session).delete(owner, kb_id, memory_id)
    return {"deleted": True}
