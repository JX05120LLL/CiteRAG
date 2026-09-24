"""Preflight identity matching the locked SDK's RAW text normalization.

Keep this tiny stdlib projection available without the optional RAG extra. It is
checked against the pinned SDK in local contract tests; it does not change source
text or claim that original offsets can locate normalized engine chunks.
"""
import hashlib
import html
import re

_UNSAFE_CHARACTERS = re.compile(r"[\uD800-\uDFFF\uFFFE\uFFFF\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


def engine_content_hash(text: str) -> str:
    normalized = _UNSAFE_CHARACTERS.sub("", html.unescape(text.strip())).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
