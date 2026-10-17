"""Bind explicit body citations without mistaking every E-number for a source.

This gate checks reference syntax, not factual truth. Bare business identifiers
outside a reference context still require the separate factual verifier.
"""

import re
from collections.abc import Collection, Mapping

from app.rag.evidence_spans import EvidenceSpanError, model_evidence

_ID = r"E[0-9]+"
_END = r"(?![A-Za-z0-9_]|[.-][A-Za-z0-9])"
_EXPLICIT = re.compile(r"\[\[ref:([^\]\r\n]*)\]\]", re.IGNORECASE)
_BRACKET = re.compile(r"(?<!\[)\[(" + _ID + r"(?:\s*[,，;；、]\s*" + _ID + r")*)\](?!\])")
_FULL_BRACKET_SPAN = re.compile(
    r"(?<!\[)\[(?P<id>" + _ID + r"):S[1-9][0-9]*:[0-9a-f]{16}\](?!\])",
)
_SPAN = re.compile(r"(?<![A-Za-z0-9_])" + _ID + r":S[1-9][0-9]*(?::[A-Za-z0-9]+)?" + _END)
_SPAN_LOCATION = re.compile(
    r"(?<![A-Za-z0-9_])(?:第\s*)?S[1-9][0-9]*\s*(?:行|页)"
    r"(?=$|[\s，。；、,.;:：)\]）】]|中|内|上|处|是|为)",
)
_PREFIX = re.compile(
    r"(?P<label>来源|出处|引用|证据|资料|文件|文档|记录|原文|"
    r"source|citation|evidence|reference|document)"
    r"\s*(?:为|是|见)?\s*[:：#]?\s*[（(\[【]?\s*(?P<id>" + _ID + r")" + _END,
    re.IGNORECASE,
)
_SUFFIX = re.compile(
    r"(?<![A-Za-z0-9_])(?P<id>" + _ID + r")\s*(?:的)?\s*"
    r"(?:来源|出处|引用|证据|原文|source|citation|evidence|reference)(?![A-Za-z])",
    re.IGNORECASE,
)
_BARE = re.compile(r"(?<![A-Za-z0-9_])" + _ID + _END)
_SOURCE_HEADINGS = frozenset({
    "来源", "出处", "引用", "证据", "source", "sources", "citation", "citations",
    "reference", "references",
})
_DOCUMENT_NOUNS = frozenset({"资料", "文件", "文档", "记录", "document"})


class SourceMentionError(ValueError):
    code = "answer_reference_invalid"


def canonicalize_selected_span_references(text: str, declared_ids: Collection[str],
                                           evidence: Mapping[str, str],
                                           support: list[dict]) -> str:
    """Map only proven current bracketed spans to their existing declared source.

    This changes reference notation, never factual prose, selected sources or
    supporting quotes. An exact catalog identity and its complete selected quote
    are both required. Internal span numbers never become physical line/page data.
    """
    if not _FULL_BRACKET_SPAN.search(text):
        return text
    declared = set(declared_ids)
    selected = {key: evidence[key] for key in declared if key in evidence}
    try:
        spans = {(source["id"], span["span_id"]): span["text"]
                 for source in model_evidence(selected) for span in source["spans"]}
    except EvidenceSpanError:
        raise SourceMentionError("invalid current source catalog") from None
    quotes = {(item["evidence_id"], item["quote"]) for item in support}

    def replace(match: re.Match) -> str:
        identifier, marker = match["id"], match.group()[1:-1]
        quote = spans.get((identifier, marker))
        if (identifier not in declared or quote is None or (identifier, quote) not in quotes):
            raise SourceMentionError("body span was not selected as supporting evidence")
        before, after = text[:match.start()], text[match.end():]
        if (re.search(r"(?:第|行号|页码|line|page)\s*$", before, re.IGNORECASE)
            or re.match(r"\s*(?:(?:行|页)(?=$|[\s，。；、,.;:：)\]）】]|中|内|上|处|是|为|号|数)"
                        r"|line\b|page\b)", after, re.IGNORECASE)):
            raise SourceMentionError("internal span cannot establish a line or page")
        return "[" + identifier + "]"

    return _FULL_BRACKET_SPAN.sub(replace, text)


def _literal(marker: str, originals: list[str]) -> bool:
    pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(marker) + _END)
    return any(pattern.search(original) for original in originals)


