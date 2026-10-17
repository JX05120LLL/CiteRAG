"""Server measured user format constraints; no model, database, or private materials."""

import json

import pytest

from app.rag.response_review import ResponseReviewError, checked_general_review


def contract(text):
    from app.rag.task_constraints import make_task_contract

    return make_task_contract(text)


def v2_review(text, task, *, revision=None, accepted=True):
    candidate = text if revision is None else revision
    checks = [{"requirement_id": key, "verdict": "met", "quote": candidate[:120],
               "reason": "候选正文覆盖本项要求。"} for key in ("request", "scope", "precision")]
    checks.extend({"requirement_id": item["id"], "verdict": "met", "quote": candidate[:120],
                   "reason": "按照服务器定义检查候选格式。"} for item in task["requirements"])
    return {"version": 2, "accepted": accepted, "issues": [] if accepted else ["constraint_missed"],
            "revision": revision, "checks": checks}


@pytest.mark.parametrize("question,expected", [
    ("请用两行列出。", [("exact_lines", 2)]),
    ("请用2句话说明。", [("exact_sentences", 2)]),
    ("解释一下，控制在150字内。", [("max_chars", 150)]),
    ("请最多用一百五十字回答。", [("max_chars", 150)]),
    ("请用两句话，控制在50字内。", [("exact_sentences", 2), ("max_chars", 50)]),
    ("你好，我正在整理学习计划，有一点拖延。请用两句话鼓励我，别说空话。"
     "接下来请尽量回答简短一些。", [("exact_sentences", 2)]),
    ("解释递归和迭代的区别，用一个生活例子，控制在150字内。"
     "这是通用编程问题，不需要查设备资料。", [("max_chars", 150)]),
    ("仅根据这张图片，两台设备的编号、连续运行温度上限、滤芯检查周期分别是什么？"
     "用两行列出；看不清就明确说，别猜。", [("exact_lines", 2)]),
])
def test_explicit_generic_formats_are_compiled_with_exact_source_evidence(question, expected):
    from app.rag.task_constraints import validate_task_contract

    task = contract(question)
    assert [(item["kind"], item["value"]) for item in task["requirements"]] == expected
    assert all(item["source_quote"] in question for item in task["requirements"])
    assert validate_task_contract(task, question) == task
    assert len(json.dumps(task, ensure_ascii=False)) < 650


@pytest.mark.parametrize("question", [
    "不要用两行，正常解释。", "最多两句话。", "用两行或三行列出。",
    "请用两行。请用三行。", "请解释‘用两行列出’是什么意思。",
    '图上写着“控制在150字内”，请描述图片。',
    "```text\n请用两行列出\n```\n请解释代码。",
    "`请用两句话`是一条什么指令？", "> 请用两行列出\n这句话是什么意思？",
    "文章最多150字吗？", "请尽量用两句话说明。",
    "用一二十行说明。", "用零十行说明。", "用二百百字说明。",
    "用999999999999999999999999999999999999999999999999行说明。",
    "用两行数据计算总和：第一行是2，第二行是3。只输出总和。",
    "请用150字节的消息举例，说明字节和字符的区别。",
    "用两行合适吗？我想听听你的建议。", "请用两句话可以吗？",
    "请用150字节。", "用两行输入替换这一行输出。",
])
def test_negated_quoted_ambiguous_and_unsupported_limits_are_not_invented(question):
    assert contract(question)["requirements"] == []


def test_directive_prefix_consumes_only_a_bounded_linear_token_sequence():
    import app.rag.task_constraints as module

    # Exercise the formerly exponential overlapping 请/请你/你 branch at a
    # finite size. A token-consumption counter proves progress without timing.
    prefix = "请你" * 900
    class Probe(str):
        visits = 0

        def startswith(self, token, start=0, end=None):
            self.visits += 1
            return super().startswith(token, start, end)

    for value, expected in ((Probe(prefix), True), (Probe(prefix + "x"), False)):
        assert module._directive_prefix(value) is expected
        assert value.visits <= len(value) * len(module.PREFIX_TOKENS)
    assert contract(prefix + "x用两行说明。")["requirements"] == []
    assert [(r["kind"], r["value"]) for r in contract(prefix + "用两行说明。")["requirements"]] == [
        ("exact_lines", 2)]


