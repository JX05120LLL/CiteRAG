"""Offline checks for model-selected query routing; no provider or business DB."""

import pytest

from app.rag.answer_adapter import AnswerError, checked_route
from app.rag.runtime import RagRuntime
from app.services.answers import matching_candidates


def test_only_literal_confirmed_attributes_become_candidates():
    attributes = [
        ("doc_code", "A001"), ("doc_code", "A0010"),
        ("model_code", "X100"), ("model_code", "X100 Pro"),
    ]
    assert matching_candidates("查 X100 Pro 的 A0010", attributes) == [
        {"id": "C1", "field": "doc_code", "value": "A0010"},
        {"id": "C2", "field": "model_code", "value": "X100 Pro"},
    ]
    assert matching_candidates("查 A0010", [("doc_code", "A001")]) == []
    assert matching_candidates("查 X100 Pro", [("model_code", "X100")]) == []
    assert matching_candidates("What is the X100 limit?", [("model_code", "X100")]) == [
        {"id": "C1", "field": "model_code", "value": "X100"},
    ]


def test_model_route_accepts_only_known_candidate_ids_and_fields():
    candidates = [
        {"id": "C1", "field": "doc_code", "value": "A001"},
        {"id": "C2", "field": "model_code", "value": "X100"},
    ]
    question = "查 ORD-001 中的 A001 和 X100"
    assert checked_route('{"mode":"semantic"}', candidates, question) == {"mode": "semantic"}
    assert checked_route('{"mode":"exact","candidate_ids":["C1","C2"]}', candidates, question) == {
        "mode": "exact", "filters": {"doc_code": "A001", "model_code": "X100"},
    }
    assert checked_route('{"mode":"unsupported"}', [], question) == {"mode": "unsupported"}
    assert checked_route('{"mode":"literal","phrase":"ORD-001"}', [], question) == {
        "mode": "literal", "phrase": "ORD-001",
    }
    for raw in [
        '{"mode":"exact","candidate_ids":["C9"]}',
        '{"mode":"exact","candidate_ids":[]}',
        '{"mode":"exact","candidate_ids":["C1","C1"]}',
        '{"mode":"exact","candidate_ids":["C1"],"extra":"text"}',
        '{"mode":"semantic","candidate_ids":["C1"]}',
        '{"mode":"exact","field":"order_id","value":"A001"}',
        '{"mode":"literal","phrase":"ORD-002"}',
        '{"mode":"literal","phrase":"ORD-001","candidate_ids":["C1"]}',
    ]:
        with pytest.raises(AnswerError):
            checked_route(raw, candidates, question)


@pytest.mark.asyncio
async def test_runtime_routes_with_answer_model_and_bounded_output():
    class FakeClient:
        async def complete(self, model, messages, *, max_tokens):
            assert model == "qwen-flash"
            assert max_tokens == 128
            assert messages[1].content == (
                '{"question": "查 X100 的上限", "confirmed_candidates": '
                '[{"id": "C1", "field": "model_code", "value": "X100"}]}'
            )
            return type("Completion", (), {"content": '{"mode":"exact","candidate_ids":["C1"]}'})()

    runtime = object.__new__(RagRuntime)
    runtime._client = FakeClient()
    assert await runtime.route_question("查 X100 的上限", [
        {"id": "C1", "field": "model_code", "value": "X100"},
    ]) == '{"mode":"exact","candidate_ids":["C1"]}'
