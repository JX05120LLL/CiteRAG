"""A bounded, revision-scoped window. History is never document evidence."""

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AnswerAttempt,
    ConversationMessage,
    ConversationSummary,
    KnowledgeBase,
)

RECENT_TURNS = 10
SUMMARY_BATCH = 10
CONTEXT_CHARS = 1400
SUMMARY_CHARS = 600


class Summarizer(Protocol):
    async def summarize(self, previous: str, turns: list[dict[str, str]]) -> str: ...


def bounded_context(summary: str, turns: list[dict[str, str]]) -> dict:
    """Drop whole old turns; never truncate a number or condition into new meaning."""
    summary = summary[:SUMMARY_CHARS]
    while len(summary) + sum(len(v) for turn in turns for v in turn.values()) > CONTEXT_CHARS:
        if turns:
            turns.pop(0)
        else:
            summary = ""
            break
    return {"summary": summary, "turns": turns}


def context_turn(message: ConversationMessage, attempt: AnswerAttempt) -> dict[str, str]:
    turn = {"user": message.content, "assistant": attempt.text or ""}
    if message.mode == "auto" and isinstance(message.query_filter, dict):
        route = message.query_filter.get("mode")
        turn["answer_kind"] = route if route in {"general", "chat"} else "knowledge"
    return turn


async def prepare_context(
    session: AsyncSession, conversation_id: UUID, kb: KnowledgeBase,
    answerer: Summarizer,
) -> dict:
    expected_revision, expected_workspace = kb.revision, kb.active_workspace
    scope = (
        ConversationMessage.conversation_id == conversation_id,
        AnswerAttempt.kb_revision == expected_revision,
        AnswerAttempt.workspace == expected_workspace,
        AnswerAttempt.status == "answered",
    )
    latest = list((await session.execute(
        select(ConversationMessage, AnswerAttempt)
        .join(AnswerAttempt, AnswerAttempt.message_id == ConversationMessage.id)
        .where(*scope)
        .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
        .limit(RECENT_TURNS)
    )).all())
    latest.reverse()
    summary = await session.get(ConversationSummary, conversation_id)
    valid = (summary is not None and summary.kb_revision == expected_revision
             and summary.workspace == expected_workspace)
    summary_text = summary.content if valid else ""
    if latest and hasattr(answerer, "summarize"):
        first = latest[0][0]
        before_window = tuple_(ConversationMessage.created_at, ConversationMessage.id) < (
            first.created_at, first.id,
        )
        criteria = [*scope, before_window]
        if valid:
            criteria.append(tuple_(ConversationMessage.created_at, ConversationMessage.id) > (
                summary.through_created_at, summary.through_message_id,
            ))
        older = list((await session.execute(
            select(ConversationMessage, AnswerAttempt)
            .join(AnswerAttempt, AnswerAttempt.message_id == ConversationMessage.id)
            .where(*criteria)
            .order_by(ConversationMessage.created_at, ConversationMessage.id)
            .limit(SUMMARY_BATCH)
        )).all())
        if older:
            source = [{"user": message.content, "assistant": attempt.text or ""}
                      for message, attempt in older]
            # Do not keep a read transaction open across an external model call.
            await session.commit()
            try:
                candidate = await answerer.summarize(summary_text, source)
                if (not isinstance(candidate, str) or not candidate.strip()
                    or len(candidate) > SUMMARY_CHARS
                    or any(ord(char) < 32 and char not in "\n\t" for char in candidate)):
                    raise ValueError("summary output is invalid")
            except Exception:
                # Model failure does not erase the prior summary or original turns.
                pass
            else:
                current = await session.get(KnowledgeBase, kb.id, with_for_update=True,
                                            populate_existing=True)
                if (current is not None and current.status == "ready"
                    and current.revision == expected_revision
                    and current.active_workspace == expected_workspace):
                    if summary is None:
                        summary = ConversationSummary(conversation_id=conversation_id)
                        session.add(summary)
                    last = older[-1][0]
                    summary.kb_revision = expected_revision
                    summary.workspace = expected_workspace
                    summary.through_created_at = last.created_at
                    summary.through_message_id = last.id
                    summary.content = candidate.strip()
                    summary.updated_at = datetime.now(UTC)
                    await session.commit()
                    summary_text = summary.content
    return bounded_context(summary_text, [context_turn(message, attempt)
                                          for message, attempt in latest])
