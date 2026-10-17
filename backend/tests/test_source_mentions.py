"""Public-synthetic body-reference regressions, without providers or database access."""

import json

import pytest

from app.rag.answer_adapter import AnswerError, checked_answer

SOURCES = {"E1": "当前有效规范。QX-11 每60天检查一次，更换前停机断电。",
           "E2": "旧规范已归档。QX-11 每90天检查一次。",
           "E3": "设备E99与E1:S2均为公开合成业务编号。S2行是生产线编号。"}


def output(text, identifiers=("E1",), sources=None):
    sources = sources or SOURCES
    return json.dumps({"status": "answered", "text": text, "evidence_ids": list(identifiers),
                       "support": [{"evidence_id": key, "quote": sources[key]}
                                   for key in identifiers]}, ensure_ascii=False)


@pytest.mark.parametrize("text", [
    # Public-synthetic provider #36/T03 shape: a span was promoted to a source line.
    "根据当前有效规范 QL-SPEC-V2，KX-17 单次连续运行最长为8小时。"
    "该要求在文件 E1 的 S2 行中明确列出。现场记录 E3 中提到的47℃不能作为依据。",
    # Public-synthetic provider #56/T06 shape: body references are not declared IDs.
    "| 参数 | V1 | V2 | 出处 |\n| --- | --- | --- | --- |\n"
    "| 温度 | 40℃ | 42℃ | E2:S2, E1:S2 |\n"
    "现场记录（E3）中提及的47℃等信息未经核准。",
    "已确认当前要求。现场记录（E3）另有不同说法。",
    "来源 E3 给出了不同条件。",
    "参考文档 E3 给出的条件。",
    "参考来源[E0]。",
    "请看[E3]。",
    "请看[[ref:E3]]。",
    "出处为E1:S2。",
    "出处为E1:S2:0123456789abcdef。",
    "该事实在 S2 行中列出。",
    "该事实在第S2页。",
    "该事实在S2行。",
    "| 对象 | 来源 |\n| --- | --- |\n| QX-11 | E3 |",
    "See source E3 for the conditions.",
    "See E3 evidence.",
])
def test_unbound_body_reference_or_internal_span_never_passes_source_gate(text):
    with pytest.raises(AnswerError, match="^answer_reference_invalid$"):
        checked_answer(output(text, ("E1", "E2")), SOURCES)


@pytest.mark.parametrize("text", [
    "根据E1，QX-11每60天检查一次。",
    "根据[E1]，QX-11每60天检查一次。",
    "根据[[ref:E1]]，QX-11每60天检查一次。",
    "| 对象 | 出处 |\n| --- | --- |\n| QX-11 | E1 |",
    "E99型号的结构需要另行核实。",  # A business identifier is not automatically a citation.
    "设备E99还需要单独核实。",
    "AE3与E3-Pro是业务型号。",
    "S2行驶平稳，S2页面需要设计。",  # Chinese words must not become line/page locators.
    "E3设备的功耗没有在当前资料中确认。",
    "| 设备编号 | 数量 |\n| --- | --- |\n| E99 | 2 |",
    "E3.8版本尚需确认。",
    "文档E3.8版本仍需另行核实。",
    "能量值写为2E3，工程代号是TypeE3。",
])
def test_declared_references_and_ambiguous_business_numbers_remain_compatible(text):
    result = checked_answer(output(text), SOURCES)
    assert result == ("answered", text, ["E1"])


def test_multiple_declared_bracketed_sources_are_accepted_without_rewriting():
    text = "[E1, E2]属于本轮已声明来源。"
    assert checked_answer(output(text, ("E1", "E2")), SOURCES) == (
        "answered", text, ["E1", "E2"],
    )


@pytest.mark.parametrize("text", ["设备E99需要校验。", "E1:S2是业务编号。", "S2行是生产线编号。"])
def test_business_markers_may_be_repeated_only_from_the_selected_original(text):
    selected = {"E1": SOURCES["E3"]}
    assert checked_answer(output(text, sources=selected), selected)[1] == text


@pytest.mark.parametrize("text", ["E1:S2是业务编号。", "S2行是生产线编号。"])
def test_unselected_original_cannot_exempt_an_internal_looking_marker(text):
    with pytest.raises(AnswerError, match="^answer_reference_invalid$"):
        checked_answer(output(text), SOURCES)


def test_explicit_reference_namespace_cannot_borrow_a_business_number():
    sources = {"E1": "设备E3的业务标签是[E3]，文本标识为[[ref:E3]]。"}
    with pytest.raises(AnswerError, match="^answer_reference_invalid$"):
        checked_answer(output("资料来源[[ref:E3]]。", sources=sources), sources)


