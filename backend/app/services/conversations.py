from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AnswerAttempt,
    Conversation,
    ConversationMessage,
    ConversationSummary,
    KnowledgeBase,
)
from app.services.conversation_retention import active_conversation
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
                .where(Conversation.owner_id == owner_id, KnowledgeBase.owner_id == owner_id,
                       active_conversation())
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
                active_conversation(),
            )
        )
        if conversation is None:
            raise ServiceError(404, "conversation_not_found", "聊天不存在或不可访问")
        return conversation

    async def rename(self, owner_id: UUID, conversation_id: UUID, title: str) -> Conversation:
        conversation = await self.get_owned(owner_id, conversation_id)
        conversation.title = title
        await self.session.commit()
        return conversation

    async def delete(self, owner_id: UUID, conversation_id: UUID) -> None:
        # Lock the conversation before inspecting attempts, matching ask's lock order.
        conversation = await self.session.scalar(select(Conversation).where(
            Conversation.id == conversation_id, Conversation.owner_id == owner_id,
            active_conversation(),
        ).with_for_update())
        if conversation is None:
            raise ServiceError(404, "conversation_not_found", "聊天不存在或不可访问")
        await self.session.scalar(select(KnowledgeBase).where(
            KnowledgeBase.id == conversation.kb_id,
            KnowledgeBase.owner_id == owner_id,
        ).with_for_update())
        if await self.session.scalar(select(AnswerAttempt.id).where(
            AnswerAttempt.conversation_id == conversation_id,
            AnswerAttempt.status == "running",
        ).limit(1)):
            raise ServiceError(409, "answer_in_progress", "回答仍在处理中，请稍后删除聊天")
        await self.session.execute(delete(ConversationSummary).where(
            ConversationSummary.conversation_id == conversation_id,
        ))
        await self.session.execute(delete(AnswerAttempt).where(
            AnswerAttempt.conversation_id == conversation_id,
        ))
        await self.session.execute(delete(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation_id,
        ))
        await self.session.delete(conversation)
        await self.session.commit()
