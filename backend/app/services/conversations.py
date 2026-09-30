from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AnswerAttempt,
    Conversation,
    ConversationMessage,
    ConversationSummary,
    ImageAttachment,
    KnowledgeBase,
    KnowledgeMemory,
    MessageImage,
    ToolCall,
)
from app.services.conversation_retention import active_conversation
from app.services.errors import ServiceError


class ConversationService:
    def __init__(self, session: AsyncSession, *, image_store=None):
        self.session = session
        self.image_store = image_store

    async def create(self, owner_id: UUID, kb_id: UUID | None, title: str) -> Conversation:
        if kb_id is not None:
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

    async def list_owned(
        self, owner_id: UUID, limit: int, offset: int, *, archived: bool = False
    ) -> list[Conversation]:
        return list(
            await self.session.scalars(
                select(Conversation)
                .outerjoin(KnowledgeBase, Conversation.kb_id == KnowledgeBase.id)
                .where(
                    Conversation.owner_id == owner_id,
                    or_(Conversation.kb_id.is_(None), KnowledgeBase.owner_id == owner_id),
                    active_conversation(),
                    Conversation.archived_at.is_not(None)
                    if archived else Conversation.archived_at.is_(None),
                )
                .order_by(Conversation.created_at.desc(), Conversation.id.desc())
                .offset(offset)
                .limit(limit)
            )
        )

    async def get_owned(self, owner_id: UUID, conversation_id: UUID) -> Conversation:
        conversation = await self.session.scalar(
            select(Conversation)
            .outerjoin(KnowledgeBase, Conversation.kb_id == KnowledgeBase.id)
            .where(
                Conversation.id == conversation_id,
                Conversation.owner_id == owner_id,
                or_(Conversation.kb_id.is_(None), KnowledgeBase.owner_id == owner_id),
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

    async def set_archived(
        self, owner_id: UUID, conversation_id: UUID, archived: bool
    ) -> Conversation:
        conversation = await self.session.scalar(select(Conversation).outerjoin(
            KnowledgeBase, Conversation.kb_id == KnowledgeBase.id,
        ).where(Conversation.id == conversation_id, Conversation.owner_id == owner_id,
                or_(Conversation.kb_id.is_(None), KnowledgeBase.owner_id == owner_id),
                active_conversation()).with_for_update(of=Conversation))
        if conversation is None:
            raise ServiceError(404, "conversation_not_found", "聊天不存在或不可访问")
        if await self.session.scalar(select(AnswerAttempt.id).where(
            AnswerAttempt.conversation_id == conversation_id,
            AnswerAttempt.status == "running",
        ).limit(1)):
            raise ServiceError(409, "answer_in_progress", "回答仍在处理中，请稍后归档")
        if await self.session.scalar(select(ToolCall.id).where(
            ToolCall.conversation_id == conversation_id,
            ToolCall.status.in_(("pending_approval", "running")),
        ).limit(1)):
            raise ServiceError(409, "tool_in_progress", "请先完成工具调用或审批")
        if archived:
            conversation.archived_at = conversation.archived_at or datetime.now(UTC)
        else:
            conversation.archived_at = None
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
        if conversation.kb_id is not None:
            await self.session.scalar(select(KnowledgeBase).where(
                KnowledgeBase.id == conversation.kb_id,
                KnowledgeBase.owner_id == owner_id,
            ).with_for_update())
        if await self.session.scalar(select(AnswerAttempt.id).where(
            AnswerAttempt.conversation_id == conversation_id,
            AnswerAttempt.status == "running",
        ).limit(1)):
            raise ServiceError(409, "answer_in_progress", "回答仍在处理中，请稍后删除聊天")
        if await self.session.scalar(select(ToolCall.id).where(
            ToolCall.conversation_id == conversation_id,
            ToolCall.status.in_(("pending_approval", "running")),
        ).limit(1)):
            raise ServiceError(409, "tool_in_progress", "请先完成工具调用或审批")
        image_keys = []
        if self.image_store is not None:
            images = list(await self.session.scalars(select(ImageAttachment).where(
                ImageAttachment.conversation_id == conversation_id,
                ImageAttachment.owner_id == owner_id,
            )))
            image_keys = [image.storage_key for image in images]
            await self.session.execute(delete(MessageImage).where(
                MessageImage.attachment_id.in_([image.id for image in images]),
            ))
            await self.session.execute(delete(ImageAttachment).where(
                ImageAttachment.conversation_id == conversation_id,
            ))
        await self.session.execute(delete(ConversationSummary).where(
            ConversationSummary.conversation_id == conversation_id,
        ))
        await self.session.execute(delete(KnowledgeMemory).where(
            KnowledgeMemory.source_conversation_id == conversation_id,
        ))
        await self.session.execute(delete(ToolCall).where(
            ToolCall.conversation_id == conversation_id,
        ))
        await self.session.execute(delete(AnswerAttempt).where(
            AnswerAttempt.conversation_id == conversation_id,
        ))
        await self.session.execute(delete(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation_id,
        ))
        await self.session.delete(conversation)
        await self.session.commit()
        for key in image_keys:
            self.image_store.discard(key)
