from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, KnowledgeBase
from app.services.errors import ServiceError


class ConversationService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, owner_id: UUID, kb_id: UUID, title: str) -> Conversation:
        kb = await self.session.scalar(
            select(KnowledgeBase)
            .where(KnowledgeBase.id == kb_id, KnowledgeBase.owner_id == owner_id)
            .with_for_update()
        )
        if kb is None:
            raise ServiceError(404, "kb_not_found", "知识库不存在")
        if kb.status != "ready":
            raise ServiceError(409, "kb_not_ready", "知识库尚未就绪，暂不能创建聊天")
        conversation = Conversation(owner_id=owner_id, kb_id=kb_id, title=title)
        self.session.add(conversation)
        await self.session.commit()
        return conversation

    async def list_owned(self, owner_id: UUID, limit: int, offset: int) -> list[Conversation]:
        return list(
            await self.session.scalars(
                select(Conversation)
                .join(KnowledgeBase, Conversation.kb_id == KnowledgeBase.id)
                .where(Conversation.owner_id == owner_id, KnowledgeBase.owner_id == owner_id)
                .order_by(Conversation.created_at.desc(), Conversation.id.desc())
                .offset(offset)
                .limit(limit)
            )
        )

    async def get_owned(self, owner_id: UUID, conversation_id: UUID) -> Conversation:
        conversation = await self.session.scalar(
            select(Conversation)
            .join(KnowledgeBase, Conversation.kb_id == KnowledgeBase.id)
            .where(
                Conversation.id == conversation_id,
                Conversation.owner_id == owner_id,
                KnowledgeBase.owner_id == owner_id,
            )
        )
        if conversation is None:
            raise ServiceError(404, "conversation_not_found", "聊天不存在或不可访问")
        return conversation
