"""A bounded, revision-scoped window. History is never document evidence."""

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AnswerAttempt,
    Conversation,
    ConversationMessage,
    ConversationSummary,
    KnowledgeBase,
)
from app.services.token_budget import (
    HISTORY_AFTER_SUMMARY_TOKENS,
    HISTORY_TOKENS,
    SUMMARY_INPUT_TOKENS,
    estimate_json_tokens,
    estimate_summary_request,
)

RECENT_SCAN = 64
SUMMARY_BATCH = 64
SUMMARY_CHARS = 600  # Legacy output guard; request selection is token based.
SUMMARY_ESTIMATE_TOKENS = 600
ORDINARY_WORKSPACE = "ordinary"


class Summarizer(Protocol):
    async def summarize(self, previous: str, turns: list[dict[str, str]]) -> str: ...


def context_tokens(summary: str, turns: list[dict[str, str]]) -> int:
    return estimate_json_tokens({"summary": summary, "turns": turns})


def bounded_context(summary: str, turns: list[dict[str, str]],
                    limit: int = HISTORY_TOKENS) -> dict:
    """Drop whole old turns, then an entire oversize legacy summary."""
    selected = list(turns)
    if context_tokens(summary, []) > limit:
        summary = ""
    while selected and context_tokens(summary, selected) > limit:
        selected.pop(0)
    return {"summary": summary, "turns": selected}


def plan_summary(summary: str, turns: list[dict[str, str]]) -> tuple[list[dict], list[dict]]:
    """Summarize a complete old prefix only after crossing the high watermark."""
    if context_tokens(summary, turns) <= HISTORY_TOKENS:
        return [], turns
    older: list[dict] = []
    recent = list(turns)
    while recent and context_tokens(summary, recent) > HISTORY_AFTER_SUMMARY_TOKENS:
        candidate = older + [recent[0]]
        # Use the exact same system prompt and serialization as RagRuntime.
        # Planning with complete turns prevents a paid rejected request.
        source = [{"user": turn["user"],
                   "answer_kind": turn.get("answer_kind", "knowledge")}
                  for turn in candidate]
        if estimate_summary_request(summary, source) > SUMMARY_INPUT_TOKENS:
            break
        older.append(recent.pop(0))
    return older, recent


def context_turn(message: ConversationMessage, attempt: AnswerAttempt) -> dict[str, str]:
    turn = {"user": message.content, "assistant": attempt.text or ""}
    if message.mode == "auto" and isinstance(message.query_filter, dict):
        route = message.query_filter.get("mode")
        turn["answer_kind"] = route if route in {"general", "chat"} else "knowledge"
    return turn


async def prepare_context(
    session: AsyncSession, conversation_id: UUID, kb: KnowledgeBase | None,
    answerer: Summarizer,
) -> dict:
    expected_revision = kb.revision if kb is not None else 0
    expected_workspace = kb.active_workspace if kb is not None else ORDINARY_WORKSPACE
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
        .limit(RECENT_SCAN)
    )).all())
    latest.reverse()
    summary = await session.get(ConversationSummary, conversation_id)
    valid = (summary is not None and summary.kb_revision == expected_revision
             and summary.workspace == expected_workspace)
    summary_text = summary.content if valid else ""
    prior_cursor = (None if summary is None else (
        summary.kb_revision, summary.workspace, summary.through_created_at,
        summary.through_message_id,
    ))
    summarized_this_call = False
    if valid:
        latest = [row for row in latest if
                  (row[0].created_at, row[0].id) >
                  (summary.through_created_at, summary.through_message_id)]
    if latest and hasattr(answerer, "summarize") and context_tokens(
        summary_text, [context_turn(message, attempt) for message, attempt in latest]
    ) > HISTORY_TOKENS:
        criteria = list(scope)
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
            source, _ = plan_summary(summary_text, [context_turn(message, attempt)
                                                     for message, attempt in older])
            older = older[:len(source)]
        if older:
            # Only the user's words and answer kind enter a summary. Earlier
            # assistant prose is never promoted into knowledge-base evidence.
            source = [{"user": turn["user"],
                       "answer_kind": turn.get("answer_kind", "knowledge")}
                      for turn in source]
            # Do not keep a read transaction open across an external model call.
            await session.commit()
            try:
                candidate = await answerer.summarize(summary_text, source)
                if (not isinstance(candidate, str) or not candidate.strip()
                    or len(candidate) > SUMMARY_CHARS
                    or estimate_json_tokens({"summary": candidate, "turns": []})
                    > SUMMARY_ESTIMATE_TOKENS
                    or any(ord(char) < 32 and char not in "\n\t" for char in candidate)):
                    raise ValueError("summary output is invalid")
            except Exception:
                # Model failure does not erase the prior summary or original turns.
                pass
            else:
                # Serialize insertion and compare the cursor after the model
                # call. An older concurrent result must not overwrite progress.
                current_conversation = await session.get(
                    Conversation, conversation_id, with_for_update=True,
                )
                current = (await session.get(KnowledgeBase, kb.id, with_for_update=True,
                                             populate_existing=True) if kb is not None else None)
                stored = await session.get(ConversationSummary, conversation_id,
                                           with_for_update=True, populate_existing=True)
                stored_cursor = (None if stored is None else (
                    stored.kb_revision, stored.workspace, stored.through_created_at,
                    stored.through_message_id,
                ))
                same_cursor = stored_cursor == prior_cursor
                valid_binding = (current_conversation is not None and
                    ((kb is None and current_conversation.kb_id is None) or
                     (kb is not None and current_conversation.kb_id == kb.id and
                      current is not None and current.status == "ready" and
                      current.revision == expected_revision and
                      current.active_workspace == expected_workspace)))
                if valid_binding and same_cursor:
                    if stored is None:
                        stored = ConversationSummary(conversation_id=conversation_id)
                        session.add(stored)
                    last = older[-1][0]
                    stored.kb_revision = expected_revision
                    stored.workspace = expected_workspace
                    stored.through_created_at = last.created_at
                    stored.through_message_id = last.id
                    stored.content = candidate.strip()
                    stored.updated_at = datetime.now(UTC)
                    await session.commit()
                    summarized_this_call = True
                    summary_text = stored.content
                    latest = [row for row in latest if
                              (row[0].created_at, row[0].id) >
                              (last.created_at, last.id)]
                else:
                    await session.rollback()
                    if (stored is not None and stored.kb_revision == expected_revision
                        and stored.workspace == expected_workspace):
                        summary_text = stored.content
                        latest = [row for row in latest if
                                  (row[0].created_at, row[0].id) >
                                  (stored.through_created_at, stored.through_message_id)]
    limit = HISTORY_AFTER_SUMMARY_TOKENS if summarized_this_call else HISTORY_TOKENS
    return bounded_context(summary_text, [context_turn(message, attempt)
                                          for message, attempt in latest], limit)
