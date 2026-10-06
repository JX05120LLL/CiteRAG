"""Map sanitized LightRAG text back to a unique parsed original span.

The fixed SDK strips, HTML-unescapes, removes invalid controls, then strips
again. Character spans keep the original byte-independent Python offsets. A
location is returned only when an entire engine chunk fits one parsed block.
"""

import html
import re
from array import array
from dataclasses import dataclass
from typing import Protocol

_ENTITY = re.compile(r"&(?:#[xX][0-9a-fA-F]+|#[0-9]+|[A-Za-z][A-Za-z0-9]*);?")
_UNSAFE = re.compile(r"[\uD800-\uDFFF\uFFFE\uFFFF\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


class SourceBlock(Protocol):
    text: str
    start: int
    end: int
    locator: dict


@dataclass(frozen=True)
class LocatedChunk:
    quote: str
    locator: dict
    start: int
    end: int


class SpanMap:
    """Compact source offsets; millions of characters must not allocate tuples."""

    def __init__(self):
        self.starts = array("I")
        self.ends = array("I")

    def append(self, start: int, end: int) -> None:
        self.starts.append(start)
        self.ends.append(end)

    def trim(self, left: int, right: int) -> "SpanMap":
        self.starts = self.starts[left:right]
        self.ends = self.ends[left:right]
        return self

    def __getitem__(self, index: int) -> tuple[int, int]:
        return self.starts[index], self.ends[index]

    def __len__(self) -> int:
        return len(self.starts)


def normalized_source(original: str) -> tuple[str, SpanMap]:
    """Return SDK-normalized text plus each character's original span."""
    first = len(original) - len(original.lstrip())
    last = len(original.rstrip())
    if first >= last:
        return "", SpanMap()
    characters: list[str] = []
    spans = SpanMap()
    position = first
    while position < last:
        match = _ENTITY.match(original, position) if original[position] == "&" else None
        token = match.group() if match else original[position]
        decoded = html.unescape(token)
        end = position + len(token)
        for character in decoded:
            if not _UNSAFE.fullmatch(character):
                characters.append(character)
                spans.append(position, end)
        position = end
    joined = "".join(characters)
    left = len(joined) - len(joined.lstrip())
    right = len(joined.rstrip())
    return joined[left:right], spans.trim(left, right)


def locate_chunk(chunk: str, original: str, blocks: list[SourceBlock]) -> LocatedChunk | None:
    """Refuse ambiguous, altered, or cross-block text rather than invent a locator."""
    from lightrag.utils import sanitize_text_for_encoding

    normalized, spans = normalized_source(original)
    if (not chunk or not normalized or not spans
        or normalized != sanitize_text_for_encoding(original)):
        return None
    position = normalized.find(chunk)
    if position < 0 or normalized.find(chunk, position + 1) >= 0:
        return None
    start, end = spans[position][0], spans[position + len(chunk) - 1][1]
    quote = original[start:end]
    if normalized_source(quote)[0] != chunk:
        return None
    matches = [block for block in blocks if block.start <= start and end <= block.end
               and original[block.start:block.end] == block.text]
    if len(matches) == 1:
        return LocatedChunk(quote, matches[0].locator, start, end)
    if matches:
        return None

    # Markdown/plain-text parsing records each paragraph as a separate line
    # block, while LightRAG can index a chunk spanning several paragraphs.
    # The full chunk has already been matched uniquely against this document.
    # Join only verified line blocks separated by whitespace; PDF pages and
    # DOCX paragraphs retain their stricter single-location requirement.
    covered = sorted((block for block in blocks
                      if block.start < end and start < block.end),
                     key=lambda block: block.start)
    if (len(covered) < 2 or not covered[0].start <= start < covered[0].end
            or not covered[-1].start < end <= covered[-1].end):
        return None
    cursor = start
    for block in covered:
        locator = block.locator
        if (block.start < 0 or block.end > len(original)
                or original[block.start:block.end] != block.text
                or not isinstance(locator, dict) or locator.get("kind") != "lines"
                or type(locator.get("line_start")) is not int
                or type(locator.get("line_end")) is not int
                or locator["line_start"] != original.count("\n", 0, block.start) + 1
                or locator["line_end"] != original.count("\n", 0, block.end - 1) + 1
                or block.start < cursor and block is not covered[0]
                or (block.start > cursor and original[cursor:block.start].strip())):
            return None
        cursor = block.end
    return LocatedChunk(quote, {"kind": "lines",
                                "line_start": covered[0].locator["line_start"],
                                "line_end": covered[-1].locator["line_end"]},
                        start, end)
