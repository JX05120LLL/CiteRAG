"""Durable single-conversation answers with source and revision gates."""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from unicodedata import category
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.errors import AgentError, AgentPaused
from app.images.service import ImageService, image_view
from app.images.storage import PrivateImageStore
from app.models import (
    AgentRun,
    AnswerAttempt,
    Conversation,
    ConversationMessage,
    Document,
    ImageAttachment,
    KnowledgeBase,
    MessageImage,
    ParsedBlockRecord,
    ToolCall,
)
from app.rag.answer_adapter import AnswerError, checked_answer, checked_route
from app.rag.query_adapter import QueryError, RetrievedChunk
from app.rag.source_mapping import locate_chunk, select_verified_article_block
from app.rag.streamed_answer import ExtractiveDraft
from app.services.conversation_context import ORDINARY_WORKSPACE, prepare_context
from app.services.conversation_retention import active_conversation
from app.services.errors import ServiceError
from app.services.knowledge_memory import shared_context
from app.services.token_budget import EVIDENCE_TOKENS, estimate_json_tokens


class Retriever(Protocol):
    async def retrieve(
        self, kb_id: UUID, workspace: str, question: str, sources: dict[str, str]
    ) -> list[RetrievedChunk]: ...


class Answerer(Protocol):
    async def answer(self, question: str, evidence: list[dict]) -> str: ...


class _ImageNeedsConfirmation(Exception):
    """Stop before retrieval until a human confirms an uncertain identifier."""


def _take_speech_segments(pending: str, *, final: bool = False) -> tuple[list[str], str]:
    """Cut at stable clauses, never at arbitrary model token boundaries."""
    ready = []
    while pending:
        boundary = next((index + 1 for index, char in enumerate(pending[:80])
                         if char in "。！？!?；;" or
                         (char in "，," and index >= 11)), None)
        if boundary is None and len(pending) >= 80:
            boundary = 80
        if boundary is None:
            break
        part, pending = pending[:boundary], pending[boundary:]
        if part.strip():
            ready.append(part.strip())
    if final and pending.strip():
        ready.append(pending.strip())
        pending = ""
    return ready, pending


def matching_candidates(question: str, attributes: list[tuple[str, str]]) -> list[dict]:
    """Expose only confirmed values present literally in the question."""
    found = set()
    matches: list[tuple[str, str]] = []
    for field, value in attributes:
        if (field not in {"doc_code", "model_code", "edition"}
            or not isinstance(value, str) or not 1 <= len(value) <= 80
            or (field, value) in found):
            continue
        match = re.search(r"(?<![A-Za-z0-9])" + re.escape(value) + r"(?![A-Za-z0-9])", question)
        if match is None:
            continue
        if field == "model_code" and re.match(
            r"\s+(?:Pro|Plus|Max|Ultra|Mini|Lite|SE|V\d+|\d+G)(?![A-Za-z0-9])",
            question[match.end():], re.IGNORECASE,
        ):
            continue
        found.add((field, value))
        matches.append((field, value))
    order = {"doc_code": 0, "model_code": 1, "edition": 2}
    matches.sort(key=lambda item: (order[item[0]], -len(item[1]), item[1]))
    return [{"id": f"C{index}", "field": field, "value": value}
            for index, (field, value) in enumerate(matches, 1)]


def answer_view(message: ConversationMessage, attempt: AnswerAttempt) -> dict:
    view = {
        "message_id": message.id, "attempt_id": attempt.id,
        "client_message_id": message.client_message_id,
        "question": message.content, "mode": message.mode,
        "status": "running" if attempt.status in {"waiting_input", "waiting_approval"}
        else attempt.status,
        "text": attempt.text or "", "citations": attempt.citations or [],
        "kb_revision": attempt.kb_revision, "error_code": attempt.error_code,
        "created_at": message.created_at,
        "saved": attempt.status not in {"running", "waiting_input", "waiting_approval"},
    }
    if attempt.status in {"waiting_input", "waiting_approval"}:
        view["phase"] = attempt.status
    if message.mode == "auto" and isinstance(message.query_filter, dict):
        route = message.query_filter.get("mode")
        if route in {"semantic", "exact", "literal", "general", "chat",
                     "needs_clarification", "unsupported"}:
            view["route"] = route
    return view


def ordinary_context(context: dict, *, ordinary_chat: bool = False) -> dict:
    """Keep conversational cues without treating a prior knowledge answer as evidence."""
    # Legacy summaries may contain old assistant claims; they are useful for
    # routing a follow-up, but never enter ordinary generation as facts.
    safe = {**context, "turns": [
        turn if turn.get("answer_kind") in {"general", "chat"}
        else {**turn, "assistant": ""}
        for turn in context.get("turns", [])
    ]}
    if "summary" in safe and not ordinary_chat:
        safe["summary"] = ""
    return safe