def test_source_hash_blocks_reusing_a_contract_for_different_user_text():
    from app.rag.task_constraints import TaskConstraintError, validate_task_contract

    with pytest.raises(TaskConstraintError):
        validate_task_contract(contract("请用两行列出。"), "请用三行列出。")


def test_counts_use_nonempty_logical_lines_and_ignore_decimal_sentence_dots():
    from app.rag.task_constraints import check_constraints

    assert check_constraints("第一项\n\n第二项", contract("用两行列出。"))[0]["actual"] == 2
    assert check_constraints("值为3.14。第二句", contract("用两句话说明。"))[0]["actual"] == 2
    assert check_constraints("甲 乙\n丙", contract("控制在3字内。"))[0]["actual"] == 3


def test_an_accepted_single_line_cannot_override_the_server_line_count():
    task = contract("用两行列出。")
    draft = "AX-11，42℃；AX-110，58℃。"
    with pytest.raises(ResponseReviewError):
        checked_general_review(v2_review(draft, task), draft, contract=task, strict=True)
    revised = "AX-11，42℃。\nAX-110，58℃。"
    assert checked_general_review(v2_review(draft, task, accepted=False, revision=revised),
        draft, contract=task, strict=True) == revised


@pytest.mark.parametrize("corruption", ["bare-accepted", "missing", "duplicate", "unknown",
                                         "fake-quote", "old-draft-quote", "unmet",
                                         "bad-verdict-type"])
def test_v2_review_requires_complete_exact_candidate_coverage(corruption):
    task = contract("用两句话，控制在30字内。")
    draft, revision = "甲。乙。", "丙。丁。"
    data = v2_review(draft, task, accepted=False, revision=revision)
    if corruption == "bare-accepted":
        data = {"accepted": True, "issues": [], "revision": None}
    elif corruption == "missing":
        data["checks"].pop()
    elif corruption == "duplicate":
        data["checks"][-1] = dict(data["checks"][0])
    elif corruption == "unknown":
        data["checks"][-1]["requirement_id"] = "not-a-requirement"
    elif corruption == "fake-quote":
        data["checks"][0]["quote"] = "不存在的候选正文"
    elif corruption == "old-draft-quote":
        data["checks"][0]["quote"] = draft
    elif corruption == "bad-verdict-type":
        data["checks"][0]["verdict"] = []
    else:
        data["checks"][0]["verdict"] = "unmet"
    with pytest.raises(ResponseReviewError):
        checked_general_review(data, draft, contract=task, strict=True)


def test_legacy_helper_remains_explicitly_legacy_but_cannot_pass_production_v2():
    old = '{"accepted":true,"issues":[],"revision":null}'
    assert checked_general_review(old, "旧测试正文") == "旧测试正文"
    with pytest.raises(ResponseReviewError):
        checked_general_review(old, "旧测试正文", contract=contract("你好"), strict=True)


def test_noop_revision_does_not_make_up_a_change_and_still_checks_constraints():
    draft = "金额为368.10元。"
    task = contract("给出金额。")
    data = v2_review(draft, task, accepted=False, revision=draft)
    assert checked_general_review(data, draft, contract=task, strict=True) == draft


@pytest.mark.parametrize("requirement", ["precision", "request", "C1"])
def test_only_precision_may_be_not_applicable(requirement):
    task, draft = contract("用两行列出。"), "甲\n乙"
    value = v2_review(draft, task)
    item = next(check for check in value["checks"] if check["requirement_id"] == requirement)
    item.update(verdict="not_applicable", quote="")
    if requirement == "precision":
        assert checked_general_review(value, draft, contract=task, strict=True) == draft
    else:
        with pytest.raises(ResponseReviewError):
            checked_general_review(value, draft, contract=task, strict=True)