def _cells(line: str) -> list[str] | None:
    """Match the UI's table grammar, including escaped and code-span pipes."""
    parts, part, ticks, separators, index = [], "", 0, 0, 0
    while index < len(line):
        char = line[index]
        if char == "\\" and line[index:index + 2] == "\\|":
            part += "|"
            index += 2
            continue
        if char == "`":
            count = 1
            while index + count < len(line) and line[index + count] == "`":
                count += 1
            if not ticks:
                ticks = count
            elif ticks == count:
                ticks = 0
            part += "`" * count
            index += count
            continue
        if char == "|" and not ticks:
            parts.append(part.strip())
            part, separators = "", separators + 1
        else:
            part += char
        index += 1
    if not separators:
        return None
    parts.append(part.strip())
    if not parts[0]:
        parts.pop(0)
    if parts and not parts[-1]:
        parts.pop()
    return parts


def _table_rows(text: str):
    """Yield complete table rows and source columns with original character offsets."""
    lines = text.splitlines(keepends=True)
    starts, offset = [], 0
    for line in lines:
        starts.append(offset)
        offset += len(line)
    index = 0
    while index + 1 < len(lines):
        headings, separator = _cells(lines[index]), _cells(lines[index + 1])
        if (not headings or len(headings) > 24 or separator is None
            or len(headings) != len(separator)
            or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator)):
            index += 1
            continue
        columns = [column for column, cell in enumerate(headings)
                   if cell.strip(" *_`").casefold() in _SOURCE_HEADINGS]
        for row in (index, index + 1):
            yield starts[row], starts[row] + len(lines[row]), [], columns
        index += 2
        while index < len(lines):
            cells = _cells(lines[index])
            if cells is None or len(cells) != len(headings):
                break
            yield starts[index], starts[index] + len(lines[index]), cells, columns
            index += 1


def markdown_table_line_ranges(text: str) -> set[tuple[int, int]]:
    """Share table recognition with the verifier without reconstructing source text."""
    return {(start, end) for start, end, _cells, _columns in _table_rows(text)}


def source_reference_mentions(text: str, declared_ids: Collection[str],
                              evidence: Mapping[str, str]) -> list[tuple[int, int, str]]:
    """Check explicit reference syntax and return its original character ranges.

    Only selected originals may establish a business marker exemption. The
    explicit [[ref:...]] namespace is always reserved for server references.
    Bare device numbers are not references. Returned IDs allow each verified
    sentence/table row to bind its explicit attribution to the corresponding proof.
    """
    declared = set(declared_ids)
    originals = [evidence[key] for key in declared if key in evidence]
    mentions = []
    for match in _EXPLICIT.finditer(text):
        if match[1] not in declared:
            raise SourceMentionError("undeclared explicit source")
        mentions.append((match.start(), match.end(), match[1]))
    if "[[ref:" in _EXPLICIT.sub("", text).casefold():
        raise SourceMentionError("malformed explicit source")
    for match in _BRACKET.finditer(text):
        identifiers = _BARE.findall(match[1])
        if any(identifier not in declared for identifier in identifiers):
            if not _literal(match.group(), originals):
                raise SourceMentionError("undeclared bracketed source")
        else:
            mentions.extend((match.start(), match.end(), identifier) for identifier in identifiers)
    for pattern in (_SPAN, _SPAN_LOCATION):
        for match in pattern.finditer(text):
            if not _literal(match.group(), originals):
                raise SourceMentionError("internal span is not a source location")
    for match in _PREFIX.finditer(text):
        business_id = (match["label"].casefold() in _DOCUMENT_NOUNS
                       and _literal(match["id"], originals))
        if (match["id"] not in declared and not business_id
            and not _literal(match.group(), originals)):
            raise SourceMentionError("undeclared source in reference context")
        if match["id"] in declared and not business_id:
            mentions.append((match.start(), match.end(), match["id"]))
    for match in _SUFFIX.finditer(text):
        if match["id"] not in declared and not _literal(match.group(), originals):
            raise SourceMentionError("undeclared source in reference context")
        if match["id"] in declared:
            mentions.append((match.start(), match.end(), match["id"]))
    for start, end, cells, columns in _table_rows(text):
        for column in columns if cells else []:
            for identifier in _BARE.findall(cells[column]):
                if identifier not in declared:
                    raise SourceMentionError("undeclared table source")
                mentions.append((start, end, identifier))
    return mentions


def checked_source_mentions(text: str, declared_ids: Collection[str],
                            evidence: Mapping[str, str]) -> None:
    """Reject undeclared source syntax without adding sources or changing prose."""
    source_reference_mentions(text, declared_ids, evidence)
