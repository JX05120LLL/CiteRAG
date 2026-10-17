"""Strict ordinary-answer review contract, separate from tool planning and KB evidence."""

import json
import re
from dataclasses import dataclass
from typing import Literal
from unicodedata import category

from app.rag.task_constraints import enforce_constraints, validate_task_contract

ReviewIssue = Literal[
    "missing_action", "missing_result", "constraint_missed", "scope_drift",
    "unsupported_generalization", "unnecessary_repetition", "tool_grounding", "uncertainty",
    "precision",
]
REVIEW_ISSUES = frozenset(ReviewIssue.__args__)


class ResponseReviewError(ValueError):
    """A review failed its bounded internal contract; unsafe drafts stay unpublished."""


@dataclass(frozen=True)
class GeneralReview:
    accepted: bool
    issues: tuple[ReviewIssue, ...]
    revision: str | None
    version: int = 1
    checks: tuple[dict, ...] = ()


def checked_general_answer(raw: str | dict) -> str:
    """Retain the ordinary response contract without accepting tool/KB answer fields."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        if (not isinstance(data, dict) or set(data) != {"text"}
            or not isinstance(data["text"], str) or not data["text"].strip()
            or len(data["text"]) > 1000
            or re.search(r"https?://", data["text"], re.IGNORECASE)
            or any(category(char).startswith("C") and char not in "\n\t"
                   for char in data["text"])):
            raise ValueError
        return data["text"].strip()
    except (ValueError, TypeError, RecursionError):
        raise ResponseReviewError("answer_unverifiable") from None


def parse_general_review(raw: str | dict, *, contract: dict | None = None,
                         strict: bool = False, draft_text: str | None = None) -> GeneralReview:
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        fields = {"accepted", "issues", "revision"} | ({"version", "checks"} if strict else set())
        if (not isinstance(data, dict) or set(data) != fields
            or type(data["accepted"]) is not bool or not isinstance(data["issues"], list)
            or len(data["issues"]) > 4
            or any(not isinstance(issue, str) or issue not in REVIEW_ISSUES
                   for issue in data["issues"])
            or len(set(data["issues"])) != len(data["issues"])):
            raise ValueError
        if data["accepted"]:
            if data["issues"] or data["revision"] is not None:
                raise ValueError
        else:
            if not data["issues"]:
                raise ValueError
            checked_general_answer({"text": data["revision"]})
        checks = ()
        if strict:
            validate_task_contract(contract)
            if type(data["version"]) is not int or data["version"] != 2:
                raise ValueError
            candidate = checked_general_answer({"text":
                draft_text if data["accepted"] else data["revision"]})
            enforce_constraints(candidate, contract)
            checks = _coverage(data["checks"], candidate, contract)
        return GeneralReview(data["accepted"], tuple(data["issues"]), data["revision"],
                             2 if strict else 1, checks)
    except (ValueError, TypeError, RecursionError):
        raise ResponseReviewError("answer_unverifiable") from None


def _coverage(value: object, candidate: str, contract: dict) -> tuple[dict, ...]:
    required = {"request", "scope", "precision"} | {item["id"] for item in contract["requirements"]}
    if not isinstance(value, list) or len(value) != len(required):
        raise ValueError
    seen = set()
    for item in value:
        if (not isinstance(item, dict)
            or set(item) != {"requirement_id", "verdict", "quote", "reason"}
            or not isinstance(item["requirement_id"], str)
            or item["requirement_id"] not in required or item["requirement_id"] in seen
            or not isinstance(item["verdict"], str)
            or item["verdict"] not in {"met", "unmet", "not_applicable"}
            or not isinstance(item["quote"], str) or len(item["quote"]) > 400
            or not isinstance(item["reason"], str) or not 1 <= len(item["reason"].strip()) <= 160
            or any(category(char).startswith("C") and char not in "\n\t"
                   for char in item["reason"])):
            raise ValueError
        seen.add(item["requirement_id"])
        if item["verdict"] == "not_applicable":
            if item["requirement_id"] != "precision" or item["quote"]:
                raise ValueError
        elif (item["verdict"] != "met" or not item["quote"].strip()
              or item["quote"] not in candidate):
            raise ValueError
    if seen != required:
        raise ValueError
    return tuple(value)


def checked_general_review(raw: str | dict, draft_text: str, *, contract: dict | None = None,
                            strict: bool = False) -> str:
    draft = checked_general_answer({"text": draft_text})
    decision = parse_general_review(raw, contract=contract, strict=strict, draft_text=draft)
    return draft if decision.accepted else checked_general_answer({"text": decision.revision})
