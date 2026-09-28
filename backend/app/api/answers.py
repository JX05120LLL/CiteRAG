"""Local text answer endpoints; model calls are explicitly disabled by default."""

import asyncio
import json
from contextlib import suppress
from typing import Annotated, Literal
from unicodedata import category
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.api.dependencies import LocalOwner, Session
from app.rag.answer_adapter import LightRAGAnswerAdapter
from app.rag.query_adapter import LightRAGQueryAdapter
from app.services.answers import AnswerService
from app.services.errors import ServiceError

router = APIRouter(prefix="/api")


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_message_id: UUID
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    mode: Literal["semantic", "exact", "auto"] = "semantic"
    exact: "ExactFilter | None" = None
    image_ids: list[UUID] = Field(default_factory=list, max_length=2)

    @field_validator("text")
    @classmethod
    def clean_text(cls, value: str) -> str:
        if any(
            category(character).startswith("C") and character not in "\n\t"
            for character in value
        ):
            raise ValueError("Question contains unsupported control characters")
        return value

    @model_validator(mode="after")
    def valid_mode(self):
        if (self.mode == "exact") != (self.exact is not None):
            raise ValueError("Exact mode requires confirmed document filters")
        if len(set(self.image_ids)) != len(self.image_ids):
            raise ValueError("Image IDs must be unique")
        return self


class ExactFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_code: str | None = None
    model_code: str | None = None
    edition: str | None = None
    phrase: str | None = None

    @field_validator("doc_code", "model_code", "edition", "phrase")
    @classmethod
    def valid_value(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if (not cleaned or len(cleaned) > 80
            or any(category(char).startswith("C") for char in cleaned)):
            raise ValueError("Exact values must be printable and at most 80 characters")
        return cleaned

    @model_validator(mode="after")
    def has_attribute(self):
        if not any((self.doc_code, self.model_code, self.edition)):
            raise ValueError("At least one confirmed document attribute is required")
        return self


AskRequest.model_rebuild()


class RetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempt_id: UUID


@router.post("/conversations/{conversation_id}/messages")
async def ask(conversation_id: UUID, body: AskRequest, request: Request,
              owner: LocalOwner, session: Session):
    request.app.state.voice_runtime.registry.require_text(conversation_id)
    if not request.app.state.answer_enabled:
        raise ServiceError(503, "answer_disabled", "文字检索与模型回答尚未启用")
    runtime = request.app.state.rag_runtime
    retriever = request.app.state.query_adapter or LightRAGQueryAdapter(runtime)
    answerer = request.app.state.answer_adapter or LightRAGAnswerAdapter(runtime)
    return await AnswerService(session,
        admission=request.app.state.voice_runtime.registry.require_text,
        image_store=request.app.state.image_store,
        image_observer=request.app.state.image_observer or runtime).ask(
        owner, conversation_id, body.client_message_id, body.text, retriever, answerer,
        mode=body.mode, exact=body.exact.model_dump(exclude_none=True) if body.exact else None,
        image_ids=body.image_ids,
    )


def _event(name: str, value: dict) -> str:
    data = json.dumps(jsonable_encoder(value), ensure_ascii=False, separators=(",", ":"))
    return f"event: {name}\ndata: {data}\n\n"


@router.post("/conversations/{conversation_id}/messages/stream")
async def ask_stream(conversation_id: UUID, body: AskRequest, request: Request,
                     owner: LocalOwner):
    request.app.state.voice_runtime.registry.require_text(conversation_id)
    if not request.app.state.answer_enabled:
        raise ServiceError(503, "answer_disabled", "文字检索与模型回答尚未启用")
    runtime = request.app.state.rag_runtime
    retriever = request.app.state.query_adapter or LightRAGQueryAdapter(runtime)
    answerer = request.app.state.answer_adapter or LightRAGAnswerAdapter(runtime)
    database = request.app.state.database

    async def stream():
        queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()
        preview_sequence = 0

        async def accepted(view: dict):
            await queue.put(("accepted", view))

        async def preview(attempt_id: UUID, delta: str):
            nonlocal preview_sequence
            preview_sequence += 1
            await queue.put(("delta", {"attempt_id": str(attempt_id),
                                       "seq": preview_sequence, "text": delta, "saved": False}))

        async def produce():
            try:
                async with database.sessions() as session:
                    view = await AnswerService(session,
                        admission=request.app.state.voice_runtime.registry.require_text,
                        image_store=request.app.state.image_store,
                        image_observer=request.app.state.image_observer or runtime).ask(
                        owner, conversation_id, body.client_message_id, body.text,
                        retriever, answerer, mode=body.mode,
                        exact=body.exact.model_dump(exclude_none=True) if body.exact else None,
                        image_ids=body.image_ids,
                        on_accepted=accepted, on_preview=preview,
                    )
                if view["status"] == "running":
                    await queue.put(("pending", view))
                    return
                # Only committed and checked text is exposed to the browser.
                if not preview_sequence and view["status"] == "answered":
                    for seq, offset in enumerate(range(0, len(view["text"]), 32), start=1):
                        await queue.put(("delta", {
                            "attempt_id": str(view["attempt_id"]), "seq": seq,
                            "text": view["text"][offset:offset + 32], "saved": True,
                        }))
                await queue.put(("saved", view))
            except ServiceError as error:
                await queue.put(("error", {"status": error.status, "code": error.code}))
            except Exception:
                await queue.put(("error", {"status": 503, "code": "answer_unavailable"}))

        producer = asyncio.create_task(produce(), name="stream-answer")
        try:
            while True:
                try:
                    name, value = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield _event(name, value)
                if name in {"saved", "pending", "error"}:
                    return
        finally:
            if not producer.done():
                producer.cancel()
            with suppress(asyncio.CancelledError):
                await producer

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-store", "X-Accel-Buffering": "no",
    })


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(conversation_id: UUID, request: Request,
                        owner: LocalOwner, session: Session):
    return {"items": await AnswerService(session,
        image_store=request.app.state.image_store).list_messages(owner, conversation_id)}


@router.post("/conversations/{conversation_id}/messages/{message_id}/retry")
async def retry_answer(conversation_id: UUID, message_id: UUID, body: RetryRequest,
                       request: Request, owner: LocalOwner, session: Session):
    request.app.state.voice_runtime.registry.require_text(conversation_id)
    if not request.app.state.answer_enabled:
        raise ServiceError(503, "answer_disabled", "文字检索与模型回答尚未启用")
    runtime = request.app.state.rag_runtime
    retriever = request.app.state.query_adapter or LightRAGQueryAdapter(runtime)
    answerer = request.app.state.answer_adapter or LightRAGAnswerAdapter(runtime)
    return await AnswerService(session,
        admission=request.app.state.voice_runtime.registry.require_text,
        image_store=request.app.state.image_store,
        image_observer=request.app.state.image_observer or runtime).retry(
                                              owner, conversation_id, message_id,
                                              body.attempt_id, retriever, answerer)
