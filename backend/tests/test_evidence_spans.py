"""Deterministic source selection and conflict reporting; no database or providers."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.rag.answer_adapter import AnswerError, checked_answer
from app.rag.evidence_spans import EvidenceSpanError, expand_span_support, model_evidence
from app.services.answers import AnswerService

EVIDENCE = [
    {"id": "E1", "text": "公开合成规范。\n\nAX-11 每 60 天检查一次。\n\nAX-110 每 30 天检查一次。"},
    {"id": "E2", "text": "公开合成记录未经核准，不能修改正式规范。"},
]


def span_output(evidence=EVIDENCE):
    chosen = model_evidence(evidence)[0]["spans"][1]
    return {"status": "answered", "text": "AX-11 每60天检查一次。",
            "evidence_ids": ["E1"], "support": [
                {"evidence_id": "E1", "span_id": chosen["span_id"]},
            ]}


def test_catalog_is_stable_bounded_and_preserves_contiguous_original_text():
    source = [{"id": "E1", "text": "甲段。\n\n" + "连续原文" * 700 + "\n\n尾段。"}]
    catalog = model_evidence(source)
    assert catalog == model_evidence(source)
    assert set(catalog[0]) == {"id", "spans"}
    assert "".join(item["text"] for item in catalog[0]["spans"]) == source[0]["text"]
    assert all(0 < len(item["text"]) <= 600 for item in catalog[0]["spans"])
    assert len({item["span_id"] for item in catalog[0]["spans"]}) == len(catalog[0]["spans"])
    assert all(item["text"] in source[0]["text"] for item in catalog[0]["spans"])
    assert source[0]["text"].endswith("尾段。")  # Caller input is never changed.


def test_many_tiny_paragraphs_do_not_create_unbounded_catalog():
    source = [{"id": "E1", "text": "句。\n\n" * 1000}]
    spans = model_evidence(source)[0]["spans"]
    assert len(spans) <= 32
    assert "".join(item["text"] for item in spans) == source[0]["text"]


@pytest.mark.parametrize("evidence", [
    [{"id": "E1", "text": "x" * 6001}],
    [{"id": f"E{i}", "text": "x"} for i in range(4)],
    [{"id": "E1", "text": "same"}, {"id": "E1", "text": "other"}],
    [{"id": "E1", "text": " "}],
])
def test_invalid_or_oversized_catalog_is_rejected_without_truncating(evidence):
    with pytest.raises(EvidenceSpanError):
        model_evidence(evidence)


def test_span_support_expands_to_canonical_quote_and_legacy_shape():
    raw = json.dumps(span_output())
    normalized = json.loads(expand_span_support(raw, EVIDENCE))
    quote = model_evidence(EVIDENCE)[0]["spans"][1]["text"]
    assert normalized["support"] == [{"evidence_id": "E1", "quote": quote}]
    assert checked_answer(raw, {item["id"]: item["text"] for item in EVIDENCE}) == (
        "answered", "AX-11 每60天检查一次。", ["E1"],
    )
    assert expand_span_support(json.dumps(normalized), EVIDENCE) == json.dumps(normalized)


async def test_span_selection_keeps_citation_identity_and_original_verifier_input():
    data = span_output()
    citations = [{"evidence_id": item["id"], "filename": f"合成{item['id']}.txt",
                  "excerpt": item["text"]} for item in EVIDENCE]
    model = SimpleNamespace(verify_answer=AsyncMock(return_value='{"supported":true}'))
    result = await AnswerService._checked_result(model, json.dumps(data), EVIDENCE, citations)
    assert result == ("answered", data["text"], [citations[0]])
    # The verifier receives the full verified original, rather than only a selected
    # sentence that might omit a limiting condition elsewhere in the same source.
    model.verify_answer.assert_awaited_once_with(data["text"], [EVIDENCE[0]])


async def test_too_many_selected_spans_never_enter_fact_verification():
    data = span_output()
    data["support"] *= 7
    model = SimpleNamespace(verify_answer=AsyncMock(return_value='{"supported":true}'))
    with pytest.raises(AnswerError, match="^answer_reference_invalid$"):
        await AnswerService._checked_result(model, json.dumps(data), EVIDENCE, [])
    model.verify_answer.assert_not_awaited()


@pytest.mark.parametrize("text", [
    "AX-11 每60天检查一次。来源E3另有不同说法。",
    "AX-11 每60天检查一次，出处为E1:S2。",
    "AX-11 每60天检查一次，详见S2行。",
])
async def test_valid_span_does_not_authorize_unbound_sources_in_the_body(text):
    data = span_output()
    data["text"] = text
    model = SimpleNamespace(verify_answer=AsyncMock(return_value='{"supported":true}'))
    with pytest.raises(AnswerError, match="^answer_reference_invalid$"):
        await AnswerService._checked_result(model, json.dumps(data), EVIDENCE, [])
    model.verify_answer.assert_not_awaited()


@pytest.mark.parametrize("corruption", [
    "unknown", "cross-evidence", "old-hash", "mixed-fields", "mixed-list", "non-string",
])
async def test_span_substitution_never_reaches_fact_verifier(corruption):
    data = span_output()
    if corruption == "unknown":
        data["support"][0]["span_id"] = "E1:S999:unknown"
    elif corruption == "cross-evidence":
        data["support"][0]["span_id"] = model_evidence(EVIDENCE)[1]["spans"][0]["span_id"]
    elif corruption == "old-hash":
        changed = [{**EVIDENCE[0], "text": EVIDENCE[0]["text"].replace("60", "90")}]
        data["support"][0]["span_id"] = model_evidence(changed)[0]["spans"][1]["span_id"]
    elif corruption == "mixed-fields":
        data["support"][0]["quote"] = "AX-11 每 60 天检查一次。"
    elif corruption == "mixed-list":
        data["support"].append({"evidence_id": "E1", "quote": "AX-11 每 60 天检查一次。"})
    else:
        data["support"][0]["span_id"] = 1
    model = SimpleNamespace(verify_answer=AsyncMock(return_value='{"supported":true}'))
    with pytest.raises(AnswerError, match="^answer_reference_invalid$"):
        await AnswerService._checked_result(model, json.dumps(data), EVIDENCE, [])
    model.verify_answer.assert_not_awaited()


async def test_span_identity_does_not_prove_an_altered_claim():
    data = span_output()
    data["text"] = "AX-11 可以每600天检查一次。"
    model = SimpleNamespace(verify_answer=AsyncMock(return_value='{"supported":false}'))
    with pytest.raises(AnswerError, match="^answer_unsupported_claims$"):
        await AnswerService._checked_result(model, json.dumps(data), EVIDENCE, [])
    model.verify_answer.assert_awaited_once_with(data["text"], [EVIDENCE[0]])


async def test_contextual_verifier_sees_unselected_conflict_without_publishing_its_source():
    data = span_output()
    contextual = AsyncMock(return_value='{"supported":true}')
    legacy = AsyncMock(side_effect=AssertionError("contextual verifier must take precedence"))
    model = SimpleNamespace(verify_answer_in_context=contextual, verify_answer=legacy)
    citations = [{"evidence_id": item["id"]} for item in EVIDENCE]
    result = await AnswerService._checked_result(model, json.dumps(data), EVIDENCE, citations)
    contextual.assert_awaited_once_with(data["text"], [EVIDENCE[0]], EVIDENCE)
    legacy.assert_not_awaited()
    assert result[2] == [citations[0]]


@pytest.mark.parametrize("kind", ["conflicting-current-source", "only-other-source-supports"])
async def test_contextual_rejection_does_not_retry_selected_only_verification(kind):
    if kind == "conflicting-current-source":
        evidence = [{"id": "E1", "text": "当前有效规则甲：上限42℃。"},
                    {"id": "E2", "text": "当前有效规则乙：上限47℃。"}]
        text = "当前上限就是42℃。"
    else:
        evidence = [{"id": "E1", "text": "设备编号AX-11。"},
                    {"id": "E2", "text": "设备AX-11的上限58℃。"}]
        text = "设备AX-11上限58℃。"
    data = {"status": "answered", "text": text, "evidence_ids": ["E1"], "support": [
        {"evidence_id": "E1", "span_id": model_evidence(evidence)[0]["spans"][0]["span_id"]}]}
    contextual = AsyncMock(return_value='{"supported":false}')
    legacy = AsyncMock(return_value='{"supported":true}')
    with pytest.raises(AnswerError, match="^answer_unsupported_claims$"):
        await AnswerService._checked_result(SimpleNamespace(
            verify_answer_in_context=contextual, verify_answer=legacy),
            json.dumps(data), evidence, [])
    contextual.assert_awaited_once_with(text, [evidence[0]], evidence)
    legacy.assert_not_awaited()


async def test_runtime_keeps_unselected_counterevidence_separate_from_body_support(monkeypatch):
    from app.rag.answer_adapter import LightRAGAnswerAdapter
    from app.rag.runtime import RagRuntime

    captured = []

    class Client:
        async def complete(self, _model, messages, **_options):
            captured.append(messages)
            payload = json.loads(messages[1].content)
            return SimpleNamespace(content=json.dumps({"units": [
                {"unit_id": unit["id"], "verdict": "unsupported", "support": []}
                for unit in payload["answer_units"]]}))

    async def client(_self):
        return Client()

    monkeypatch.setattr(RagRuntime, "_get_client", client)
    runtime = object.__new__(RagRuntime)
    selected = [{"id": "E1", "text": "规则甲上限42℃。"}]
    other = {"id": "E2", "text": "当前有效规则乙上限47℃。"}
    result = await LightRAGAnswerAdapter(runtime).verify_answer_in_context(
        "上限42℃。", selected, selected + [other])
    assert json.loads(result) == {"supported": False}
    assert len(captured) == 1
    payload = json.loads(captured[0][-1].content)
    assert "".join(unit["text"] for unit in payload["answer_units"]) == "上限42℃。"
    assert payload["evidence"] == model_evidence(selected)
    assert payload["other_evidence"] == model_evidence([other])
    assert "other_evidence" in captured[0][0].content
    assert "不能" in captured[0][0].content


async def test_agent_delegates_contextual_verification_and_old_runtime_remains_compatible():
    from app.agent.planner import AgentAnswerer
    from app.rag.answer_adapter import LightRAGAnswerAdapter

    evidence = [{"id": "E1", "text": "公开合成原文。"}]
    contextual = AsyncMock(return_value='{"supported":true}')
    base = LightRAGAnswerAdapter(SimpleNamespace(verify_answer_in_context=contextual))
    agent = AgentAnswerer(None, "synthetic", 1, base)
    assert await agent.verify_answer_in_context("公开合成原文。", evidence, evidence) == (
        '{"supported":true}')
    contextual.assert_awaited_once_with("公开合成原文。", evidence, evidence)
    legacy = AsyncMock(return_value='{"supported":true}')
    old = LightRAGAnswerAdapter(SimpleNamespace(verify_answer=legacy))
    assert await old.verify_answer_in_context("公开合成原文。", evidence, evidence) == (
        '{"supported":true}')
    legacy.assert_awaited_once_with("公开合成原文。", evidence)


async def test_original_ellipsis_quote_is_never_repaired_or_fuzzily_matched():
    data = span_output()
    data["support"] = [{"evidence_id": "E1", "quote":
                        "AX-11...每 60 天检查一次。\n\nAX-110...每 30 天检查一次。"}]
    raw = json.dumps(data)
    assert expand_span_support(raw, EVIDENCE) == raw
    model = SimpleNamespace(verify_answer=AsyncMock(return_value='{"supported":true}'))
    with pytest.raises(AnswerError, match="^answer_source_mismatch$"):
        await AnswerService._checked_result(model, raw, EVIDENCE, [])
    model.verify_answer.assert_not_awaited()


def conflict_replay():
    """Original public-synthetic provider #37 shape, previously rejected before verification."""
    note = ("现场口述记录写道：“KX-17 连续运行温度可以达到 47 摄氏度。”"
            "该数值与当前 QL-SPEC-V2 的 42 摄氏度不同。现有资料没有解释差异，"
            "也没有授权以 47 摄氏度替代正式上限。")
    rule = ("KX-17 的当前要求：仅限室内使用；连续运行温度上限为 42 摄氏度；"
            "环境相对湿度不得超过 75%；单次连续运行最长 8 小时；滤芯每 60 天检查一次。"
            "更换滤芯前必须停机并断电。")
    evidence = [{"id": "E1", "text": "状态：未经核准，不能视为对正式规范的修订。\n" + note},
                {"id": "E2", "text": "QL-SPEC-V2。状态：当前有效。\n" + rule}]
    data = {"status": "conflicting_evidence", "text":
            "存在冲突：E1 中的现场记录称 KX-17 可连续运行至 47℃，但 E2 明确规定其连续运行"
            "温度上限为 42℃。E1 所述记录未经核准，状态为‘未经核准’，不能视为对正式规范的修订；"
            "而 E2 为当前有效版本（QL-SPEC-V2），具有正式效力。因此，不能依据 E1 的非正式记录"
            "将正式上限修改为 47℃。", "evidence_ids": ["E1", "E2"], "support": [
                {"evidence_id": "E1", "quote": note}, {"evidence_id": "E2", "quote": rule},
            ]}
    return data, evidence


