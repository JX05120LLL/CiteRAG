"""Coverage/source proof contracts; no provider, credentials, or database access."""

import copy
import json
from types import SimpleNamespace

import pytest

from app.rag.answer_adapter import AnswerError, LightRAGAnswerAdapter
from app.rag.evidence_spans import model_evidence

SOURCES = [{"id": "E1", "text": "公开合成规则：温度42℃，状态当前有效。"}]


def proof(text, sources=SOURCES):
    from app.rag.verification_audit import verification_units

    span = model_evidence(sources)[0]["spans"][0]["span_id"]
    return {"units": [{"unit_id": unit["id"], "verdict": "supported", "support": [
        {"evidence_id": sources[0]["id"], "span_id": span},
    ]} for unit in verification_units(text)]}


def test_units_cover_every_original_character_and_bind_the_whole_answer():
    from app.rag.verification_audit import verification_units

    text = ("温度42℃，状态当前有效。\n\n| 参数 | 值 |\n| --- | --- |\n"
            "| 温度 | 42℃ |\n注：仅为合成规则。")
    units = verification_units(text)
    assert "".join(unit["text"] for unit in units) == text
    assert units[0]["start"] == 0 and units[-1]["end"] == len(text)
    assert all(left["end"] == right["start"]
               for left, right in zip(units, units[1:], strict=False))
    assert all(text[unit["start"]:unit["end"]] == unit["text"] for unit in units)
    assert all(len(unit["text"]) > 0 for unit in units)
    changed = verification_units(text + "还有新事实。")
    assert units[0]["id"] != changed[0]["id"]


def test_every_clause_of_conflict_explanation_is_submitted_not_model_selected():
    from app.rag.verification_audit import verification_units

    text = "规范温度42℃，现场记录47℃。现有规范具有法律效力，须提交正式变更申请。"
    units = verification_units(text)
    assert any(unit["text"].strip() == "现有规范具有法律效力，须提交正式变更申请。"
               for unit in units)
    assert any("须提交正式变更申请" in unit["text"] for unit in units)


def test_units_have_a_fixed_bound_without_truncating_the_tail():
    from app.rag.verification_audit import MAX_VERIFICATION_UNITS, verification_units

    text = "很短的句子。" * 100
    units = verification_units(text)
    assert len(units) <= MAX_VERIFICATION_UNITS
    assert "".join(unit["text"] for unit in units) == text


def test_complete_audit_uses_only_real_selected_spans():
    from app.rag.verification_audit import checked_verification_audit

    text = "温度42℃。状态当前有效。"
    assert checked_verification_audit(json.dumps(proof(text)), text, SOURCES) is True


def test_one_top_level_alias_keeps_the_same_strict_unit_and_source_contract():
    from app.rag.verification_audit import checked_verification_audit

    text = "温度42℃。状态当前有效。"
    aliased = {"answer_units": proof(text)["units"]}
    assert checked_verification_audit(json.dumps(aliased), text, SOURCES) is True


@pytest.mark.parametrize("bad", ["both", "missing", "unknown", "duplicate-unit", "unknown-unit",
                                  "unknown-span", "other-source", "bare-boolean", "duplicate-key"])
def test_top_level_alias_never_relaxes_internal_source_or_coverage_checks(bad):
    from app.rag.verification_audit import VerificationAuditError, checked_verification_audit

    text = "温度42℃。状态当前有效。"
    units = proof(text)["units"]
    data = {"answer_units": units}
    if bad == "both":
        data["units"] = units
    elif bad == "missing":
        data = {}
    elif bad == "unknown":
        data = {"unit_list": units}
    elif bad == "duplicate-unit":
        units[1] = units[0]
    elif bad == "unknown-unit":
        units[0]["unit_id"] = "U999:invented"
    elif bad == "unknown-span":
        units[0]["support"][0]["span_id"] = "E1:S999:invented"
    elif bad == "other-source":
        units[0]["support"][0]["evidence_id"] = "E2"
    elif bad == "bare-boolean":
        data = {"supported": True}
    raw = json.dumps(data)
    if bad == "duplicate-key":
        raw = '{"answer_units":' + json.dumps(units) + ',"answer_units":' + json.dumps(units) + '}'
    with pytest.raises(VerificationAuditError):
        checked_verification_audit(raw, text, SOURCES)


