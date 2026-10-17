"""Select frozen source spans instead of asking a model to copy source characters.

This is an internal generation format. Saved answers still use the existing
canonical quote/reference validation and never expose model-authored locators.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence

MAX_SPAN_CHARS = 600
MAX_EVIDENCE_CHARS = 6000
MAX_EVIDENCE_ITEMS = 3
MAX_SPANS_PER_ITEM = 32


class EvidenceSpanError(ValueError):
    code = "answer_reference_invalid"


def _chunks(text: str) -> list[str]:
    """Keep normal paragraphs whole, with exact whitespace and bounded long spans."""
    paragraphs = re.findall(r".*?(?:\n\s*\n|\Z)", text, re.DOTALL)
    paragraphs = [part for part in paragraphs if part]
    if len(paragraphs) > MAX_SPANS_PER_ITEM:
        paragraphs = [text]
    spans = []
    for paragraph in paragraphs:
        start = 0
        while start < len(paragraph):
            stop = min(start + MAX_SPAN_CHARS, len(paragraph))
            if stop < len(paragraph):
                # Prefer a sentence boundary, but never skip or reconstruct source text.
                boundaries = list(re.finditer(r"[。！？!?；;]\s*", paragraph[start:stop]))
                if boundaries:
                    stop = start + boundaries[-1].end()
            spans.append(paragraph[start:stop])
            start = stop
    if len(spans) > MAX_SPANS_PER_ITEM:
        spans = [text[index:index + MAX_SPAN_CHARS]
                 for index in range(0, len(text), MAX_SPAN_CHARS)]
    return spans


def model_evidence(evidence: Sequence[dict] | Mapping[str, str]) -> list[dict]:
    """Build stable, bounded IDs bound to the evidence identity and complete source text."""
    items = ([{"id": key, "text": value} for key, value in evidence.items()]
             if isinstance(evidence, Mapping) else evidence)
    if len(items) > MAX_EVIDENCE_ITEMS:
        raise EvidenceSpanError("too many evidence items")
    catalog, known, total = [], set(), 0
    for item in items:
        identifier, text = item.get("id"), item.get("text")
        if (not isinstance(identifier, str) or not identifier or len(identifier) > 80
            or identifier in known or not isinstance(text, str) or not text.strip()):
            raise EvidenceSpanError("invalid evidence identity or source")
        total += len(text)
        if total > MAX_EVIDENCE_CHARS:
            raise EvidenceSpanError("evidence character budget exceeded")
        known.add(identifier)
        digest = hashlib.sha256((identifier + "\0" + text).encode("utf-8")).hexdigest()[:16]
        entry = {"id": identifier, "spans": [
            {"span_id": f"{identifier}:S{index}:{digest}", "text": part}
            for index, part in enumerate(_chunks(text), 1)
        ]}
        if "source" in item:
            entry["source"] = item["source"]
        catalog.append(entry)
    return catalog


def generation_evidence(evidence: Sequence[dict] | Mapping[str, str]) -> list[dict]:
    """Offer per-source indices within this frozen call, without hash-copying tasks.

    The verifier retains the hash-bound model_evidence catalog. These generation
    indices resolve only against the evidence passed to expand_span_support; they
    are not document positions or transferable references across answer attempts.
    """
    catalog = []
    for source in model_evidence(evidence):
        entry = {"id": source["id"], "spans": [
            {"span_index": index, "text": span["text"]}
            for index, span in enumerate(source["spans"], 1)
        ]}
        if "source" in source:
            entry["source"] = source["source"]
        catalog.append(entry)
    return catalog


def expand_span_support(raw: str, evidence: Sequence[dict] | Mapping[str, str]) -> str:
    """Expand selected spans once; legacy quotes are left unchanged, never repaired."""
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("support"), list):
        return raw
    support = data["support"]
    if not any(isinstance(item, dict) and ("span_id" in item or "span_index" in item)
               for item in support):
        return raw
    if not 1 <= len(support) <= 6:
        raise EvidenceSpanError("invalid support count")
    selector = "span_index" if isinstance(support[0], dict) and "span_index" in support[0] \
        else "span_id"
    lookup = {(entry["id"], index if selector == "span_index" else span["span_id"]): span["text"]
              for entry in model_evidence(evidence)
              for index, span in enumerate(entry["spans"], 1)}
    expanded = []
    for item in support:
        if (not isinstance(item, dict) or set(item) != {"evidence_id", selector}
            or not isinstance(item["evidence_id"], str)):
            raise EvidenceSpanError("mixed or malformed span support")
        value = item[selector]
        if ((selector == "span_index" and (not isinstance(value, int)
                                          or isinstance(value, bool) or value < 1))
            or (selector == "span_id" and not isinstance(value, str))):
            raise EvidenceSpanError("invalid span selector")
        quote = lookup.get((item["evidence_id"], value))
        if quote is None:
            raise EvidenceSpanError("span does not belong to frozen evidence")
        expanded.append({"evidence_id": item["evidence_id"], "quote": quote})
    data["support"] = expanded
    return json.dumps(data, ensure_ascii=False, allow_nan=False)