async def test_real_synthetic_conflict_analysis_reaches_verifier_and_keeps_sources():
    data, evidence = conflict_replay()
    citations = [{"evidence_id": item["id"], "excerpt": item["text"]} for item in evidence]
    model = SimpleNamespace(verify_answer=AsyncMock(return_value='{"supported":true}'))
    result = await AnswerService._checked_result(model, json.dumps(data), evidence, citations)
    assert result == ("conflicting_evidence", data["text"], citations)
    model.verify_answer.assert_awaited_once_with(data["text"], evidence)


@pytest.mark.parametrize("corruption", ["missing-ids", "missing-support", "wrong-quote",
                                         "made-up-authorization"])
async def test_conflict_reporting_cannot_bypass_source_or_factual_gates(corruption):
    data, evidence = conflict_replay()
    if corruption == "missing-ids":
        data["evidence_ids"] = []
    elif corruption == "missing-support":
        del data["support"]
    elif corruption == "wrong-quote":
        data["support"][0]["quote"] = "该记录已经核准。"
    else:
        data["text"] += "因此授权将正式上限改为47℃。"
    model = SimpleNamespace(verify_answer=AsyncMock(return_value='{"supported":false}'))
    with pytest.raises(AnswerError):
        await AnswerService._checked_result(model, json.dumps(data), evidence, [])
    if corruption == "made-up-authorization":
        model.verify_answer.assert_awaited_once()
    else:
        model.verify_answer.assert_not_awaited()