def select_evidence(evidence: list[dict], citations: list[dict]) -> tuple[list[dict], list[dict]]:
    """Take complete, ordered source blocks; never shorten a number or condition."""
    selected: list[dict] = []
    linked: list[dict] = []
    for item, citation in zip(evidence, citations, strict=True):
        if estimate_json_tokens(selected + [item]) > EVIDENCE_TOKENS:
            break
        selected.append(item)
        linked.append(citation)
    return selected, linked


class AnswerService:
    def __init__(self, session: AsyncSession, *,
                 admission: Callable[[UUID], None] | None = None,
                 commit_allowed: Callable[[], bool] | None = None,
                 expected_binding: tuple[int, str] | None = None,
                 image_store: PrivateImageStore | None = None,
                 image_observer: object | None = None):
        self.session = session
        self.admission = admission
        self.commit_allowed = commit_allowed
        self.expected_binding = expected_binding
        self.images = ImageService(session, image_store) if image_store is not None else None
        self.image_observer = image_observer

    async def _view(self, message: ConversationMessage, attempt: AnswerAttempt) -> dict:
        view = answer_view(message, attempt)
        if self.images is not None:
            view["images"] = [image_view(image)
                              for image in await self.images.for_message(message.id)]
        return view

    async def _owned_conversation(self, owner: UUID, conversation_id: UUID, *, lock=False):
        query = select(Conversation).where(
            Conversation.id == conversation_id, Conversation.owner_id == owner,
            active_conversation(),
        )
        conversation = await self.session.scalar(query.with_for_update() if lock else query)
        if conversation is None:
            raise ServiceError(404, "conversation_not_found", "聊天不存在或不可访问")
        kb = None
        if conversation.kb_id is not None:
            kb_query = select(KnowledgeBase).where(
                KnowledgeBase.id == conversation.kb_id, KnowledgeBase.owner_id == owner,
            )
            kb = await self.session.scalar(kb_query.with_for_update() if lock else kb_query)
            if kb is None:
                raise ServiceError(404, "conversation_not_found", "聊天不存在或不可访问")
        binding = (kb.revision, kb.active_workspace) if kb is not None else (0, ORDINARY_WORKSPACE)
        if self.expected_binding is not None and binding != self.expected_binding:
            raise ServiceError(409, "kb_changed", "通话的知识库修订或活动空间已失效")
        return conversation, kb

    async def _expired_image_at(self, conversation_id: UUID):
        expired_image_at = None
        if self.images is not None:
            expired_image_at = await self.session.scalar(
                select(func.min(ConversationMessage.created_at))
                .join(MessageImage, MessageImage.message_id == ConversationMessage.id)
                .join(ImageAttachment, ImageAttachment.id == MessageImage.attachment_id)
                .where(ConversationMessage.conversation_id == conversation_id,
                       ImageAttachment.expires_at <= datetime.now(UTC))
            )
        return expired_image_at

    @staticmethod
    def _history_visibility(view, message, attempt, kb, expired_image_at):
        hidden = ((kb is not None and (kb.status != "ready"
                  or attempt.kb_revision < kb.hide_history_before_revision))
                  or (expired_image_at is not None and message.created_at >= expired_image_at))
        view["stale"] = (kb is not None and
                         (attempt.kb_revision != kb.revision or kb.status != "ready"))
        view["hidden"] = hidden
        if hidden:
            view["text"], view["citations"], view["images"] = "", [], []
        return view

    async def history_view(self, owner, conversation_id, message, attempt):
        _, kb = await self._owned_conversation(owner, conversation_id)
        return self._history_visibility(await self._view(message, attempt), message, attempt,
                                        kb, await self._expired_image_at(conversation_id))

    async def list_messages(self, owner: UUID, conversation_id: UUID) -> list[dict]:
        _, kb = await self._owned_conversation(owner, conversation_id)
        expired_image_at = await self._expired_image_at(conversation_id)
        rows = await self.session.execute(
            select(ConversationMessage, AnswerAttempt)
            .join(AnswerAttempt, AnswerAttempt.message_id == ConversationMessage.id)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc(),
                      AnswerAttempt.created_at.desc())
            .limit(50)
        )
        messages: dict[UUID, dict] = {}
        for message, attempt in rows:
            view = await self._view(message, attempt)
            self._history_visibility(view, message, attempt, kb, expired_image_at)
            messages.setdefault(message.id, view)
        return list(reversed(list(messages.values())))

    async def ask(
        self, owner: UUID, conversation_id: UUID, request_id: UUID, question: str,
        retriever: Retriever, answerer: Answerer, *, mode: str = "semantic",
        exact: dict | None = None,
        image_ids: list[UUID] | None = None,
        on_accepted: Callable[[dict], Awaitable[None]] | None = None,
        on_preview: Callable[[UUID, str], Awaitable[None]] | None = None,
        on_general_segment: Callable[[str], Awaitable[None]] | None = None,
        defer_finish: bool = False,
    ) -> dict:
        conversation, kb = await self._owned_conversation(owner, conversation_id, lock=True)
        if kb is None and (mode != "auto" or exact is not None):
            raise ServiceError(422, "ordinary_mode", "普通聊天只支持自动普通回答")
        if conversation.archived_at is not None:
            raise ServiceError(409, "conversation_archived", "聊天已归档，请恢复后再提问")
        image_ids = image_ids or []
        if image_ids and (self.images is None or self.image_observer is None):
            raise ServiceError(503, "image_unavailable", "图片识别服务尚未启用")
        if self.admission:
            self.admission(conversation_id)
        existing = await self.session.scalar(select(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.client_message_id == request_id,
        ))
        if existing is not None:
            bound_images = (await self.images.for_message(existing.id)) if self.images else []
            if (existing.content != question or existing.mode != mode
                or (mode != "auto" and existing.query_filter != exact)
                or {image.id for image in bound_images} != set(image_ids)):
                raise ServiceError(409, "idempotency_conflict", "同一提问请求不能更换正文")
            attempt = await self.session.scalar(select(AnswerAttempt).where(
                AnswerAttempt.message_id == existing.id,
            ).order_by(AnswerAttempt.created_at.desc(), AnswerAttempt.id.desc()))
            if kb is not None and kb.status != "ready":
                raise ServiceError(409, "kb_not_ready", "知识库未就绪，暂不能问答")
            if (attempt is None or attempt.kb_revision != (kb.revision if kb else 0)
                or attempt.workspace != (kb.active_workspace if kb else ORDINARY_WORKSPACE)):
                raise ServiceError(409, "kb_changed", "知识库已变化，请重新提问")
            await self.session.commit()
            return await self._view(existing, attempt)
        if kb is not None and kb.status != "ready":
            raise ServiceError(409, "kb_not_ready", "知识库未就绪，暂不能问答")
        active = await self.session.scalar(select(AnswerAttempt.id).where(
            AnswerAttempt.conversation_id == conversation_id,
            AnswerAttempt.status.in_(("running", "waiting_input", "waiting_approval")),
        ))
        if active is not None:
            raise ServiceError(409, "answer_in_progress", "当前聊天已有提问正在处理")
        if await self.session.scalar(select(ToolCall.id).where(
            ToolCall.conversation_id == conversation_id,
            ToolCall.status.in_(("pending_approval", "running")),
        ).limit(1)):
            raise ServiceError(409, "tool_in_progress", "请先完成当前工具调用或审批")
        message = ConversationMessage(
            id=uuid4(), conversation_id=conversation_id,
            client_message_id=request_id, content=question, mode=mode, query_filter=exact,
        )
        attempt = AnswerAttempt(
            id=uuid4(), conversation_id=conversation_id, message_id=message.id,
            status="running", citations=[], kb_revision=kb.revision if kb else 0,
            workspace=kb.active_workspace if kb else ORDINARY_WORKSPACE,
        )
        self.session.add(message)
        await self.session.flush()
        if image_ids:
            await self.images.bind(owner, conversation_id, message.id, image_ids)
        self.session.add(attempt)
        await self.session.commit()
        if on_accepted is not None:
            await on_accepted(await self._view(message, attempt))
        if defer_finish:
            return await self._view(message, attempt)
        return await self._finish_attempt(owner, conversation_id, kb, message, attempt,
                                          retriever, answerer, mode, exact, on_preview,
                                          on_general_segment)

    async def retry(self, owner: UUID, conversation_id: UUID, message_id: UUID,
                    retry_id: UUID, retriever: Retriever, answerer: Answerer, *,
                    on_accepted: Callable[[dict], Awaitable[None]] | None = None,
                    defer_finish: bool = False) -> dict:
        conversation, kb = await self._owned_conversation(owner, conversation_id, lock=True)
        if conversation.archived_at is not None:
            raise ServiceError(409, "conversation_archived", "聊天已归档，请恢复后再重试")
        if self.admission:
            self.admission(conversation_id)
        message = await self.session.scalar(select(ConversationMessage).where(
            ConversationMessage.id == message_id,
            ConversationMessage.conversation_id == conversation_id,
        ))
        if message is None:
            raise ServiceError(404, "message_not_found", "消息不存在或不可访问")
        unknown = await self.session.scalar(select(ToolCall.id).join(
            AgentRun, AgentRun.id == ToolCall.run_id).where(
                AgentRun.message_id == message_id, ToolCall.status == "unknown",
                ToolCall.effect == "write",
            ).limit(1))
        if unknown:
            raise ServiceError(409, "tool_result_unknown",
                               "写入结果未知，请人工核对后再处理；禁止自动重试")
        existing = await self.session.get(AnswerAttempt, retry_id)
        if existing is not None:
            if existing.message_id != message_id or existing.conversation_id != conversation_id:
                raise ServiceError(409, "idempotency_conflict", "重试请求键已用于其他消息")
            if ((kb is not None and kb.status != "ready")
                or existing.kb_revision != (kb.revision if kb else 0)
                or existing.workspace != (kb.active_workspace if kb else ORDINARY_WORKSPACE)):
                raise ServiceError(409, "kb_changed", "知识库已变化，请重新提问")
            if existing.status == "running":
                raise ServiceError(409, "answer_in_progress", "回答仍在处理中，请稍后读取结果")
            await self.session.commit()
            return await self._view(message, existing)
        if kb is not None and kb.status != "ready":
            raise ServiceError(409, "kb_not_ready", "知识库未就绪，暂不能重试")
        active = await self.session.scalar(select(AnswerAttempt.id).where(
            AnswerAttempt.conversation_id == conversation_id,
            AnswerAttempt.status.in_(("running", "waiting_input", "waiting_approval")),
        ).limit(1))
        if active is not None:
            raise ServiceError(409, "answer_in_progress", "当前聊天已有提问正在处理")
        last = await self.session.scalar(select(AnswerAttempt).where(
            AnswerAttempt.message_id == message_id,
        ).order_by(AnswerAttempt.created_at.desc(), AnswerAttempt.id.desc()))
        linked_images = (await self.images.for_message(message.id)
                         if self.images is not None else [])
        image_retry = (last is not None and last.status == "needs_clarification"
                       and bool(linked_images)
                       and any(image.needs_confirmation for image in linked_images)
                       and all(not image.needs_confirmation or image.confirmed_identifier
                               for image in linked_images))
        if last is None or (last.status not in {"failed", "interrupted", "partial"}
                            and not image_retry):
            raise ServiceError(409, "answer_not_retryable", "仅失败、中断或部分回答可以重试")
        if (last.kb_revision != (kb.revision if kb else 0)
            or last.workspace != (kb.active_workspace if kb else ORDINARY_WORKSPACE)):
            raise ServiceError(409, "kb_changed", "知识库已变化，请重新提问")
        attempt = AnswerAttempt(
            id=retry_id, conversation_id=conversation_id, message_id=message_id,
            status="running", citations=[], kb_revision=kb.revision if kb else 0,
            workspace=kb.active_workspace if kb else ORDINARY_WORKSPACE,
        )
        self.session.add(attempt)
        await self.session.commit()
        if on_accepted is not None:
            await on_accepted(await self._view(message, attempt))
        if defer_finish:
            return await self._view(message, attempt)
        return await self._finish_attempt(owner, conversation_id, kb, message, attempt,
                                          retriever, answerer, message.mode,
                                          message.query_filter)

    async def _finish_attempt(
        self, owner: UUID, conversation_id: UUID, kb: KnowledgeBase | None,
        message: ConversationMessage, attempt: AnswerAttempt,
        retriever: Retriever, answerer: Answerer, mode: str, exact: dict | None,
        on_preview: Callable[[UUID, str], Awaitable[None]] | None = None,
        on_general_segment: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict:
        question = message.content
        previewed = ""

        async def preview(candidate: str) -> None:
            nonlocal previewed
            current = await self.session.scalar(select(KnowledgeBase).where(
                KnowledgeBase.id == kb.id, KnowledgeBase.owner_id == owner,
            ).execution_options(populate_existing=True)) if kb is not None else None
            if (kb is None or current is None or current.status != "ready"
                or current.revision != attempt.kb_revision
                or current.active_workspace != attempt.workspace):
                raise QueryError("kb_changed")
            await self.session.commit()
            if not candidate.startswith(previewed):
                raise AnswerError("answer_unverifiable")
            delta = candidate[len(previewed):]
            previewed = candidate
            if delta and on_preview is not None:
                await on_preview(attempt.id, delta)

        async def general_segment(segment: str) -> None:
            nonlocal previewed
            if self.commit_allowed is not None and not self.commit_allowed():
                raise ServiceError(409, "voice_interrupted", "语音轮次已中止")
            if kb is None:
                current_chat = await self.session.scalar(select(Conversation).where(
                    Conversation.id == conversation_id, Conversation.owner_id == owner,
                    active_conversation(),
                ).execution_options(populate_existing=True))
                binding_live = (current_chat is not None and current_chat.kb_id is None
                                and attempt.kb_revision == 0
                                and attempt.workspace == ORDINARY_WORKSPACE)
            else:
                current = await self.session.scalar(select(KnowledgeBase).where(
                    KnowledgeBase.id == kb.id, KnowledgeBase.owner_id == owner,
                ).execution_options(populate_existing=True))
                binding_live = (current is not None and current.status == "ready"
                                and current.revision == attempt.kb_revision
                                and current.active_workspace == attempt.workspace)
            if not binding_live:
                raise QueryError("kb_changed")
            await self.session.commit()
            previewed += segment
            if on_general_segment is not None:
                await on_general_segment(segment)

        try:
            if self.images is not None and await self.images.for_message(message.id):
                if self.image_observer is None:
                    raise ServiceError(503, "image_unavailable", "图片识别服务尚未启用")
                observation, unsure = await self.images.observe(message.id, self.image_observer)
                if unsure:
                    status, text, citations = (
                        "needs_clarification",
                        "图片中的编号尚不确定，请核对图片观察并确认编号。", [],
                    )
                    error_code = None
                    raise _ImageNeedsConfirmation
                question = (f"{question}\n【当前提问的图片观察，非知识库原文或引用】\n"
                            f"{observation}")
            context = await prepare_context(self.session, conversation_id, kb, answerer)
            if kb is not None:
                shared = await shared_context(self.session, owner, kb)
                if shared:
                    context["shared_memory"] = shared
            if kb is None:
                exact = {"mode": "general"}
            elif mode == "auto" and exact is None:
                exact = await self._route_auto(kb, question, answerer, context)
            if mode == "auto":
                message.query_filter = exact
                await self.session.commit()
            status, text, citations = await self._resolve(
                kb.id if kb else None, attempt.workspace, question, retriever, answerer,
                mode, exact, context,
                preview if on_preview is not None else None,
                general_segment if on_general_segment is not None else None,
            )
            error_code = None
        except _ImageNeedsConfirmation:
            pass
        except AgentPaused:
            await self.session.rollback()
            waiting_attempt = await self.session.get(AnswerAttempt, attempt.id,
                                                     populate_existing=True)
            if waiting_attempt.status not in {"waiting_input", "waiting_approval"}:
                raise ServiceError(409, "agent_interrupted", "等待任务已失效") from None
            return await self._view(message, waiting_attempt)
        except asyncio.CancelledError:
            # A disconnected request must not hold the conversation until restart.
            async def interrupt() -> None:
                # The request session belongs to the cancelled ASGI task.
                async with AsyncSession(bind=self.session.bind) as cleanup:
                    await cleanup.execute(update(AnswerAttempt).where(
                        AnswerAttempt.id == attempt.id, AnswerAttempt.status == "running",
                    ).values(status="partial" if previewed else "interrupted",
                             text=previewed or None, citations=[], error_code="request_interrupted",
                             finished_at=datetime.now(UTC)))
                    await cleanup.commit()

            cleanup_task = asyncio.create_task(interrupt())
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                # The separate task still commits; startup also recovers on process loss.
                pass
            raise
        except (QueryError, AnswerError, AgentError) as error:
            status, text, citations, error_code = (
                "partial" if previewed else "failed", previewed, [], str(error)
            )
        except ServiceError as error:
            status, text, citations, error_code = (
                "partial" if previewed else "failed", previewed, [], error.code,
            )
        except Exception:
            # Provider, SDK, and DB diagnostics can contain private content.
            status, text, citations, error_code = (
                "partial" if previewed else "failed", previewed, [], "answer_unavailable"
            )
        return await self._commit_result(owner, conversation_id, kb, message, attempt,
                                         status, text, citations, error_code)

    async def _commit_result(self, owner, conversation_id, kb, message, attempt,
                             status, text, citations, error_code=None):
        current_kb = (await self.session.scalar(select(KnowledgeBase).where(
            KnowledgeBase.id == kb.id, KnowledgeBase.owner_id == owner,
        ).with_for_update().execution_options(populate_existing=True)) if kb else None)
        current_chat = await self.session.scalar(select(Conversation).where(
            Conversation.id == conversation_id, Conversation.owner_id == owner,
        ).with_for_update().execution_options(populate_existing=True))
        current_attempt = await self.session.get(
            AnswerAttempt, attempt.id, with_for_update=True, populate_existing=True,
        )
        image_expired = self.images is not None and any(
            image.expires_at <= datetime.now(UTC)
            for image in await self.images.for_message(message.id)
        )
        voice_cancelled = self.commit_allowed is not None and not self.commit_allowed()
        binding_valid = ((kb is None and current_chat is not None and
                          current_chat.kb_id is None and attempt.kb_revision == 0 and
                          attempt.workspace == ORDINARY_WORKSPACE) or
                         (kb is not None and current_chat is not None and
                          current_chat.kb_id == kb.id and current_kb is not None and
                          current_kb.status == "ready" and
                          current_kb.revision == attempt.kb_revision and
                          current_kb.active_workspace == attempt.workspace))
        if (not binding_valid
            or current_attempt is None or current_attempt.status != "running"
            or voice_cancelled or image_expired):
            if current_attempt is None:
                raise ServiceError(409, "answer_interrupted", "聊天已变化，本次回答未保存")
            current_attempt.status, current_attempt.text = "interrupted", None
            cancelled_in_same_kb = voice_cancelled and binding_valid
            code = ("image_expired" if image_expired else
                    "request_interrupted" if cancelled_in_same_kb else "kb_changed")
            current_attempt.citations, current_attempt.error_code = [], code
            current_attempt.finished_at = datetime.now(UTC)
            await self.session.commit()
            raise ServiceError(409, code, "本次回答已停止；请核对聊天与知识库后重试")
        current_attempt.status, current_attempt.text = status, text or None
        current_attempt.citations, current_attempt.error_code = citations, error_code
        current_attempt.finished_at = datetime.now(UTC)
        await self.session.commit()
        return await self._view(message, current_attempt)

    async def finish_prepared(self, owner, conversation_id, message, attempt,
                              answerer, prepared, raw):
        """Resume generation with frozen inputs; reuse source checks and final binding gates."""
        _, kb = await self._owned_conversation(owner, conversation_id)
        if prepared["general"]:
            class Draft:
                async def general_answer(self, question, context):
                    return raw
            status, text, citations = await self._resolve(None, attempt.workspace,
                prepared["question"], None, Draft(), "auto", {"mode": "general"}, {})
        else:
            status, text, citations = await self._checked_result(answerer, raw,
                prepared["evidence"], prepared["citations"])
        return await self._commit_result(owner, conversation_id, kb, message, attempt,
                                         status, text, citations)

    async def _route_auto(self, kb: KnowledgeBase, question: str, answerer: Answerer,
                          context: dict) -> dict:
        rows = list(await self.session.execute(select(
            Document.doc_code, Document.model_code, Document.edition, Document.filename,
        ).where(
            Document.kb_id == kb.id, Document.status == "ready", Document.indexed_once.is_(True),
        ).order_by(Document.created_at.desc(), Document.id.desc()).limit(501)))
        await self.session.commit()
        attributes = [
            (field, value)
            for doc_code, model_code, edition, _filename in (rows if len(rows) <= 500 else [])
            for field, value in (("doc_code", doc_code), ("model_code", model_code),
                                 ("edition", edition))
            if value is not None
        ]
        candidates = matching_candidates(question, attributes)
        if len(candidates) > 12:
            return {"mode": "needs_clarification"}
        contextual = getattr(answerer, "route_with_context", None)
        if contextual is not None:
            documents = [{"filename": row.filename[:200], "status": "ready"}
                         for row in rows[:20]]
            route_context = {**context, "knowledge_base": {
                "name": kb.name,
                "ready_document_count": len(rows) if len(rows) <= 500 else None,
                "document_list_truncated": len(rows) > 20,
            }}
            raw = await contextual(question, candidates, route_context, documents)
            return checked_route(raw, candidates, question, context=context)
        route_query = getattr(answerer, "route_query", None)
        if route_query is None:
            raise AnswerError("answer_unavailable")
        return checked_route(await route_query(question, candidates), candidates, question)

    async def _resolve(
        self, kb_id: UUID | None, workspace: str, question: str,
        retriever: Retriever, answerer: Answerer, mode: str, exact: dict | None,
        context: dict, on_preview: Callable[[str], Awaitable[None]] | None = None,
        on_general_segment: Callable[[str], Awaitable[None]] | None = None,
    ) -> tuple[str, str, list[dict]]:
        if mode == "auto":
            route = exact or {}
            routed_mode = route.get("mode")
            if routed_mode in {"general", "chat"}:
                stream = getattr(answerer, "stream_general", None)
                if on_general_segment is not None and stream is not None:
                    generated, pending = "", ""
                    async for piece in stream(route.get("query", question),
                                              ordinary_context(context,
                                                               ordinary_chat=kb_id is None)):
                        if not isinstance(piece, str):
                            raise AnswerError("answer_format_invalid")
                        generated += piece
                        if (len(generated) > 1000
                            or re.search(r"https?://", generated, re.IGNORECASE)
                            or "```" in generated
                            or any(category(char).startswith("C") and char not in "\n\t"
                                   for char in generated)):
                            raise AnswerError("answer_format_invalid")
                        ready, pending = _take_speech_segments(pending + piece)
                        for segment in ready:
                            await on_general_segment(segment)
                    if not generated.strip():
                        raise AnswerError("answer_format_invalid")
                    ready, _ = _take_speech_segments(pending, final=True)
                    for segment in ready:
                        await on_general_segment(segment)
                    return "answered", generated.strip(), []
                general = getattr(answerer, "general_answer", None)
                if general is None:
                    raise AnswerError("answer_unavailable")
                raw = await general(route.get("query", question), ordinary_context(
                    context, ordinary_chat=kb_id is None))
                try:
                    data = json.loads(raw)
                    if (not isinstance(data, dict) or set(data) != {"text"}
                        or not isinstance(data["text"], str) or not data["text"].strip()
                        or len(data["text"]) > 1000
                        or re.search(r"https?://", data["text"], re.IGNORECASE)
                        or any(category(char).startswith("C") and char not in "\n\t"
                               for char in data["text"])):
                        raise ValueError
                except (ValueError, TypeError):
                    raise AnswerError("answer_format_invalid") from None
                return "answered", data["text"].strip(), []
            if routed_mode == "exact":
                return await self._resolve_exact(kb_id, question, route["filters"], answerer,
                                                 context, on_preview)
            if routed_mode == "literal":
                return await self._resolve_literal(kb_id, question, route["phrase"], answerer,
                                                   context, on_preview)
            if routed_mode == "needs_clarification":
                return ("needs_clarification",
                        "请补充具体对象、资料或编号，以便确定该从哪里查找。", [])
            if routed_mode == "unsupported":
                return ("needs_clarification",
                        "当前知识库不支持以检索片段计算全集统计；请缩小到具体资料或编号。", [])
            if routed_mode != "semantic":
                raise AnswerError("answer_unverifiable")
        if kb_id is None:
            raise AnswerError("answer_unverifiable")
        if mode == "exact":
            return await self._resolve_exact(kb_id, question, exact or {}, answerer, context,
                                             on_preview)
        documents = list(await self.session.scalars(select(Document).where(
            Document.kb_id == kb_id, Document.status == "ready", Document.indexed_once.is_(True),
        )))
        await self.session.commit()
        if not documents:
            raise QueryError("retrieval_failed")
        by_source = {document.source_key: document for document in documents}
        retrieval_question = (exact or {}).get("query", question) if mode == "auto" else question
        chunks = await retriever.retrieve(kb_id, workspace, retrieval_question, {
            document.source_key: document.engine_doc_id for document in documents
        })
        if not chunks:
            return "insufficient_evidence", "", []
        evidence: list[dict[str, Any]] = []
        citations: list[dict] = []
        for chunk in chunks:
            document = by_source.get(chunk.source_key)
            if document is None or not document.parsed_text:
                raise QueryError("retrieval_failed")
            blocks = list(await self.session.scalars(select(ParsedBlockRecord).where(
                ParsedBlockRecord.document_id == document.id,
            ).order_by(ParsedBlockRecord.ordinal)))
            location = locate_chunk(chunk.content, document.parsed_text, blocks)
            if location is None:
                continue
            if (len(location.quote) > 2500
                or estimate_json_tokens([{"id": "E1", "text": location.quote}])
                > EVIDENCE_TOKENS):
                location = select_verified_article_block(
                    question, location, document.parsed_text, blocks,
                )
                if location is None:
                    break
            if (len(location.quote) > 2500
                or sum(len(e["text"]) for e in evidence) + len(location.quote) > 6000):
                break
            marker = f"E{len(evidence) + 1}"
            evidence.append({"id": marker, "text": location.quote})
            citations.append({
                "evidence_id": marker, "document_id": str(document.id),
                "filename": document.filename, "locator": location.locator,
                "excerpt": location.quote,
            })
            if len(evidence) == 3:
                break
        await self.session.commit()
        if not evidence:
            return "insufficient_evidence", "", []
        evidence, citations = select_evidence(evidence, citations)
        if not evidence:
            return "needs_clarification", "原文片段过长，请缩小问题或定位范围。", []
        raw = await self._answer(answerer, question, evidence, context, on_preview, citations)
        return await self._checked_result(answerer, raw, evidence, citations)

    async def _resolve_literal(
        self, kb_id: UUID, question: str, phrase: str, answerer: Answerer, context: dict,
        on_preview: Callable[[str], Awaitable[None]] | None = None,
    ) -> tuple[str, str, list[dict]]:
        escaped = phrase.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        blocks = list(await self.session.scalars(select(ParsedBlockRecord).join(
            Document, Document.id == ParsedBlockRecord.document_id,
        ).where(
            Document.kb_id == kb_id, Document.status == "ready", Document.indexed_once.is_(True),
            ParsedBlockRecord.text.like(f"%{escaped}%", escape="\\"),
        ).order_by(ParsedBlockRecord.document_id, ParsedBlockRecord.ordinal).limit(1001)))
        await self.session.commit()
        if len(blocks) > 1000:
            return "needs_clarification", "定位范围过大，请提供更完整的编号或限定资料。", []
        pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(phrase) + r"(?![A-Za-z0-9])")
        matches = [block for block in blocks if pattern.search(block.text)]
        if not matches:
            return "insufficient_evidence", "", []
        if len({block.document_id for block in matches}) != 1:
            return "needs_clarification", "多个资料包含该编号或短语，请指定文件或补充区分条件。", []
        if len(matches) > 3 or sum(len(block.text) for block in matches) > 6000:
            return "needs_clarification", "定位结果过多，请提供更具体的编号或限定资料。", []
        document = await self.session.get(Document, matches[0].document_id)
        if document is None or document.parsed_text is None:
            raise QueryError("retrieval_failed")
        evidence: list[dict] = []
        citations: list[dict] = []
        for block in matches:
            if document.parsed_text[block.start:block.end] != block.text:
                raise QueryError("retrieval_failed")
            marker = f"E{len(evidence) + 1}"
            evidence.append({"id": marker, "text": block.text})
            citations.append({
                "evidence_id": marker, "document_id": str(document.id),
                "filename": document.filename, "locator": block.locator,
                "excerpt": block.text,
            })
        await self.session.commit()
        evidence, citations = select_evidence(evidence, citations)
        if not evidence:
            return "needs_clarification", "原文片段过长，请缩小问题或定位范围。", []
        raw = await self._answer(answerer, question, evidence, context, on_preview, citations)
        return await self._checked_result(answerer, raw, evidence, citations)

    async def _resolve_exact(
        self, kb_id: UUID, question: str, filters: dict, answerer: Answerer, context: dict,
        on_preview: Callable[[str], Awaitable[None]] | None = None,
    ) -> tuple[str, str, list[dict]]:
        attributes = {key: filters[key] for key in ("doc_code", "model_code", "edition")
                      if key in filters}
        if not attributes:
            raise QueryError("retrieval_failed")
        conditions = [getattr(Document, key) == value for key, value in attributes.items()]
        documents = list(await self.session.scalars(select(Document).where(
            Document.kb_id == kb_id, Document.status == "ready", Document.indexed_once.is_(True),
            *conditions,
        ).order_by(Document.id).limit(4)))
        if not documents:
            return "insufficient_evidence", "", []
        if len(documents) != 1:
            return "needs_clarification", "", []
        document = documents[0]
        blocks = list(await self.session.scalars(select(ParsedBlockRecord).where(
            ParsedBlockRecord.document_id == document.id,
        ).order_by(ParsedBlockRecord.ordinal).limit(2001)))
        await self.session.commit()
        if len(blocks) > 2000 or sum(len(block.text) for block in blocks) > 200_000:
            return "needs_clarification", "", []
        phrase = filters.get("phrase")
        pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(phrase) + r"(?![A-Za-z0-9])") \
            if phrase else None
        selected_blocks = [
            block for block in blocks if pattern is None or pattern.search(block.text)
        ]
        if not selected_blocks:
            return "insufficient_evidence", "", []
        if (len(selected_blocks) > 3
            or sum(len(block.text) for block in selected_blocks) > 6000):
            return "needs_clarification", "", []
        evidence: list[dict] = []
        citations: list[dict] = []
        for block in selected_blocks:
            if (document.parsed_text is None
                or document.parsed_text[block.start:block.end] != block.text):
                raise QueryError("retrieval_failed")
            marker = f"E{len(evidence) + 1}"
            evidence.append({"id": marker, "text": block.text})
            citations.append({
                "evidence_id": marker, "document_id": str(document.id),
                "filename": document.filename, "locator": block.locator,
                "excerpt": block.text,
            })
        evidence, citations = select_evidence(evidence, citations)
        if not evidence:
            return "needs_clarification", "原文片段过长，请缩小问题或定位范围。", []
        raw = await self._answer(answerer, question, evidence, context, on_preview, citations)
        return await self._checked_result(answerer, raw, evidence, citations)

    @staticmethod
    async def _checked_result(answerer: Answerer, raw: str, evidence: list[dict],
                               citations: list[dict]) -> tuple[str, str, list[dict]]:
        status, text, identifiers = checked_answer(
            raw, {item["id"]: item["text"] for item in evidence},
        )
        if status == "answered" and "support" in json.loads(raw):
            verifier = getattr(answerer, "verify_answer", None)
            if verifier is None:
                raise AnswerError("answer_verification_unavailable")
            selected = [item for item in evidence if item["id"] in identifiers]
            verdict = await verifier(text, selected)
            try:
                data = json.loads(verdict)
                if (not isinstance(data, dict) or set(data) != {"supported"}
                    or not isinstance(data["supported"], bool)):
                    raise ValueError
            except (ValueError, TypeError):
                raise AnswerError("answer_verification_unavailable") from None
            if not data["supported"]:
                raise AnswerError("answer_unsupported_claims")
        return status, text, [c for c in citations if c["evidence_id"] in identifiers]

    @staticmethod
    async def _answer(answerer: Answerer, question: str, evidence: list[dict],
                      context: dict,
                      on_preview: Callable[[str], Awaitable[None]] | None = None,
                      citations: list[dict] | None = None) -> str:
        prepare_sources = getattr(answerer, "prepare_sources", None)
        if prepare_sources is not None:
            prepare_sources(citations or [])
        streamed = getattr(answerer, "stream_with_context", None) if on_preview else None
        if streamed is not None:
            draft = None if getattr(answerer, "requires_support_verification", False) else (
                ExtractiveDraft({item["id"]: item["text"] for item in evidence})
            )
            fragments = []
            size = 0
            async for fragment in streamed(question, evidence, context):
                if not isinstance(fragment, str) or size + len(fragment) > 12000:
                    raise AnswerError("answer_format_invalid")
                size += len(fragment)
                fragments.append(fragment)
                if draft is not None and draft.feed(fragment):
                    await on_preview(draft.text)
            return "".join(fragments)
        contextual = getattr(answerer, "answer_with_context", None)
        if contextual is not None:
            return await contextual(question, evidence, context)
        return await answerer.answer(question, evidence)
