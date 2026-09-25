"""Expose only literal source spans from an unfinished model JSON response."""

import json
import re


class ExtractiveDraft:
    def __init__(self, evidence: dict[str, str]):
        self._evidence = tuple(evidence.values())
        self._raw = ""
        self._sent = ""

    def feed(self, fragment: str) -> str:
        if not isinstance(fragment, str) or len(self._raw) + len(fragment) > 8192:
            raise ValueError("Answer stream exceeds the fixed budget")
        self._raw += fragment
        match = re.search(r'"text"\s*:\s*"', self._raw)
        if match is None:
            return ""
        start = match.end()
        escaped = False
        end = len(self._raw)
        for index in range(start, len(self._raw)):
            char = self._raw[index]
            if char == '"' and not escaped:
                end = index
                break
            if char == "\\" and not escaped:
                escaped = True
            else:
                escaped = False
        try:
            candidate = json.loads('"' + self._raw[start:end] + '"')
        except (ValueError, TypeError):
            return ""
        if (not isinstance(candidate, str) or len(candidate) > 1500
            or not candidate.startswith(self._sent)
            or "http://" in candidate or "https://" in candidate
            or not any(candidate in source for source in self._evidence)):
            return ""
        delta = candidate[len(self._sent):]
        self._sent = candidate
        return delta

    @property
    def text(self) -> str:
        return self._sent
