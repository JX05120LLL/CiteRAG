"""Attempt-scoped local embedding diagnostics with no input or supplier text.

The SDK can catch an embedding exception inside a child task and only record a
FAILED document. An inherited mutable flag carries this one safe cause back to
the adapter; each adapter call owns a fresh flag and restores its parent context.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import NoReturn

from app.services.token_budget import TokenBudgetExceeded


@dataclass(slots=True)
class EmbeddingFailure:
    input_limit_exceeded: bool = False


_FAILURE: ContextVar[EmbeddingFailure | None] = ContextVar("embedding_failure", default=None)


@contextmanager
def capture_embedding_failures() -> Iterator[EmbeddingFailure]:
    failure = EmbeddingFailure()
    token = _FAILURE.set(failure)
    try:
        yield failure
    finally:
        _FAILURE.reset(token)


def raise_embedding_input_limit() -> NoReturn:
    failure = _FAILURE.get()
    if failure is not None:
        failure.input_limit_exceeded = True
    raise TokenBudgetExceeded("embedding input budget exceeded")