@pytest.mark.parametrize("with_support", [False, True])
async def test_old_empty_conflict_history_still_does_not_invoke_verifier(with_support):
    data = {"status": "conflicting_evidence", "text": "", "evidence_ids": []}
    if with_support:
        data["support"] = []
    model = SimpleNamespace(verify_answer=AsyncMock())
    assert await AnswerService._checked_result(model, json.dumps(data), [], []) == (
        "conflicting_evidence", "", [],
    )
    model.verify_answer.assert_not_awaited()


@pytest.mark.parametrize("supported", [False, True])
async def test_real_body_span_uses_one_normalized_text_for_verification_and_storage(supported):
    data = span_output()
    data["text"] += "[" + data["support"][0]["span_id"] + "]"
    expected = "AX-11 每60天检查一次。[E1]"
    verifier = AsyncMock(return_value=json.dumps({"supported": supported}))
    model = SimpleNamespace(verify_answer=verifier)
    citations = [{"evidence_id": "E1", "filename": "公开合成规范.txt"}]
    if supported:
        assert await AnswerService._checked_result(
            model, json.dumps(data), EVIDENCE, citations) == ("answered", expected, citations)
    else:
        with pytest.raises(AnswerError, match="^answer_unsupported_claims$"):
            await AnswerService._checked_result(model, json.dumps(data), EVIDENCE, citations)
    verifier.assert_awaited_once_with(expected, [EVIDENCE[0]])


