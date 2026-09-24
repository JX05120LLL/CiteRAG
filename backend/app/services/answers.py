"""Durable single-conversation answers with source and revision gates."""

import asyncio
import re
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AnswerAttempt,
    Conversation,
    ConversationMessage,
    Document,
    KnowledgeBase,
    ParsedBlockRecord,
)
from app.rag.answer_adapter import AnswerError, checked_answer
from app.rag.query_adapter import QueryError, RetrievedChunk
from app.rag.source_mapping import locate_chunk
from app.services.errors import ServiceError


class Retriever(Protocol):
    async def retrieve(
        self, kb_id: UUID, workspace: str, question: str, sources: dict[str, str]
    ) -> list[RetrievedChunk]: ...


class Answerer(Protocol):
    async def answer(self, question: str, evidence: list[dict]) -> str: ...


def answer_view(message: ConversationMessage, attempt: AnswerAttempt) -> dict:
    return {
        "message_id": message.id, "attempt_id": attempt.id,
        "client_message_id": message.client_message_id,
        "question": message.content, "mode": message.mode, "status": attempt.status,
        "text": attempt.text or "", "citations": attempt.citations or [],
        "kb_revision": attempt.kb_revision, "error_code": attempt.error_code,
        "created_at": message.created_at, "saved": attempt.status != "running",
    }


