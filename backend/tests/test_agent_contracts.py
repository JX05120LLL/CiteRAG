"""Public synthetic contracts: no models, credentials or business database."""

import math

import pytest

from app.services.token_budget import estimate_json_tokens
from app.tools import gateway
from app.tools.contracts import bounded_model_results


def test_model_tool_budget_keeps_failures_and_identities_without_raw_large_data():
    raw = [
        {
            "status": "failed",
            "tool_id": f"tool.{x}",
            "error_code": "safe_error",
            "data": {"text": "合成" * 4000},
            "source_type": "tool",
        }
        for x in range(4)
    ]
    trimmed = bounded_model_results(raw)
    assert estimate_json_tokens(trimmed) <= 3000
    assert all(item["status"] == "failed" and item["truncated"] for item in trimmed)
    assert raw[0]["data"] is not None  # Audit payload is not overwritten by context compression.


def test_catalog_exposes_reviewed_schema_and_version():
    spec = gateway.built_in_tools()["local.time"]
    assert getattr(spec, "version", None) == "1"
    assert getattr(spec, "input_schema", None) == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert getattr(spec, "effect", None) == "read_only"


def test_arguments_fingerprint_is_canonical_and_rejects_nonfinite():
    fingerprint = getattr(gateway, "arguments_fingerprint", None)
    assert fingerprint is not None, "Gateway must bind approvals to canonical arguments"
    assert fingerprint({"b": 2, "a": 1}) == fingerprint({"a": 1, "b": 2})
    assert fingerprint({"a": 1}) != fingerprint({"a": "1"})
    with pytest.raises(ValueError):
        fingerprint({"value": math.nan})


def test_mcp_protocol_success_is_not_tool_success():
    normalize = getattr(gateway, "normalize_mcp_result", None)
    assert normalize is not None, "MCP errors must be normalized by the gateway"
    result = normalize(
        {
            "isError": True,
            "content": [
                {
                    "type": "text",
                    "text": "secret private upstream diagnostic",
                }
            ],
        }
    )
    assert result["status"] == "failed"
    assert result["error_code"] == "mcp_tool_failed"
    assert "secret" not in str(result)


def test_mcp_content_is_bounded_untrusted_data_and_never_fetched():
    normalize = getattr(gateway, "normalize_mcp_result", None)
    assert normalize is not None, "MCP content needs a bounded data boundary"
    result = normalize({"content": [{"type": "text", "text": "x" * 15000}]})
    assert result["status"] == "succeeded"
    assert result["truncated"] is True
    assert len(result["data"]["text"]) <= 4000
    assert result["source_type"] == "tool"
    assert (
        normalize({"content": [{"type": "resource_link", "uri": "http://127.0.0.1/private"}]})[
            "status"
        ]
        == "failed"
    )


def test_registry_rejects_write_without_approval_and_invalid_limits():
    for kwargs in ({"effect": "write"}, {"timeout_seconds": 0}, {"version": ""}):
        with pytest.raises(ValueError):
            gateway.ToolDefinition(
                "test.tool", "合成", "any", False, "合成", lambda a: a, None, **kwargs
            )