def test_generation_catalog_uses_local_integer_indices_and_preserves_exact_spans():
    from app.rag.evidence_spans import generation_evidence

    evidence = [{**EVIDENCE[0], "source": {"filename": "公开合成规范.txt"}}, EVIDENCE[1]]
    before = json.dumps(evidence, ensure_ascii=False)
    catalog = generation_evidence(evidence)
    verified = model_evidence(evidence)
    assert catalog == generation_evidence(evidence)
    assert catalog[0]["source"] == evidence[0]["source"]
    assert catalog[1]["spans"][0]["span_index"] == 1
    assert json.dumps(evidence, ensure_ascii=False) == before
    for original, entry, verifier in zip(evidence, catalog, verified, strict=True):
        assert set(entry) == {"id", "spans"} | ({"source"} if "source" in original else set())
        assert "".join(span["text"] for span in entry["spans"]) == original["text"]
        assert entry["spans"] == [{"span_index": index, "text": span["text"]}
                                   for index, span in enumerate(verifier["spans"], 1)]
        assert all(set(span) == {"span_index", "text"} for span in entry["spans"])
    assert "span_id" not in json.dumps(catalog)
    assert generation_evidence({item["id"]: item["text"] for item in evidence}) == [
        {key: value for key, value in item.items() if key != "source"} for item in catalog]


