"""Small, conservative format contracts compiled from the original user's words.

Only explicit positive instructions are compiled. Quoted/code text, uncertain
limits and conflicting values remain outside this grammar; no topic rules are used.
"""

import hashlib
import json
import re

KINDS = frozenset({"max_chars", "exact_lines", "exact_sentences"})
NUMBER = r"[0-9零一二两三四五六七八九十百千]+"
EXACT = re.compile(rf"(?:用|分成|分为)\s*({NUMBER})\s*(行|句话|句)")
MAX_CHARS = re.compile(
    rf"(?:控制在|不超过|不要超过|最多(?:用)?|至多(?:用)?|限(?:制在)?|用)"
    rf"\s*({NUMBER})\s*(?:个)?(?:字符|字)(?:以内|之内|以下|内)?"
)
CLAUSES = re.compile(r"([^，,。！？!?；;\n]+)([，,。！？!?；;\n]?)")
PREFIX_TOKENS = tuple(sorted(("请", "请你", "回答", "答复", "正文", "本次", "这次", "你", "再",
    "并", "且", "请将", "将", "请把", "把", "最好", "只", "仅"), key=len, reverse=True))
OUTPUT_VERBS = ("列出", "回答", "答复", "说明", "概括", "总结", "解释", "描述", "介绍",
                "写出", "给出", "表达", "呈现", "作答", "输出", "鼓励", "安慰", "回应", "写", "说")
OUTPUT_QUALIFIERS = ("分别", "简要", "简短", "简单", "清楚", "清晰", "直接", "来")


def _directive_prefix(value: str) -> bool:
    """Consume each instruction token once; overlapping tokens never backtrack."""
    offset = 0
    while offset < len(value):
        token = next((token for token in PREFIX_TOKENS if value.startswith(token, offset)), None)
        if token is None:
            return False
        offset += len(token)
    return True


def _output_suffix(value: str) -> bool:
    """A count followed by an input noun or a question is not an output contract."""
    suffix = value.strip()
    if re.search(r"或|左右|大约|约莫|至|到|[-~～—]|吗|么|是否|能否|可否", suffix):
        return False
    if not suffix:
        return True
    offset = 0
    while offset < len(suffix):
        qualifier = next((word for word in OUTPUT_QUALIFIERS
                          if suffix.startswith(word, offset)), None)
        if qualifier is None:
            break
        offset += len(qualifier)
    return any(suffix.startswith(verb, offset) for verb in OUTPUT_VERBS)


class TaskConstraintError(ValueError):
    """The frozen task contract is malformed or its candidate fails a hard limit."""


def _number(value: str) -> int | None:
    if value.isascii() and value.isdecimal():
        if len(value) > 4:
            return None
        result = int(value)
        return result if 1 <= result <= 1000 else None
    digits = {char: number for number, char in enumerate("零一二三四五六七八九")}
    digits["两"] = 2
    # Reject ambiguous digit strings instead of interpreting the last digit silently.
    if re.fullmatch(r"(?:[一二两三四五六七八九]|[一二三四五六七八九]?十[一二三四五六七八九]?|"
                    r"[一二两三四五六七八九]百(?:零[一二三四五六七八九]|"
                    r"[一二三四五六七八九]十[一二三四五六七八九]?)?|一千)", value) is None:
        return None
    if len(value) == 1 and value in digits:
        return digits[value] or None
    if not any(unit in value for unit in "十百千"):
        return None
    result, digit, previous = 0, 0, 10000
    for char in value:
        if char in digits:
            digit = digits[char]
        elif char in "十百千":
            unit = {"十": 10, "百": 100, "千": 1000}[char]
            if unit >= previous:
                return None
            result += (digit or 1) * unit
            digit, previous = 0, unit
        else:
            return None
    result += digit
    return result if 1 <= result <= 1000 else None


def _mask_data(text: str) -> str:
    def mask(match):
        return " " * len(match.group())
    # Preserve offsets so source_quote always names the original user substring.
    text = re.sub(r"(?ms)^\s*(`{3,}|~{3,})[^\n]*\n.*?(?:^\s*\1\s*$|\Z)", mask, text)
    text = re.sub(r"(?m)^\s*>.*$", mask, text)
    for pattern in (r"`[^`\n]*`", r'"[^"\n]*"', r"'[^'\n]*'", r"“[^”]*”", r"‘[^’]*’",
                    r"「[^」]*」", r"『[^』]*』"):
        text = re.sub(pattern, mask, text)
    return text