@pytest.mark.parametrize("bad", ["boolean", "missing", "duplicate", "unknown-unit", "old-body",
                                  "unknown-span", "other-source", "no-support", "wrong-type",
                                  "extra-field", "extra-top", "invented-verdict"])
def test_audit_rejects_boolean_acceptance_omissions_and_fake_proofs(bad):
    from app.rag.verification_audit import VerificationAuditError, checked_verification_audit

    text = "温度42℃。状态当前有效。"
    data = copy.deepcopy(proof(text))
    if bad == "boolean":
        data = {"supported": True}
    elif bad == "missing":
        data["units"].pop()
    elif bad == "duplicate":
        data["units"][1] = data["units"][0]
    elif bad == "unknown-unit":
        data["units"][0]["unit_id"] = "U999:invented"
    elif bad == "old-body":
        data = proof("温度43℃。状态当前有效。")
    elif bad == "unknown-span":
        data["units"][0]["support"][0]["span_id"] = "E1:S999:invented"
    elif bad == "other-source":
        other = model_evidence([{"id": "E2", "text": "其他检索资料：99℃。"}])[0]
        data["units"][0]["support"] = [{"evidence_id": "E2",
                                        "span_id": other["spans"][0]["span_id"]}]
    elif bad == "no-support":
        data["units"][0]["support"] = []
    elif bad == "wrong-type":
        data["units"][0]["unit_id"] = True
    elif bad == "extra-field":
        data["units"][0]["tool"] = "execute"
    elif bad == "extra-top":
        data["supported"] = True
    else:
        data["units"][0]["verdict"] = "not_factual"
    with pytest.raises(VerificationAuditError):
        checked_verification_audit(json.dumps(data), text, SOURCES)


@pytest.mark.parametrize("verdict", ["unsupported", "uncertain"])
def test_one_unsupported_tail_clause_prevents_whole_answer_acceptance(verdict):
    from app.rag.verification_audit import checked_verification_audit

    text = "温度42℃。该规则有法律效力。"
    data = proof(text)
    data["units"][-1].update(verdict=verdict, support=[])
    assert checked_verification_audit(json.dumps(data), text, SOURCES) is False


async def test_production_runtime_never_accepts_a_naked_supported_true(monkeypatch):
    from app.rag.runtime import RagRuntime

    class Client:
        async def complete(self, _model, _messages, **_options):
            return SimpleNamespace(content='{"supported":true}')

    async def client(_self):
        return Client()

    monkeypatch.setattr(RagRuntime, "_get_client", client)
    runtime = object.__new__(RagRuntime)
    with pytest.raises(AnswerError, match="^answer_verification_unavailable$"):
        await LightRAGAnswerAdapter(runtime).verify_answer_in_context(
            "温度42℃。", SOURCES, SOURCES)


async def test_runtime_sends_all_units_once_with_separate_counterevidence(monkeypatch):
    from app.rag.runtime import RagRuntime

    text = "温度42℃。状态当前有效。"
    captured = []

    class Client:
        async def complete(self, _model, messages, **options):
            captured.append((messages, options))
            return SimpleNamespace(content=json.dumps(proof(text)))

    async def client(_self):
        return Client()

    monkeypatch.setattr(RagRuntime, "_get_client", client)
    other = {"id": "E2", "text": "另有47℃记录，未经核准。"}
    result = await object.__new__(RagRuntime).verify_answer_in_context(
        text, SOURCES, SOURCES + [other])
    assert json.loads(result) == {"supported": True}
    assert len(captured) == 1
    payload = json.loads(captured[0][0][1].content)
    assert "answer" not in payload  # Do not send the same text a second time.
    assert "".join(unit["text"] for unit in payload["answer_units"]) == text
    assert {item["id"] for item in payload["evidence"]} == {"E1"}
    assert {item["id"] for item in payload["other_evidence"]} == {"E2"}
    assert captured[0][1]["max_tokens"] > 128


