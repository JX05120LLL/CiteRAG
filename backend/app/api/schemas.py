from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.validation import CapabilityState

__all__ = ["CapabilityState", "ConversationCreate", "ConversationView", "KnowledgeBaseView"]


class ConversationCreate(BaseModel):
    kb_id: UUID
    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ] = "新聊天"


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