class AnswerService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _owned_conversation(self, owner: UUID, conversation_id: UUID, *, lock=False):
        query = select(Conversation).where(
            Conversation.id == conversation_id, Conversation.owner_id == owner,
        )
        conversation = await self.session.scalar(query.with_for_update() if lock else query)
        if conversation is None:
            raise ServiceError(404, "conversation_not_found", "聊天不存在或不可访问")
        kb_query = select(KnowledgeBase).where(
            KnowledgeBase.id == conversation.kb_id, KnowledgeBase.owner_id == owner,
        )
        kb = await self.session.scalar(kb_query.with_for_update() if lock else kb_query)
        if kb is None:
            raise ServiceError(404, "conversation_not_found", "聊天不存在或不可访问")
        return conversation, kb

    async def list_messages(self, owner: UUID, conversation_id: UUID) -> list[dict]:
        _, kb = await self._owned_conversation(owner, conversation_id)
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
            view = answer_view(message, attempt)
            hidden = (kb.status != "ready"
                      or attempt.kb_revision < kb.hide_history_before_revision)
            view["stale"] = attempt.kb_revision != kb.revision or kb.status != "ready"
            view["hidden"] = hidden
            if hidden:
                view["text"], view["citations"] = "", []
            messages.setdefault(message.id, view)
        return list(reversed(list(messages.values())))

    async def ask(
        self, owner: UUID, conversation_id: UUID, request_id: UUID, question: str,
        retriever: Retriever, answerer: Answerer, *, mode: str = "semantic",
        exact: dict | None = None,
    ) -> dict:
        conversation, kb = await self._owned_conversation(owner, conversation_id, lock=True)
        existing = await self.session.scalar(select(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.client_message_id == request_id,
        ))
        if existing is not None:
            if (existing.content != question or existing.mode != mode
                or existing.query_filter != exact):
                raise ServiceError(409, "idempotency_conflict", "同一提问请求不能更换正文")
            attempt = await self.session.scalar(select(AnswerAttempt).where(
                AnswerAttempt.message_id == existing.id,
            ).order_by(AnswerAttempt.created_at.desc(), AnswerAttempt.id.desc()))
            if kb.status != "ready":
                raise ServiceError(409, "kb_not_ready", "知识库未就绪，暂不能问答")
            if (attempt is None or attempt.kb_revision != kb.revision
                or attempt.workspace != kb.active_workspace):
                raise ServiceError(409, "kb_changed", "知识库已变化，请重新提问")
            await self.session.commit()
            return answer_view(existing, attempt)
        if kb.status != "ready":
            raise ServiceError(409, "kb_not_ready", "知识库未就绪，暂不能问答")
        active = await self.session.scalar(select(AnswerAttempt.id).where(
            AnswerAttempt.conversation_id == conversation_id,
            AnswerAttempt.status == "running",
        ))
        if active is not None:
            raise ServiceError(409, "answer_in_progress", "当前聊天已有提问正在处理")
        message = ConversationMessage(
            id=uuid4(), conversation_id=conversation_id,
            client_message_id=request_id, content=question, mode=mode, query_filter=exact,
        )
        attempt = AnswerAttempt(
            id=uuid4(), conversation_id=conversation_id, message_id=message.id,
            status="running", citations=[], kb_revision=kb.revision,
            workspace=kb.active_workspace,
        )
        self.session.add(message)
        await self.session.flush()
        self.session.add(attempt)
        await self.session.commit()
        try:
            status, text, citations = await self._resolve(
                kb.id, kb.active_workspace, question, retriever, answerer, mode, exact,
            )
            error_code = None
        except asyncio.CancelledError:
            # A disconnected request must not hold the conversation until restart.
            async def interrupt() -> None:
                # The request session belongs to the cancelled ASGI task.
                async with AsyncSession(bind=self.session.bind) as cleanup:
                    await cleanup.execute(update(AnswerAttempt).where(
                        AnswerAttempt.id == attempt.id, AnswerAttempt.status == "running",
                    ).values(status="interrupted", error_code="request_interrupted",
                             finished_at=datetime.now(UTC)))
                    await cleanup.commit()

            cleanup_task = asyncio.create_task(interrupt())
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                # The separate task still commits; startup also recovers on process loss.
                pass
            raise
        except (QueryError, AnswerError) as error:
            status, text, citations, error_code = "failed", "", [], str(error)
        except Exception:
            # Provider, SDK, and DB diagnostics can contain private content.
            status, text, citations, error_code = "failed", "", [], "answer_unavailable"
        current_kb = await self.session.scalar(select(KnowledgeBase).where(
            KnowledgeBase.id == kb.id, KnowledgeBase.owner_id == owner,
        ).with_for_update().execution_options(populate_existing=True))
        current_attempt = await self.session.get(
            AnswerAttempt, attempt.id, with_for_update=True, populate_existing=True,
        )
        if (current_kb is None or current_kb.status != "ready"
            or current_kb.revision != attempt.kb_revision
            or current_kb.active_workspace != attempt.workspace
            or current_attempt.status != "running"):
            current_attempt.status, current_attempt.text = "interrupted", None
            current_attempt.citations, current_attempt.error_code = [], "kb_changed"
            current_attempt.finished_at = datetime.now(UTC)
            await self.session.commit()
            raise ServiceError(409, "kb_changed", "知识库已变化，本次回答已停止；请重新提问")
        current_attempt.status, current_attempt.text = status, text or None
        current_attempt.citations, current_attempt.error_code = citations, error_code
        current_attempt.finished_at = datetime.now(UTC)
        await self.session.commit()
        return answer_view(message, current_attempt)

    async def _resolve(
        self, kb_id: UUID, workspace: str, question: str,
        retriever: Retriever, answerer: Answerer, mode: str, exact: dict | None,
    ) -> tuple[str, str, list[dict]]:
        if mode == "exact":
            return await self._resolve_exact(kb_id, question, exact or {}, answerer)
        documents = list(await self.session.scalars(select(Document).where(
            Document.kb_id == kb_id, Document.status == "ready", Document.indexed_once.is_(True),
        )))
        await self.session.commit()
        if not documents:
            raise QueryError("retrieval_failed")
        by_source = {document.source_key: document for document in documents}
        chunks = await retriever.retrieve(kb_id, workspace, question, {
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
        raw = await answerer.answer(question, evidence)
        status, text, identifiers = checked_answer(
            raw, {item["id"]: item["text"] for item in evidence},
        )
        selected = [citation for citation in citations if citation["evidence_id"] in identifiers]
        return status, text, selected

    async def _resolve_exact(
        self, kb_id: UUID, question: str, filters: dict, answerer: Answerer,
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
        raw = await answerer.answer(question, evidence)
        status, text, identifiers = checked_answer(
            raw, {item["id"]: item["text"] for item in evidence},
        )
        return status, text, [c for c in citations if c["evidence_id"] in identifiers]
