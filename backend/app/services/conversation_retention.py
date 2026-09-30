"""Stop access at 180 days and remove expired local chats in bounded batches."""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, exists, func, or_, select, update

from app.models import (
    AnswerAttempt,
    Conversation,
    ConversationMessage,
    ConversationSummary,
    ImageAttachment,
    KnowledgeMemory,
    MessageImage,
    ToolCall,
)

RETENTION = timedelta(days=180)


def active_conversation(now: datetime | None = None):
    cutoff = (now or datetime.now(UTC)) - RETENTION
    last_input = select(func.max(ConversationMessage.created_at)).where(
        ConversationMessage.conversation_id == Conversation.id
    ).correlate(Conversation).scalar_subquery()
    # Ordinary chats are retained until their owner deletes them. Knowledge
    # chats keep the existing 180-day access and sweep policy.
    return or_(Conversation.kb_id.is_(None),
               func.coalesce(last_input, Conversation.created_at) > cutoff)


async def sweep_expired_conversations(database, *, now: datetime | None = None,
                                      image_store=None) -> int:
    """Never delete an active answer; the access predicate still hides its expired chat."""
    removed = 0
    while True:
        async with database.sessions() as session:
            ids = list(await session.scalars(select(Conversation.id).where(
                Conversation.owner_id.is_not(None),
                ~active_conversation(now),
                ~exists(select(AnswerAttempt.id).where(
                    AnswerAttempt.conversation_id == Conversation.id,
                    AnswerAttempt.status == "running",
                )),
                ~exists(select(ToolCall.id).where(
                    ToolCall.conversation_id == Conversation.id,
                    ToolCall.status == "running",
                )),
            ).order_by(Conversation.created_at, Conversation.id).limit(100).with_for_update(
                skip_locked=True,
            )))
            if not ids:
                return removed
            keys = []
            if image_store is not None:
                images = list(await session.scalars(select(ImageAttachment).where(
                    ImageAttachment.conversation_id.in_(ids),
                )))
                keys = [image.storage_key for image in images]
                await session.execute(delete(MessageImage).where(
                    MessageImage.attachment_id.in_([image.id for image in images]),
                ))
                await session.execute(delete(ImageAttachment).where(
                    ImageAttachment.conversation_id.in_(ids),
                ))
            await session.execute(delete(ConversationSummary).where(
                ConversationSummary.conversation_id.in_(ids)))
            await session.execute(delete(KnowledgeMemory).where(
                KnowledgeMemory.source_conversation_id.in_(ids)))
            await session.execute(delete(ToolCall).where(
                ToolCall.conversation_id.in_(ids)))
            await session.execute(delete(AnswerAttempt).where(
                AnswerAttempt.conversation_id.in_(ids)))
            await session.execute(delete(ConversationMessage).where(
                ConversationMessage.conversation_id.in_(ids)))
            await session.execute(delete(Conversation).where(Conversation.id.in_(ids)))
            await session.commit()
            removed += len(ids)
        if image_store is not None:
            for key in keys:
                image_store.discard(key)


async def sweep_expired_images(database, image_store, *, now: datetime | None = None) -> int:
    """Remove expired images and any saved answer that may derive from them."""
    now = now or datetime.now(UTC)
    async with database.sessions() as session:
        images = list(await session.scalars(select(ImageAttachment).where(
            ImageAttachment.expires_at <= now,
            ~exists(select(AnswerAttempt.id).where(
                AnswerAttempt.conversation_id == ImageAttachment.conversation_id,
                AnswerAttempt.status == "running",
            )),
        ).order_by(ImageAttachment.expires_at, ImageAttachment.id).limit(100)
            .with_for_update(skip_locked=True)))
        if not images:
            referenced = set(await session.scalars(select(ImageAttachment.storage_key)))
            image_store.cleanup_orphans(referenced)
            return 0
        keys = [image.storage_key for image in images]
        for image in images:
            first = await session.scalar(select(func.min(ConversationMessage.created_at)).join(
                MessageImage, MessageImage.message_id == ConversationMessage.id,
            ).where(MessageImage.attachment_id == image.id))
            if first is not None:
                affected = select(ConversationMessage.id).where(
                    ConversationMessage.conversation_id == image.conversation_id,
                    ConversationMessage.created_at >= first,
                )
                await session.execute(update(AnswerAttempt).where(
                    AnswerAttempt.message_id.in_(affected),
                ).values(status="interrupted", text=None, citations=[],
                         error_code="image_expired", finished_at=now))
                await session.execute(delete(ConversationSummary).where(
                    ConversationSummary.conversation_id == image.conversation_id,
                ))
        await session.execute(delete(MessageImage).where(
            MessageImage.attachment_id.in_([image.id for image in images]),
        ))
        await session.execute(delete(ImageAttachment).where(
            ImageAttachment.id.in_([image.id for image in images]),
        ))
        await session.commit()
    for key in keys:
        image_store.discard(key)
    return len(images)


class RetentionRunner:
    def __init__(self, database, assert_owned, backup_gate=None, image_store=None):
        self.database = database
        self.assert_owned = assert_owned
        self.backup_gate = backup_gate
        self.image_store = image_store
        self._task = None
        self.available = True

    async def start(self):
        self.assert_owned()
        await sweep_expired_conversations(self.database, image_store=self.image_store)
        if self.image_store is not None:
            await sweep_expired_images(self.database, self.image_store)
        self._task = asyncio.create_task(self._run(), name="conversation-retention")

    async def _run(self):
        try:
            while True:
                await asyncio.sleep(3600)
                self.assert_owned()
                if self.backup_gate is not None:
                    await self.backup_gate.wait_and_enter()
                try:
                    await sweep_expired_conversations(self.database,
                                                      image_store=self.image_store)
                    if self.image_store is not None:
                        await sweep_expired_images(self.database, self.image_store)
                finally:
                    if self.backup_gate is not None:
                        await self.backup_gate.leave()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Do not log SQL parameters, private chat text, or database secrets.
            self.available = False

    async def close(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