@pytest.mark.parametrize("linebreak", ["", "\n"])
def test_numeric_commas_and_source_attribution_stay_in_the_same_unit(linebreak):
    from app.rag.verification_audit import verification_units

    text = "根据来源E2，" + linebreak + "预算是1,234.50元，相比1,200.00元增长2.875%。"
    assert [unit["text"] for unit in verification_units(text)] == [text]


@pytest.mark.parametrize("outer_pipes", [False, True])
def test_legal_markdown_table_rows_retain_complete_values(outer_pipes):
    from app.rag.verification_audit import verification_units

    lines = ["参数 | 金额 | 来源", "--- | --- | ---", "预算 | 1,234.50元；同比增长2.875%。 | E1"]
    if outer_pipes:
        lines = ["| " + line + " |" for line in lines]
    text = "\n".join(lines)
    assert [unit["text"] for unit in verification_units(text)] == [
        lines[0] + "\n", lines[1] + "\n", lines[2]]


@pytest.mark.parametrize("text", [
    "根据来源E2，规范温度为42℃。",
    "根据来源E2，\n规范温度为42℃。",
    "根据[E2]，规范温度为42℃。",
    "根据[[ref:E2]]，规范温度为42℃。",
    "| 参数 | 值 | 来源 |\n| --- | --- | --- |\n| 温度 | 42℃ | E2 |",
    "参数 | 示例 | 来源\n--- | --- | ---\n温度 | `A|B`；42℃。 | E2",
])
def test_explicit_unit_source_cannot_be_proved_by_another_declared_source(text):
    from app.rag.verification_audit import VerificationAuditError, checked_verification_audit

    sources = [{"id": "E1", "text": "规范温度42℃。"}, {"id": "E2", "text": "规范温度47℃。"}]
    with pytest.raises(VerificationAuditError):
        checked_verification_audit(json.dumps(proof(text, sources)), text, sources)


def test_plain_business_number_does_not_become_a_required_server_source():
    from app.rag.verification_audit import checked_verification_audit

    text = "设备E99支持温度42℃。"
    sources = [{"id": "E1", "text": text}]
    assert checked_verification_audit(json.dumps(proof(text, sources)), text, sources)


def test_explicit_unit_source_accepts_its_own_proof_without_adding_new_sources():
    from app.rag.verification_audit import checked_verification_audit

    sources = [{"id": "E2", "text": "规范温度47℃。"}, {"id": "E1", "text": "规范温度42℃。"}]
    text = "根据来源E2，规范温度为47℃。"
    assert checked_verification_audit(json.dumps(proof(text, sources)), text, sources)


def test_business_document_number_does_not_bind_an_unrelated_declared_source():
    from app.rag.verification_audit import checked_verification_audit

    sources = [{"id": "E1", "text": "设备E2的维护周期为60天。"},
               {"id": "E2", "text": "另一个已声明的公开合成来源。"}]
    text = "资料E2的维护周期为60天。"
    assert checked_verification_audit(json.dumps(proof(text, sources)), text, sources)


