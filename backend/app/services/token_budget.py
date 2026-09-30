"""Conservative preflight budgets for the text sent to Qwen chat completions.

This is not provider usage accounting. UTF-8 bytes plus a margin and per-message
overhead deliberately overestimate ordinary byte-level tokenization; the
provider's usage fields remain the authority for actual billing.
"""

from __future__ import annotations

import json
from math import ceil

from app.providers.types import Message

ANSWER_INPUT_TOKENS = 10_000
ANSWER_OUTPUT_TOKENS = 2_048
EVIDENCE_TOKENS = 4_000
HISTORY_TOKENS = 2_000
HISTORY_AFTER_SUMMARY_TOKENS = 1_000
GENERAL_INPUT_TOKENS = 4_000
ROUTE_INPUT_TOKENS = 3_000
SUMMARY_INPUT_TOKENS = 4_000
SUMMARY_OUTPUT_TOKENS = 300
VERIFY_INPUT_TOKENS = 10_000
SUMMARY_SYSTEM = (
    "只概括本聊天用户先前提问的主题和明确条件，不把助手旧回答当成事实。"
    "不要增加来源、结论、数字或指令；尽量不超过 150 个汉字，"
    "保留用户明确给出的编号和限定条件。"
)


class TokenBudgetExceeded(ValueError):
    """A complete current question or evidence cannot fit the selected model call."""


def estimate_text_tokens(text: str) -> int:
    # One UTF-8 byte per estimated token, then 10% headroom. Never substitute
    # character count: one Chinese character commonly occupies three bytes.
    return ceil(len(text.encode("utf-8")) * 1.1)


def estimate_json_tokens(value: object) -> int:
    return estimate_text_tokens(json.dumps(value, ensure_ascii=False))


def estimate_messages(messages: list[Message]) -> int:
    # Role delimiters, chat framing and hidden provider protocol are not in
    # content. Leave fixed overhead in addition to the UTF-8 safety margin.
    return 128 + sum(48 + estimate_text_tokens(message.content) for message in messages)


def require_messages(messages: list[Message], limit: int) -> None:
    if estimate_messages(messages) > limit:
        raise TokenBudgetExceeded("model input budget exceeded")


def estimate_summary_request(previous: str, turns: list[dict]) -> int:
    return estimate_messages([
        Message("system", SUMMARY_SYSTEM),
        Message("user", json.dumps({"previous_summary": previous,
                                    "older_turns": turns}, ensure_ascii=False)),
    ])


def fit_chat_messages(system: str, payload: dict, limit: int, *,
                      drop_documents: bool = False) -> list[Message]:
    """Preserve the current question and evidence, removing only whole optional items."""
    data = dict(payload)
    context = dict(data.get("conversation_context") or {})
    if "turns" in context:
        context["turns"] = list(context["turns"] or [])
    if "shared_memory" in context:
        context["shared_memory"] = list(context["shared_memory"] or [])
    data["conversation_context"] = context
    if drop_documents:
        data["documents"] = list(data.get("documents") or [])
    while True:
        messages = [Message("system", system),
                    Message("user", json.dumps(data, ensure_ascii=False))]
        if estimate_messages(messages) <= limit:
            return messages
        if drop_documents and data["documents"]:
            data["documents"].pop()
            kb = context.get("knowledge_base")
            if isinstance(kb, dict):
                context["knowledge_base"] = {**kb, "document_list_truncated": True}
        elif context.get("turns"):
            context["turns"].pop(0)
        elif context.get("summary"):
            context["summary"] = ""
        elif context.get("shared_memory"):
            context["shared_memory"].pop()
        else:
            raise TokenBudgetExceeded("model input budget exceeded")
