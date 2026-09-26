from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.api.dependencies import LocalOwner, Session
from app.models import AnswerAttempt, KnowledgeBase
from app.services.conversations import ConversationService
from app.services.errors import ServiceError
from app.voice.transport import connection_details, transport_status

router = APIRouter(prefix="/api")


@router.get("/voice/status")
async def status(request: Request):
    return transport_status(request.app.state.settings)


@router.post("/conversations/{conversation_id}/voice/token")
async def token(conversation_id: UUID, request: Request, owner: LocalOwner, session: Session):
    conversation = await ConversationService(session).get_owned(owner, conversation_id)
    state = await session.scalar(select(KnowledgeBase.status).where(
        KnowledgeBase.id == conversation.kb_id, KnowledgeBase.owner_id == owner,
    ))
    if state != "ready":
        raise ServiceError(409, "kb_not_ready", "知识库未就绪，暂不能进行音频连接测试")
    running = await session.scalar(select(AnswerAttempt.id).where(
        AnswerAttempt.conversation_id == conversation_id, AnswerAttempt.status == "running",
    ).limit(1))
    if running is not None:
        raise ServiceError(409, "answer_running", "请等待当前回答结束后再测试音频连接")
    return connection_details(request.app.state.settings, conversation_id)
