from datetime import datetime
from typing import Annotated, Literal
from unicodedata import category
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from app.validation import CapabilityState

__all__ = ["CapabilityState", "ConversationCreate", "ConversationRename",
           "ConversationView", "KnowledgeBaseView"]


class ConversationCreate(BaseModel):
    kb_id: UUID
    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ] = "新聊天"


class ConversationRename(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=120)
    ]


class ConversationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    owner_id: UUID
    kb_id: UUID
    title: str
    created_at: datetime


class KnowledgeBaseView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    status: Literal["empty", "ready", "maintaining", "blocked"]


class KnowledgeBaseRename(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=120)
    ]

    @field_validator("name", mode="before")
    @classmethod
    def printable_name(cls, value: object) -> object:
        if isinstance(value, str) and any(
            category(character).startswith("C") for character in value
        ):
            raise ValueError("Knowledge base names must not contain control characters")
        return value


class KnowledgeBaseCreate(KnowledgeBaseRename):
    client_request_id: UUID
