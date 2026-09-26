"""Answer model boundary. Only the enabled query path invokes this adapter."""

import json
import re
from collections.abc import AsyncIterator
from typing import Any, Protocol
from unicodedata import category


class AnswerRuntime(Protocol):
    async def complete_answer(self, question: str, evidence: list[dict]) -> str: ...
    async def complete_summary(self, previous: str, turns: list[dict]) -> str: ...
    async def route_question(self, question: str, candidates: list[dict]) -> str: ...


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

    async def route_query(self, question: str, candidates: list[dict]) -> str:
        try:
            return await self.runtime.route_question(question, candidates)
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


def checked_route(raw: str, candidates: list[dict], question: str) -> dict:
    """A model can select only confirmed attributes or a literal span of the question."""
    try:
        data: Any = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError
        mode = data.get("mode")
        if mode in {"semantic", "needs_clarification", "unsupported"}:
            if set(data) != {"mode"}:
                raise ValueError
            return {"mode": mode}
        if mode == "literal":
            if set(data) != {"mode", "phrase"}:
                raise ValueError
            phrase = data["phrase"]
            if (not isinstance(phrase, str) or phrase != phrase.strip()
                or not 2 <= len(phrase) <= 80 or not any(char.isalnum() for char in phrase)
                or any(category(char).startswith("C") for char in phrase)
                or re.search(r"(?<![A-Za-z0-9])" + re.escape(phrase) +
                             r"(?![A-Za-z0-9])", question) is None):
                raise ValueError
            return {"mode": "literal", "phrase": phrase}
        if mode != "exact" or set(data) != {"mode", "candidate_ids"}:
            raise ValueError
        identifiers = data["candidate_ids"]
        if (not isinstance(identifiers, list) or not 1 <= len(identifiers) <= 3
            or len(set(identifiers)) != len(identifiers)):
            raise ValueError
        known = {candidate["id"]: candidate for candidate in candidates}
        filters = {}
        for identifier in identifiers:
            if not isinstance(identifier, str) or identifier not in known:
                raise ValueError
            candidate = known[identifier]
            field = candidate["field"]
            if field in filters or field not in {"doc_code", "model_code", "edition"}:
                raise ValueError
            filters[field] = candidate["value"]
        return {"mode": "exact", "filters": filters}
    except (ValueError, TypeError, KeyError):
        raise AnswerError("answer_unverifiable") from None


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
            if not text.strip():
                raise ValueError
            raise AnswerError("answer_reference_invalid")
        if (len(identifiers) > 3 or any(
            not isinstance(identifier, str) or identifier not in evidence
            for identifier in identifiers)
            or len(set(identifiers)) != len(identifiers)):
            raise AnswerError("answer_reference_invalid")
        if status != "answered":
            return status, "", []
        if "http://" in text or "https://" in text:
            raise AnswerError("answer_source_mismatch")
        # This first slice publishes only extractive answers. The model may
        # select a relevant span, but it cannot invent new factual prose.
        if text.strip() not in evidence[identifiers[0]]:
            raise AnswerError("answer_source_mismatch")
        return status, text.strip(), identifiers
    except (ValueError, TypeError, KeyError):
        raise AnswerError("answer_format_invalid") from None