async def test_maximum_unit_and_support_contract_preserves_the_complete_proof(monkeypatch):
    from app.rag.runtime import RagRuntime
    from app.rag.verification_audit import (
        MAX_VERIFICATION_UNITS,
        checked_verification_audit,
        verification_units,
    )

    text = "当前规则允许交叉核对。" * MAX_VERIFICATION_UNITS
    sources = [{"id": f"E{index}", "text": f"公开合成规则{index}：允许交叉核对。"}
               for index in range(1, 4)]
    references = [{"evidence_id": item["id"], "span_id": item["spans"][0]["span_id"]}
                  for item in model_evidence(sources)]
    units = verification_units(text)
    data = {"units": [{"unit_id": unit["id"], "verdict": "supported", "support": references}
                      for unit in units]}
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    assert len(units) == MAX_VERIFICATION_UNITS
    assert checked_verification_audit(raw, text, sources)
    assert json.loads(raw)["units"][-1]["unit_id"] == units[-1]["id"]
    captured = []

    class Client:
        async def complete(self, _model, messages, **options):
            captured.append((messages, options))
            return SimpleNamespace(content=raw)

    async def client(_self):
        return Client()

    monkeypatch.setattr(RagRuntime, "_get_client", client)
    result = await object.__new__(RagRuntime).verify_answer(text, sources)
    assert json.loads(result) == {"supported": True}
    assert len(captured) == 1
    assert json.loads(captured[0][0][1].content)["answer_units"] == units
    assert captured[0][1]["max_tokens"] == 4096


def test_actual_supplier_single_key_answer_units_alias_preserves_valid_proof():
    from app.rag.verification_audit import checked_verification_audit

    text = (
        '根据当前有效的青岚设备运行规范 V2（生效日期：2026-0'
        '2-01，状态：当前有效）：\n\n- KX-17 的滤芯检查周'
        '期为每 60 天检查一次。\n- KX-170 的滤芯检查周期'
        '为每 30 天检查一次。'
    )
    sources = json.loads(
        '[{"id": "E1", "text": "公开合成验收资'
        '料：青岚设备运行规范 V2\\n本文为虚构设备资料，不对应真实'
        '厂商或产品。\\n文件标识：QL-SPEC-V2。版本：V2。'
        '生效日期：2026-02-01。状态：当前有效。本文替代 Q'
        'L-SPEC-V1。\\n\\nKX-17 的当前要求：仅限室内'
        '使用；连续运行温度上限为 42 摄氏度；环境相对湿度不得超过'
        ' 75%；单次连续运行最长 8 小时；滤芯每 60 天检查一'
        '次。更换滤芯前必须停机并断电。\\n\\nKX-170 的当前要'
        '求：仅限室内使用；连续运行温度上限为 58 摄氏度；环境相对'
        '湿度不得超过 85%；单次连续运行最长 12 小时；滤芯每 '
        '30 天检查一次。更换滤芯前必须停机并断电。\\n\\n运行条件'
        '必须同时满足，不能仅凭温度达标判断可以使用。上述温度为连续运'
        '行上限，不适用于启动测试。\\n\\n当前服务台办公时间为工作日'
        ' 09:00—17:00。报修必须提供设备编号、故障现象和最'
        '近一次检查日期。\\n\\n本规范没有给出售价、库存、防水等级或'
        '雨淋使用许可。"}]'
    )
    raw = (
        '{\n  "answer_units": [\n    {\n  '
        '    "unit_id": "U1:598789d09a"'
        ',\n      "verdict": "supported"'
        ',\n      "support": [\n        {'
        '\n          "evidence_id": "E1"'
        ',\n          "span_id": "E1:S1:'
        '1d88bd81e30cd5ab"\n        }\n  '
        '    ]\n    },\n    {\n      "unit'
        '_id": "U2:598789d09a",\n      "'
        'verdict": "supported",\n      "'
        'support": [\n        {\n        '
        '  "evidence_id": "E1",\n       '
        '   "span_id": "E1:S2:1d88bd81e'
        '30cd5ab"\n        }\n      ]\n   '
        ' },\n    {\n      "unit_id": "U3'
        ':598789d09a",\n      "verdict":'
        ' "supported",\n      "support":'
        ' [\n        {\n          "eviden'
        'ce_id": "E1",\n          "span_'
        'id": "E1:S3:1d88bd81e30cd5ab"\n'
        '        }\n      ]\n    }\n  ]\n}'
    )
    normalized = json.dumps({"units": json.loads(raw)["answer_units"]})
    assert checked_verification_audit(normalized, text, sources) is True
    assert checked_verification_audit(raw, text, sources) is True