def test_selected_business_identifier_is_not_reinterpreted_by_a_document_noun():
    sources = {"E1": "设备E99需要定期维护。"}
    text = "请整理资料 E99 的维护事项。"
    assert checked_answer(output(text, sources=sources), sources)[1] == text
    with pytest.raises(AnswerError, match="^answer_reference_invalid$"):
        checked_answer(output("资料来源E99给出了维护事项。", sources=sources), sources)


@pytest.mark.parametrize("text", ["[[ref:unknown]]", "[[ref:E3", "[[ref:E3]]", "[E1,E3]"])
def test_unknown_or_malformed_explicit_reference_cannot_fall_back_to_plain_text(text):
    with pytest.raises(AnswerError, match="^answer_reference_invalid$"):
        checked_answer(output(text), SOURCES)


def test_source_column_is_checked_without_reinterpreting_business_columns():
    from app.rag.source_mentions import checked_source_mentions

    checked_source_mentions("| 设备编号 | 来源 |\n| --- | --- |\n| E99 | E1 |", ["E1"], SOURCES)
    with pytest.raises(ValueError):
        checked_source_mentions("| 设备编号 | 来源 |\n| --- | --- |\n| E99 | E3 |", ["E1"], SOURCES)


@pytest.mark.parametrize("code", ["`A|B`", "``A|`B``", r"A\|B"])
def test_code_or_escaped_pipes_cannot_hide_an_undeclared_table_source(code):
    text = f"| 参数 | 示例 | 来源 |\n| --- | --- | --- |\n| 温度 | {code} | E3 |"
    with pytest.raises(AnswerError, match="^answer_reference_invalid$"):
        checked_answer(output(text), SOURCES)


def test_business_numbers_inside_code_cells_stay_in_the_business_column():
    text = "| 示例 | 来源 |\n| --- | --- |\n| `E99|E98` | E1 |"
    assert checked_answer(output(text), SOURCES)[1] == text


@pytest.mark.parametrize("legacy_quote", [False, True])
@pytest.mark.parametrize("shape", ["sentence", "operation", "table"])
def test_real_selected_bracketed_span_becomes_its_declared_source(legacy_quote, shape):
    from app.rag.evidence_spans import model_evidence

    sources = {"E1": "公开合成规范。\n\nQX-11每60天检查一次。\n\nQX-110每30天检查一次。"}
    span = model_evidence(sources)[0]["spans"][1]
    text = "QX-11每60天检查一次。[" + span["span_id"] + "]"
    expected = "QX-11每60天检查一次。[E1]"
    if shape == "operation":
        text = "以下条件可连续运行[" + span["span_id"] + "]。"
        expected = "以下条件可连续运行[E1]。"
    elif shape == "table":
        text = "| 周期 | 来源 |\n| --- | --- |\n| 60天 | [" + span["span_id"] + "] |"
        expected = "| 周期 | 来源 |\n| --- | --- |\n| 60天 | [E1] |"
    support = ({"evidence_id": "E1", "quote": span["text"]} if legacy_quote else
               {"evidence_id": "E1", "span_id": span["span_id"]})
    raw = json.dumps({"status": "answered", "text": text, "evidence_ids": ["E1"],
                      "support": [support]})
    assert checked_answer(raw, sources) == ("answered", expected, ["E1"])
    assert json.loads(raw)["text"] == text


@pytest.mark.parametrize("kind", ["unselected-span", "other-source", "old-hash", "short-id",
                                  "partial-quote", "line", "page", "naked"])
def test_span_reference_canonicalization_never_repairs_unproven_locations(kind):
    from app.rag.evidence_spans import model_evidence

    sources = {"E1": "公开合成规范。\n\nQX-11每60天检查一次。\n\nQX-110每30天检查一次。",
               "E2": "另一个来源。"}
    catalog = model_evidence(sources)
    span = catalog[0]["spans"][1]
    marker = span["span_id"]
    support = {"evidence_id": "E1", "quote": span["text"]}
    if kind == "unselected-span":
        marker = catalog[0]["spans"][2]["span_id"]
    elif kind == "other-source":
        marker = catalog[1]["spans"][0]["span_id"]
    elif kind == "old-hash":
        marker = marker.rsplit(":", 1)[0] + ":0000000000000000"
    elif kind == "short-id":
        marker = marker.rsplit(":", 1)[0]
    elif kind == "partial-quote":
        support["quote"] = "每60天检查一次。"
    text = "请核对[" + marker + "]。"
    if kind == "line":
        text = "请核对第[" + marker + "]行。"
    elif kind == "page":
        text = "请核对[" + marker + "]页。"
    elif kind == "naked":
        text = "请核对" + marker + "。"
    raw = json.dumps({"status": "answered", "text": text, "evidence_ids": ["E1"],
                      "support": [support]})
    with pytest.raises(AnswerError, match="^answer_reference_invalid$"):
        checked_answer(raw, sources)
