"""Provider-neutral immutable request and response values."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal

from app.providers.errors import ProviderBudgetExceeded


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class ProviderUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class Completion:
    model: str
    content: str
    finish_reason: str | None
    request_id: str | None
    usage: ProviderUsage


@dataclass(frozen=True)
class EmbeddingBatch:
    model: str
    vectors: tuple[tuple[float, ...], ...]
    request_id: str | None
    usage: ProviderUsage


@dataclass(frozen=True)
class RerankItem:
    index: int
    relevance_score: float


@dataclass(frozen=True)
class RerankBatch:
    model: str
    items: tuple[RerankItem, ...]
    request_id: str | None
    usage: ProviderUsage


@dataclass
class RequestBudget:
    limit: int
    used: int = field(default=0, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("request budget limit must be positive")

    async def reserve(self) -> None:
        async with self._lock:
            if self.used >= self.limit:
                raise ProviderBudgetExceeded(self.limit)
            self.used += 1
