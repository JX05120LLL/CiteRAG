"""Server-defined coverage for factual review; model acceptance alone is not proof."""

from __future__ import annotations

import hashlib
import json
import math
import re

from app.rag.evidence_spans import EvidenceSpanError, model_evidence
from app.rag.source_mentions import markdown_table_line_ranges, source_reference_mentions

MAX_VERIFICATION_UNITS = 24
MAX_AUDIT_CHARS = 24_000


class VerificationAuditError(ValueError):
    """Missing coverage or invalid source identity, not a semantic verdict."""


def _unique_audit_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate audit field")
        result[key] = value
    return result


def verification_units(text: str) -> list[dict]:
    """Cover every character, retaining table rows and complete prose sentences.

    No model chooses which claims to omit. Adjacent clauses are grouped only
    when needed to bound the existing one-request review and output budget.
    """
    if not isinstance(text, str) or not text.strip() or len(text) > 1500:
        raise VerificationAuditError("invalid answer size")
    ends = []
    offset = 0
    table_rows = markdown_table_line_ranges(text)
    for line in text.splitlines(keepends=True):
        if (offset, offset + len(line)) in table_rows:
            if offset and (not ends or ends[-1] != offset):
                ends.append(offset)
            ends.append(offset + len(line))
        else:
            ends.extend(offset + match.end() for match in re.finditer(
                r"[。！？!?；;]|\.(?=\s|$)", line))
            # A soft line wrap must not detach an attribution from its predicate.
            if not line.strip() and (not ends or ends[-1] != offset + len(line)):
                ends.append(offset + len(line))
        offset += len(line)
    if not ends or ends[-1] != len(text):
        ends.append(len(text))
    ranges, start = [], 0
    for end in ends:
        if end <= start:
            continue
        if not text[start:end].strip() and ranges:
            ranges[-1] = (ranges[-1][0], end)
        else:
            ranges.append((start, end))
        start = end
    group = math.ceil(len(ranges) / MAX_VERIFICATION_UNITS)
    ranges = [(ranges[index][0], ranges[min(index + group, len(ranges)) - 1][1])
              for index in range(0, len(ranges), group)]
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]
    return [{"id": f"U{index}:{digest}", "start": start, "end": end,
             "text": text[start:end]} for index, (start, end) in enumerate(ranges, 1)]


def checked_verification_audit(raw: str, text: str, selected: list[dict]) -> bool:
    """Validate complete unit/source coverage, then derive the aggregate verdict.

    Exact spans establish provenance, not natural-language entailment. A model
    may still judge a claim incorrectly; this contract makes omissions visible.
    """
    try:
        if not isinstance(raw, str) or len(raw) > MAX_AUDIT_CHARS:
            raise ValueError
        data = json.loads(raw, object_pairs_hook=_unique_audit_object)
        units = {unit["id"]: unit for unit in verification_units(text)}
        if not isinstance(data, dict) or set(data) not in ({"units"}, {"answer_units"}):
            raise ValueError
        checks = next(iter(data.values()))
        if not isinstance(checks, list) or len(checks) != len(units):
            raise ValueError
        spans = {(source["id"], span["span_id"])
                 for source in model_evidence(selected) for span in source["spans"]}
        mentions = source_reference_mentions(
            text, [source["id"] for source in selected],
            {source["id"]: source["text"] for source in selected},
        )
        seen, supported = set(), True
        for check in checks:
            if (not isinstance(check, dict) or set(check) != {"unit_id", "verdict", "support"}
                or not isinstance(check["unit_id"], str) or check["unit_id"] not in units
                or check["unit_id"] in seen
                or check["verdict"] not in {"supported", "unsupported", "uncertain"}
                or not isinstance(check["support"], list) or len(check["support"]) > 3):
                raise ValueError
            seen.add(check["unit_id"])
            references = set()
            for reference in check["support"]:
                if (not isinstance(reference, dict)
                    or set(reference) != {"evidence_id", "span_id"}
                    or not isinstance(reference["evidence_id"], str)
                    or not isinstance(reference["span_id"], str)):
                    raise ValueError
                identity = reference["evidence_id"], reference["span_id"]
                if identity not in spans or identity in references:
                    raise ValueError
                references.add(identity)
            if check["verdict"] == "supported":
                unit = units[check["unit_id"]]
                required = {identifier for start, end, identifier in mentions
                            if start < unit["end"] and end > unit["start"]}
                if not required.issubset({identifier for identifier, _span in references}):
                    raise ValueError
                if not references and any(char.isalnum()
                                          for char in unit["text"]):
                    raise ValueError
            else:
                supported = False
        if seen != set(units):
            raise ValueError
        return supported
    except (ValueError, TypeError, KeyError, RecursionError, EvidenceSpanError):
        raise VerificationAuditError("invalid verification coverage or source proof") from None
