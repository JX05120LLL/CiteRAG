"""Public synthetic contracts: no models, credentials or business database."""

import json
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


@pytest.mark.parametrize("answer", [
    {"text": "公开合成普通回答"},
    {"status": "answered", "text": "上限为42℃。", "evidence_ids": ["E1"],
     "support": [{"evidence_id": "E1", "quote": "上限为42℃。"}]},
])
def test_finish_accepts_one_encoded_answer_without_changing_action_or_input(answer):
    from app.agent.graph import checked_decision

    # The real-provider failure returned a valid outer JSON object with a
    # JSON-encoded answer string. Decode this one field, never tool arguments.
    value = {"action": "finish", "answer": json.dumps(answer, ensure_ascii=False)}
    original = dict(value)
    assert checked_decision(value) == {"action": "finish", "answer": answer}
    assert value == original
    valid = {"action": "finish", "answer": answer}
    assert checked_decision(valid) is valid


@pytest.mark.parametrize("answer", [
    "plain text", "```json\n{}\n```", "[]", "null", "12", "true",
    json.dumps(json.dumps({"text": "Do not decode recursively"})),
    '{"text": NaN}', '{"text": Infinity}',
    '{"text":"' + "x" * 16_001 + '"}',
    "[" * 1100 + "]" * 1100,
])
def test_finish_rejects_invalid_encoded_answer_with_safe_error(answer):
    from app.agent.errors import AgentError
    from app.agent.graph import checked_decision

    with pytest.raises(AgentError, match="^agent_decision_invalid$"):
        checked_decision({"action": "finish", "answer": answer})


@pytest.mark.parametrize("decision", [
    {"action": "finish", "answer": '{"text":"ok"}', "tool_id": "local.time"},
    {"action": "call_tool", "tool_id": "local.time", "arguments": "{}"},
    {"action": "call_tool", "tool_id": "local.time", "arguments": {},
     "answer": '{"text":"ok"}'},
    {"action": "execute", "answer": '{"text":"ok"}'},
    {"action": "request_input", "prompt": "参数？", "fields": '{"name":"string"}'},
])
def test_answer_compatibility_never_repairs_tool_actions_or_arguments(decision):
    from app.agent.errors import AgentError
    from app.agent.graph import checked_decision

    with pytest.raises(AgentError, match="^agent_decision_invalid$"):
        checked_decision(decision)


@pytest.mark.parametrize("failure", [None, "unknown_source", "fabricated_quote", "unsupported"])
async def test_decoded_answer_still_passes_original_source_and_fact_gates(failure):
    from app.agent.graph import checked_decision
    from app.rag.answer_adapter import AnswerError
    from app.services.answers import AnswerService

    answer = {"status": "answered", "text": "上限为42℃。", "evidence_ids": ["E1"],
              "support": [{"evidence_id": "E1", "quote": "上限为42℃。"}]}
    if failure == "unknown_source":
        answer["evidence_ids"] = ["E99"]
        answer["support"][0]["evidence_id"] = "E99"
    if failure == "fabricated_quote":
        answer["support"][0]["quote"] = "上限为99℃。"
    evidence = [{"id": "E1", "text": "上限为42℃。"}]
    citations = [{"evidence_id": "E1", "filename": "公开合成.txt", "excerpt": "上限为42℃。"}]
    verified = []

    class Answerer:
        async def verify_answer(self, text, sources):
            verified.append((text, sources))
            return json.dumps({"supported": failure != "unsupported"})

    decision = checked_decision({"action": "finish", "answer": json.dumps(answer)})
    raw = json.dumps(decision["answer"])
    if failure:
        with pytest.raises(AnswerError):
            await AnswerService._checked_result(Answerer(), raw, evidence, citations)
        assert bool(verified) == (failure == "unsupported")
    else:
        assert await AnswerService._checked_result(Answerer(), raw, evidence, citations) == (
            "answered", "上限为42℃。", citations,
        )
        assert verified == [("上限为42℃。", evidence)]
