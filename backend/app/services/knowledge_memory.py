"""Small owner-curated cross-chat context scoped to a single knowledge base."""

from uuid import UUID

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AnswerAttempt,
    Conversation,
    ConversationMessage,
    KnowledgeBase,
    KnowledgeMemory,
    MessageImage,
)
from app.services.conversation_retention import active_conversation
from app.services.errors import ServiceError

MAX_MEMORIES = 20
CONTEXT_MEMORIES = 4


def memory_view(memory: KnowledgeMemory, valid: bool) -> dict:
    return {
        "id": memory.id, "kb_id": memory.kb_id, "kind": memory.kind,
        "content": memory.content, "source_conversation_id": memory.source_conversation_id,
        "source_message_id": memory.source_message_id, "created_at": memory.created_at,
        "valid": valid,
    }


class KnowledgeMemoryService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _kb(self, owner: UUID, kb_id: UUID, *, lock=False) -> KnowledgeBase:
        query = select(KnowledgeBase).where(
            KnowledgeBase.id == kb_id, KnowledgeBase.owner_id == owner,
        )
        kb = await self.session.scalar(query.with_for_update() if lock else query)
        if kb is None:
            raise ServiceError(404, "kb_not_found", "知识库不存在")
        return kb

    async def list(self, owner: UUID, kb_id: UUID) -> list[dict]:
        kb = await self._kb(owner, kb_id)
        rows = list((await self.session.execute(
            select(KnowledgeMemory, Conversation, active_conversation()).join(
                Conversation, KnowledgeMemory.source_conversation_id == Conversation.id,
            ).where(KnowledgeMemory.owner_id == owner, KnowledgeMemory.kb_id == kb_id)
            .order_by(KnowledgeMemory.created_at.desc(), KnowledgeMemory.id.desc())
            .limit(MAX_MEMORIES)
        )).all())
        return [memory_view(item, kb.status == "ready" and source_active
                            and item.kb_revision == kb.revision
                            and item.workspace == kb.active_workspace
                            and source.kb_id == kb_id)
                for item, source, source_active in rows]

    async def create(self, owner: UUID, kb_id: UUID, message_id: UUID,
                     kind: str, content: str) -> dict:
        kb = await self._kb(owner, kb_id, lock=True)
        if kb.status != "ready":
            raise ServiceError(409, "kb_not_ready", "知识库未就绪，暂不能保存共享摘要")
        source = (await self.session.execute(
            select(ConversationMessage, Conversation).join(
                Conversation, Conversation.id == ConversationMessage.conversation_id,
            ).where(ConversationMessage.id == message_id, Conversation.kb_id == kb_id,
                    Conversation.owner_id == owner, active_conversation())
        )).first()
        if source is None:
            raise ServiceError(404, "message_not_found", "来源消息不存在或不可访问")
        if (await self.session.scalar(select(exists().where(
            MessageImage.message_id == message_id,
        ))) or await self.session.scalar(select(exists().where(
            AnswerAttempt.message_id == message_id,
            AnswerAttempt.error_code == "image_expired",
        )))):
            raise ServiceError(422, "memory_image_source", "图片消息不能作为共享摘要来源")
        if not await self.session.scalar(select(exists().where(
            AnswerAttempt.message_id == message_id,
            AnswerAttempt.status == "answered",
            AnswerAttempt.kb_revision == kb.revision,
            AnswerAttempt.workspace == kb.active_workspace,
        ))):
            raise ServiceError(409, "memory_source_stale", "来源回答不属于当前知识库版本")
        count = await self.session.scalar(select(func.count()).select_from(KnowledgeMemory).where(
            KnowledgeMemory.kb_id == kb_id, KnowledgeMemory.owner_id == owner,
        ))
        if count >= MAX_MEMORIES:
            raise ServiceError(409, "memory_limit", "共享摘要已满，请先删除旧条目")
        message, conversation = source
        item = KnowledgeMemory(owner_id=owner, kb_id=kb_id,
                               source_conversation_id=conversation.id,
                               source_message_id=message.id,
                               kind=kind, content=content,
                               kb_revision=kb.revision, workspace=kb.active_workspace)
        self.session.add(item)
        await self.session.commit()
        return memory_view(item, True)

    async def delete(self, owner: UUID, kb_id: UUID, memory_id: UUID) -> None:
        await self._kb(owner, kb_id)
        item = await self.session.scalar(select(KnowledgeMemory).where(
            KnowledgeMemory.id == memory_id, KnowledgeMemory.kb_id == kb_id,
            KnowledgeMemory.owner_id == owner,
        ).with_for_update())
        if item is None:
            raise ServiceError(404, "memory_not_found", "共享摘要不存在")
        await self.session.delete(item)
        await self.session.commit()


async def shared_context(session: AsyncSession, owner: UUID, kb: KnowledgeBase) -> list[dict]:
    rows = list(await session.scalars(select(KnowledgeMemory).join(
        Conversation, KnowledgeMemory.source_conversation_id == Conversation.id,
    ).where(KnowledgeMemory.owner_id == owner, KnowledgeMemory.kb_id == kb.id,
            KnowledgeMemory.kb_revision == kb.revision,
            KnowledgeMemory.workspace == kb.active_workspace,
            Conversation.owner_id == owner, Conversation.kb_id == kb.id,
            active_conversation())
        .order_by(KnowledgeMemory.created_at.desc(), KnowledgeMemory.id.desc())
        .limit(CONTEXT_MEMORIES)))
    return [{"kind": item.kind, "content": item.content} for item in rows]
