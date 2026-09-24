from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.api.capabilities import load_capability_status
from app.api.dependencies import LocalOwner, Session
from app.api.schemas import (
    ConversationCreate,
    ConversationView,
    KnowledgeBaseCreate,
    KnowledgeBaseRename,
    KnowledgeBaseView,
)
from app.models import KnowledgeBase, LocalProfile
from app.services.conversations import ConversationService
from app.services.knowledge import KnowledgeService

router = APIRouter(prefix="/api")


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
    return result


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
):
    items = await ConversationService(session).list_owned(owner, limit, offset)
    return {"items": [ConversationView.model_validate(item) for item in items]}


@router.get("/conversations/{conversation_id}", response_model=ConversationView)
async def get_conversation(conversation_id: UUID, owner: LocalOwner, session: Session):
    return await ConversationService(session).get_owned(owner, conversation_id)