def make_task_contract(original_user_text: str) -> dict:
    if not isinstance(original_user_text, str):
        raise TaskConstraintError("invalid original question")
    selected: list[tuple[int, str, int, str]] = []
    for clause in CLAUSES.finditer(_mask_data(original_user_text)):
        if clause.group(2) in {"?", "？"}:
            continue
        value = clause.group(1).strip()
        for pattern, default_kind in ((EXACT, None), (MAX_CHARS, "max_chars")):
            for match in pattern.finditer(value):
                if not _directive_prefix(value[:match.start()].strip()):
                    continue
                suffix = value[match.end():]
                if not _output_suffix(suffix):
                    continue
                number = _number(match.group(1))
                if number is None:
                    continue
                kind = default_kind or ("exact_lines" if match.group(2) == "行"
                                        else "exact_sentences")
                selected.append((clause.start() + match.start(), kind, number, match.group()))
    requirements = []
    for _offset, kind, number, quote in sorted(selected):
        if len({candidate[2] for candidate in selected if candidate[1] == kind}) != 1:
            continue
        if any(item["kind"] == kind for item in requirements):
            continue
        requirements.append({"id": f"C{len(requirements) + 1}", "kind": kind,
                             "value": number, "source_quote": quote})
    return {"version": 1, "source_hash": hashlib.sha256(original_user_text.encode()).hexdigest(),
            "requirements": requirements}


def validate_task_contract(contract: dict, original_user_text: str | None = None) -> dict:
    if (not isinstance(contract, dict)
        or set(contract) != {"version", "source_hash", "requirements"}
        or type(contract["version"]) is not int or contract["version"] != 1
        or not isinstance(contract["source_hash"], str)
        or re.fullmatch(r"[0-9a-f]{64}", contract["source_hash"]) is None
        or not isinstance(contract["requirements"], list) or len(contract["requirements"]) > 4):
        raise TaskConstraintError("invalid task contract")
    seen = set()
    for index, item in enumerate(contract["requirements"], 1):
        if (not isinstance(item, dict) or set(item) != {"id", "kind", "value", "source_quote"}
            or item["id"] != f"C{index}" or not isinstance(item["kind"], str)
            or item["kind"] not in KINDS or item["kind"] in seen
            or type(item["value"]) is not int or not 1 <= item["value"] <= 1000
            or not isinstance(item["source_quote"], str)
            or not 1 <= len(item["source_quote"]) <= 60):
            raise TaskConstraintError("invalid task requirement")
        seen.add(item["kind"])
    if original_user_text is not None and contract != make_task_contract(original_user_text):
        raise TaskConstraintError("task source changed")
    return contract


def contract_hash(contract: dict) -> str:
    validate_task_contract(contract)
    encoded = json.dumps(contract, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def check_constraints(text: str, contract: dict) -> list[dict]:
    validate_task_contract(contract)
    if not isinstance(text, str):
        raise TaskConstraintError("invalid candidate")
    metrics = {
        "max_chars": sum(not char.isspace() for char in text),
        "exact_lines": sum(bool(line.strip()) for line in text.splitlines()),
        "exact_sentences": sum(bool(part.strip(" \n\t\r\"'“”‘’「」『』")) for part in re.split(
            r"[。！？!?]+|(?<!\d)\.|\.(?!\d)", text)),
    }
    return [{"requirement_id": item["id"], "kind": item["kind"], "expected": item["value"],
             "actual": metrics[item["kind"]], "met": (metrics[item["kind"]] <= item["value"]
             if item["kind"] == "max_chars" else metrics[item["kind"]] == item["value"])}
            for item in contract["requirements"]]


def enforce_constraints(text: str, contract: dict) -> str:
    if any(not item["met"] for item in check_constraints(text, contract)):
        raise TaskConstraintError("answer task constraint unmet")
    return text
