"""Local text answer endpoints; model calls are explicitly disabled by default."""

from typing import Annotated, Literal
from unicodedata import category
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator, model_validator

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
    mode: Literal["semantic", "exact"] = "semantic"
    exact: "ExactFilter | None" = None

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


@router.post("/conversations/{conversation_id}/messages")
async def ask(conversation_id: UUID, body: AskRequest, request: Request,
              owner: LocalOwner, session: Session):
    if not request.app.state.answer_enabled:
        raise ServiceError(503, "answer_disabled", "文字检索与模型回答尚未启用")
    runtime = request.app.state.rag_runtime
    retriever = request.app.state.query_adapter or LightRAGQueryAdapter(runtime)
    answerer = request.app.state.answer_adapter or LightRAGAnswerAdapter(runtime)
    return await AnswerService(session).ask(
        owner, conversation_id, body.client_message_id, body.text, retriever, answerer,
        mode=body.mode, exact=body.exact.model_dump(exclude_none=True) if body.exact else None,
    )


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(conversation_id: UUID, owner: LocalOwner, session: Session):
    return {"items": await AnswerService(session).list_messages(owner, conversation_id)}
