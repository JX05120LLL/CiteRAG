"""Stop access at 180 days and remove expired local chats in bounded batches."""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, exists, func, select

from app.models import AnswerAttempt, Conversation, ConversationMessage, ConversationSummary

RETENTION = timedelta(days=180)


def active_conversation(now: datetime | None = None):
    cutoff = (now or datetime.now(UTC)) - RETENTION
    last_input = select(func.max(ConversationMessage.created_at)).where(
        ConversationMessage.conversation_id == Conversation.id
    ).scalar_subquery()
    return func.coalesce(last_input, Conversation.created_at) > cutoff


async def sweep_expired_conversations(database, *, now: datetime | None = None) -> int:
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
            ).order_by(Conversation.created_at, Conversation.id).limit(100).with_for_update(
                skip_locked=True,
            )))
            if not ids:
                return removed
            await session.execute(delete(ConversationSummary).where(
                ConversationSummary.conversation_id.in_(ids)))
            await session.execute(delete(AnswerAttempt).where(
                AnswerAttempt.conversation_id.in_(ids)))
            await session.execute(delete(ConversationMessage).where(
                ConversationMessage.conversation_id.in_(ids)))
            await session.execute(delete(Conversation).where(Conversation.id.in_(ids)))
            await session.commit()
            removed += len(ids)


class RetentionRunner:
    def __init__(self, database, assert_owned, backup_gate=None):
        self.database = database
        self.assert_owned = assert_owned
        self.backup_gate = backup_gate
        self._task = None
        self.available = True

    async def start(self):
        self.assert_owned()
        await sweep_expired_conversations(self.database)
        self._task = asyncio.create_task(self._run(), name="conversation-retention")

    async def _run(self):
        try:
            while True:
                await asyncio.sleep(3600)
                self.assert_owned()
                if self.backup_gate is not None:
                    await self.backup_gate.wait_and_enter()
                try:
                    await sweep_expired_conversations(self.database)
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