def test_selected_integer_index_expands_to_existing_canonical_quote_contract():
    data = span_output()
    data["support"] = [{"evidence_id": "E1", "span_index": 2}]
    raw = json.dumps(data)
    expected = {**data, "support": [{"evidence_id": "E1", "quote":
                                    model_evidence(EVIDENCE)[0]["spans"][1]["text"]}]}
    assert json.loads(expand_span_support(raw, EVIDENCE)) == expected
    assert checked_answer(raw, {item["id"]: item["text"] for item in EVIDENCE}) == (
        "answered", data["text"], ["E1"])
    assert json.loads(raw) == data


async def test_index_selection_still_requires_factual_verification_before_submission():
    data = span_output()
    data["support"] = [{"evidence_id": "E1", "span_index": 2}]
    data["text"] = "AX-11可以每600天检查一次。"
    verifier = AsyncMock(return_value='{"supported":false}')
    with pytest.raises(AnswerError, match="^answer_unsupported_claims$"):
        await AnswerService._checked_result(SimpleNamespace(verify_answer=verifier),
                                           json.dumps(data), EVIDENCE, [])
    verifier.assert_awaited_once_with(data["text"], [EVIDENCE[0]])


@pytest.mark.parametrize("index", [True, False, 0, -1, 999, "2", 2.0, None])
async def test_invalid_integer_span_indices_never_enter_the_fact_verifier(index):
    data = span_output()
    data["support"] = [{"evidence_id": "E1", "span_index": index}]
    verifier = AsyncMock(return_value='{"supported":true}')
    with pytest.raises(AnswerError, match="^answer_reference_invalid$"):
        await AnswerService._checked_result(SimpleNamespace(verify_answer=verifier),
                                           json.dumps(data), EVIDENCE, [])
    verifier.assert_not_awaited()


@pytest.mark.parametrize("kind", ["unknown-source", "mixed-fields", "mixed-quote-fields",
                                  "mixed-hash-list", "reverse-mixed-hash-list",
                                  "mixed-quote-list", "source-specific-bound"])
async def test_index_selection_cannot_mix_formats_or_borrow_another_source(kind):
    data = span_output()
    data["support"] = [{"evidence_id": "E1", "span_index": 2}]
    if kind == "unknown-source":
        data["support"][0]["evidence_id"] = "E99"
    elif kind == "mixed-fields":
        data["support"][0]["span_id"] = model_evidence(EVIDENCE)[0]["spans"][1]["span_id"]
    elif kind == "mixed-quote-fields":
        data["support"][0]["quote"] = "AX-11 每 60 天检查一次。"
    elif kind == "mixed-hash-list":
        data["support"].extend(span_output()["support"])
    elif kind == "reverse-mixed-hash-list":
        data["support"] = span_output()["support"] + data["support"]
    elif kind == "mixed-quote-list":
        data["support"].append({"evidence_id": "E1", "quote": "AX-11 每 60 天检查一次。"})
    else:
        # Source E1 has index2; E2 has only one span. Indices are never global.
        data["evidence_ids"] = ["E2"]
        data["support"][0]["evidence_id"] = "E2"
    verifier = AsyncMock(return_value='{"supported":true}')
    with pytest.raises(AnswerError, match="^answer_reference_invalid$"):
        await AnswerService._checked_result(SimpleNamespace(verify_answer=verifier),
                                           json.dumps(data), EVIDENCE, [])
    verifier.assert_not_awaited()


def test_integer_span_is_resolved_only_against_the_supplied_frozen_source():
    data = span_output()
    data["support"] = [{"evidence_id": "E1", "span_index": 2}]
    raw = json.dumps(data)
    revised = [{**EVIDENCE[0], "text": EVIDENCE[0]["text"].replace("60", "90")}]
    assert "60" in json.loads(expand_span_support(raw, EVIDENCE))["support"][0]["quote"]
    current = json.loads(expand_span_support(raw, revised))["support"][0]["quote"]
    assert "90" in current and "60" not in current
    # It selects bytes from the supplied snapshot, not a remembered earlier answer.
    assert current in revised[0]["text"]


async def test_valid_integer_selection_returns_existing_citations_without_index_fields():
    data = span_output()
    data["support"] = [{"evidence_id": "E1", "span_index": 2}]
    citations = [{"evidence_id": "E1", "filename": "公开合成规范.txt"}]
    verifier = AsyncMock(return_value='{"supported":true}')
    result = await AnswerService._checked_result(SimpleNamespace(verify_answer=verifier),
                                                 json.dumps(data), EVIDENCE, citations)
    assert result == ("answered", data["text"], citations)
    verifier.assert_awaited_once_with(data["text"], [EVIDENCE[0]])
