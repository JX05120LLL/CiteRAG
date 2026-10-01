"""Reviewed tool schemas and bounded, untrusted results; no remote discovery."""

import hashlib
import json

from app.services.token_budget import estimate_json_tokens


def bounded_model_results(results: list[dict], budget: int = 3000) -> list[dict]:
    """Keep status/identity while truncating untrusted data before model context assembly."""
    value = [dict(item) for item in results]
    for item in value:
        if estimate_json_tokens(value) <= budget:
            break
        item["data"] = None
        item["truncated"] = True
    if estimate_json_tokens(value) > budget:
        raise ValueError("Tool result metadata exceeds the model budget")
    return value


def arguments_fingerprint(arguments: dict) -> str:
    if not isinstance(arguments, dict):
        raise ValueError("Arguments must be an object")
    encoded = json.dumps(
        arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    if len(encoded) > 2048:
        raise ValueError("Arguments exceed the review budget")
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def normalize_mcp_result(result: dict) -> dict:
    failed = {
        "status": "failed",
        "error_code": "mcp_tool_failed",
        "data": None,
        "truncated": False,
        "source_type": "tool",
    }
    if not isinstance(result, dict) or result.get("isError") is True:
        return failed
    blocks = result.get("content", [])
    if (
        not isinstance(blocks, list)
        or len(blocks) > 20
        or any(
            not isinstance(block, dict)
            or block.get("type") != "text"
            or not isinstance(block.get("text"), str)
            for block in blocks
        )
    ):
        return {**failed, "error_code": "mcp_content_unsupported"}
    raw = "\n".join(block["text"] for block in blocks)
    structured = result.get("structuredContent")
    if structured is not None:
        try:
            raw = json.dumps(structured, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            return {**failed, "error_code": "mcp_result_invalid"}
    return {
        "status": "succeeded",
        "error_code": None,
        "source_type": "tool",
        "data": {"text": raw[:4000]},
        "truncated": len(raw) > 4000,
    }
