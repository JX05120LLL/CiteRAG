"""Answer model boundary. Only the enabled query path invokes this adapter."""

import json
from collections.abc import AsyncIterator
from typing import Any, Protocol


class AnswerRuntime(Protocol):
    async def complete_answer(self, question: str, evidence: list[dict]) -> str: ...
    async def complete_summary(self, previous: str, turns: list[dict]) -> str: ...


class AnswerError(Exception):
    pass


class LightRAGAnswerAdapter:
    def __init__(self, runtime: AnswerRuntime):
        self.runtime = runtime

    async def answer(self, question: str, evidence: list[dict]) -> str:
        try:
            return await self.runtime.complete_answer(question, evidence)
        except Exception:
            raise AnswerError("answer_unavailable") from None

    async def answer_with_context(self, question: str, evidence: list[dict],
                                  context: dict) -> str:
        try:
            return await self.runtime.complete_answer(question, evidence, context)
        except Exception:
            raise AnswerError("answer_unavailable") from None

    async def stream_with_context(self, question: str, evidence: list[dict],
                                  context: dict) -> AsyncIterator[str]:
        try:
            stream = getattr(self.runtime, "stream_answer", None)
            if stream is None:
                yield await self.runtime.complete_answer(question, evidence, context)
            else:
                async for piece in stream(question, evidence, context):
                    yield piece
        except Exception:
            raise AnswerError("answer_unavailable") from None

    async def summarize(self, previous: str, turns: list[dict[str, str]]) -> str:
        try:
            return await self.runtime.complete_summary(previous, turns)
        except Exception:
            raise AnswerError("summary_unavailable") from None


def checked_answer(raw: str, evidence: dict[str, str]) -> tuple[str, str, list[str]]:
    """The model supplies prose and evidence IDs, never source metadata or URLs."""
    try:
        data: Any = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {"status", "text", "evidence_ids"}:
            raise ValueError
        status, text, identifiers = data["status"], data["text"], data["evidence_ids"]
        if status not in {"answered", "insufficient_evidence", "needs_clarification",
                          "conflicting_evidence"}:
            raise ValueError
        if not isinstance(text, str) or len(text) > 1500 or not isinstance(identifiers, list):
            raise ValueError
        if status == "answered" and (not text.strip() or len(identifiers) != 1):
            raise ValueError
        if len(identifiers) > 3 or len(set(identifiers)) != len(identifiers) or (
            any(not isinstance(identifier, str) or identifier not in evidence
                for identifier in identifiers)
        ):
            raise ValueError
        if status != "answered":
            return status, "", []
        if "http://" in text or "https://" in text:
            raise ValueError
        # This first slice publishes only extractive answers. The model may
        # select a relevant span, but it cannot invent new factual prose.
        if text.strip() not in evidence[identifiers[0]]:
            raise ValueError
        return status, text.strip(), identifiers
    except (ValueError, TypeError, KeyError):
        raise AnswerError("answer_unverifiable") from None
