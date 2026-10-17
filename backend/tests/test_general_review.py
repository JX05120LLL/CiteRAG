"""Task completion review with synthetic drafts, no supplier or business database."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.rag.answer_adapter import AnswerError, LightRAGAnswerAdapter
from app.rag.runtime import RagRuntime
from app.rag.task_constraints import contract_hash, make_task_contract
from app.services.answers import AnswerService
from app.services.errors import ServiceError
from app.services.token_budget import ANSWER_INPUT_TOKENS, estimate_messages

WEAK_DRAFTS = [
    ("我今天只有20分钟，怎么开始？",
     "20分钟足够开始。选一个最小任务立刻动手，行动本身就能打破拖延。",
     "先用2分钟选学习小节，15分钟读并写3个要点，最后3分钟圈出一个疑问，留下明天的起点。",
     "missing_action"),
    ("请用两句话鼓励我做学习计划，别说空话，我希望有下一步。",
     "你已经迈出了开始的一步，这比完美计划更重要。现在只需专注下一分钟，行动就会自然延续。",
     "先把今天要学的一小节写在纸上，给它留一个开始时间。开始后先写一个自己的问题，完成它就是今天的进展。",
     "missing_result"),
    ("解释递归和迭代的区别，用一个生活例子，控制在150字内。",
     "递归是函数调用自身；迭代用循环。递归更简洁但可能占更多内存；迭代更高效但代码稍长。",
     "递归把任务交给规模更小的同类任务，并在基本情况停止；迭代用循环逐步更新。像找套盒里的物件：递归逐层交给下一层，迭代自己逐层打开；效率取决于算法和实现。",
     "unsupported_generalization"),
]


def review(*, accepted=True, issue=None, revision=None, draft=None, task=None):
    value = {"accepted": accepted, "issues": [] if accepted else [issue], "revision": revision}
    if draft is not None:
        candidate = draft if accepted else revision
        keys = ["request", "scope", "precision"] + [item["id"] for item in task["requirements"]]
        value.update(version=2, checks=[
            {"requirement_id": key, "verdict": "met", "quote": candidate[:120],
             "reason": "离线候选覆盖检查。"} for key in keys])
    return json.dumps(value, ensure_ascii=False)


@pytest.mark.parametrize("question,draft,revised,issue", WEAK_DRAFTS)
async def test_weak_draft_is_revised_once_before_becoming_an_answer(
    monkeypatch, question, draft, revised, issue,
):
    calls = []

    class Client:
        async def complete(self, _model, messages, **options):
            calls.append((json.loads(messages[-1].content), options))
            return SimpleNamespace(content=(json.dumps({"text": draft}) if len(calls) == 1
                else review(accepted=False, issue=issue, revision=revised,
                            draft=draft, task=calls[-1][0]["task_contract"])))

    async def client(_self):
        return Client()

    monkeypatch.setattr(RagRuntime, "_get_client", client)
    context = {"turns": [{"user": "请简短一些", "assistant": "好的", "answer_kind": "general"}]}
    answerer = LightRAGAnswerAdapter(object.__new__(RagRuntime))
    result = await AnswerService(None)._resolve(None, "ordinary", question, None, answerer,
                                               "auto", {"mode": "general"}, context)
    assert result == ("answered", revised, [])
    assert len(calls) == 2
    assert calls[1][0]["question"] == question
    assert calls[1][0]["draft"] == draft
    assert calls[1][0]["conversation_context"] == context
    assert calls[1][0]["tool_results"] == []
    assert "allowed_tools" not in calls[1][0] and "evidence" not in calls[1][0]
    assert calls[1][1]["response_format"] == "json_object"


async def test_accepted_draft_is_not_rewritten(monkeypatch):
    from app.rag.response_review import checked_general_review

    draft = "你好，有什么我可以帮忙的？"
    assert checked_general_review(review(), draft) == draft


@pytest.mark.parametrize("bad", [
    {}, {"accepted": True, "issues": ["missing_action"], "revision": None},
    {"accepted": True, "issues": [], "revision": "silently changed"},
    {"accepted": False, "issues": [], "revision": "changed"},
    {"accepted": False, "issues": ["unknown"], "revision": "changed"},
    {"accepted": False, "issues": ["missing_action"], "revision": None},
    {"accepted": False, "issues": ["missing_action"], "revision": ""},
    {"accepted": False, "issues": ["missing_action"], "revision": "x" * 1001},
    {"accepted": False, "issues": ["missing_action"], "revision": "https://invented.invalid"},
    {"accepted": False, "issues": ["missing_action"], "revision": "hidden\x00text"},
    {"accepted": 1, "issues": [], "revision": None},
    {"accepted": True, "issues": [], "revision": None, "tool_id": "injected"},
])
def test_review_contract_fails_closed(bad):
    from app.rag.response_review import ResponseReviewError, checked_general_review

    with pytest.raises(ResponseReviewError):
        checked_general_review(json.dumps(bad), "draft")


async def test_invalid_review_fails_without_a_third_request(monkeypatch):
    calls = []

    class Client:
        async def complete(self, *_args, **_kwargs):
            calls.append(1)
            return SimpleNamespace(content='{"text":"draft"}' if len(calls) == 1 else "bad")

    async def client(_self):
        return Client()

    monkeypatch.setattr(RagRuntime, "_get_client", client)
    with pytest.raises(AnswerError, match="^answer_unverifiable$"):
        await LightRAGAnswerAdapter(object.__new__(RagRuntime)).general_answer("question", {})
    assert len(calls) == 2


async def test_voice_yields_no_draft_while_review_is_pending_and_cancel_propagates(monkeypatch):
    entered = asyncio.Event()

    class Client:
        calls = 0

        async def complete(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(content='{"text":"unreviewed draft"}')
            entered.set()
            await asyncio.Future()

    fake = Client()

    async def client(_self):
        return fake

    monkeypatch.setattr(RagRuntime, "_get_client", client)
    seen = []

    async def consume():
        async for piece in object.__new__(RagRuntime).stream_general("question", {}):
            seen.append(piece)

    task = asyncio.create_task(consume())
    await asyncio.wait_for(entered.wait(), 1)
    assert seen == []
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert seen == [] and fake.calls == 2


@pytest.mark.parametrize("channel", ["text", "voice"])
async def test_reviewed_code_is_preserved_for_text_and_explained_orally_for_voice(
    monkeypatch, channel,
):
    code = "```python\nprint(1)\n```"
    oral = "在 Python 中调用 print 函数，传入数字一，就会打印一。"
    captured = []

    class Client:
        async def complete(self, _model, messages, **_options):
            captured.append(messages)
            return SimpleNamespace(content=json.dumps({"text": code}) if len(captured) == 1
                else review(draft=code, task=json.loads(messages[-1].content)["task_contract"])
                if channel == "text" else review(accepted=False, issue="constraint_missed",
                    revision=oral, draft=code,
                    task=json.loads(messages[-1].content)["task_contract"]))

    async def client(_self):
        return Client()

    monkeypatch.setattr(RagRuntime, "_get_client", client)
    spoken = []

    async def segment(text):
        spoken.append(text)

    result = await AnswerService(None)._resolve(
        None, "ordinary", "给我一行 Python 打印代码", None,
        LightRAGAnswerAdapter(object.__new__(RagRuntime)), "auto", {"mode": "general"}, {},
        on_general_segment=segment if channel == "voice" else None,
    )
    assert result == ("answered", code if channel == "text" else oral, [])
    assert len(captured) == 2
    if channel == "voice":
        assert "".join(spoken) == oral
        assert "口语" in captured[0][0].content and "代码块" in captured[0][0].content
        assert "口头说明" in captured[1][0].content
        assert "constraint_missed" in captured[1][0].content
    else:
        assert spoken == []
        assert "仅输出口语正文" not in captured[0][0].content


async def test_voice_still_rejects_an_accepted_code_block_without_a_third_request(monkeypatch):
    calls = []

    class Client:
        async def complete(self, _model, messages, **_kwargs):
            calls.append(1)
            return SimpleNamespace(content='{"text":"```python\\nprint(1)\\n```"}'
                if len(calls) == 1 else review(draft="```python\nprint(1)\n```",
                    task=json.loads(messages[-1].content)["task_contract"]))

    async def client(_self):
        return Client()

    monkeypatch.setattr(RagRuntime, "_get_client", client)
    spoken = []

    async def segment(text):
        spoken.append(text)

    with pytest.raises(AnswerError, match="^answer_format_invalid$"):
        await AnswerService(None)._resolve(
            None, "ordinary", "打印代码", None,
            LightRAGAnswerAdapter(object.__new__(RagRuntime)), "auto", {"mode": "general"}, {},
            on_general_segment=segment,
        )
    assert spoken == [] and len(calls) == 2


@pytest.mark.parametrize("voice", [False, True])
async def test_router_rewrite_never_replaces_original_generation_request(voice):
    received, spoken = [], []

    class Answerer:
        async def general_answer(self, question, context):
            received.append(question)
            return '{"text":"reviewed"}'

        async def stream_general(self, question, context):
            received.append(question)
            yield "reviewed。"

    async def speak(segment):
        spoken.append(segment)

    question = "Explain recursion under 150 words and use one everyday example."
    await AnswerService(None)._resolve(None, "ordinary", question, None, Answerer(), "auto",
        {"mode": "general", "query": "Explain recursion"}, {},
        on_general_segment=speak if voice else None)
    assert received == [question]


def agent_fixture(monkeypatch, reviewer):
    from app.agent import hooks as module
    from app.models import AgentStep

    journal, calls = [], []
    task = make_task_contract("original request")
    run = SimpleNamespace(model_rounds=1, active_ms=0, event_seq=0, voice_session_id=None,
        prepared={"question": "original request", "context": {"_task_contract": task},
                  "general": True})
    valid = {"value": True}

    class Session:
        async def scalar(self, _query):
            return next((step for step in journal if step.key == "answer_review"), None)

        async def get(self, _model, identifier):
            return next(step for step in journal if step.id == identifier)

        def add(self, step):
            if isinstance(step, AgentStep):
                step.id = uuid4()
                journal.append(step)

        async def commit(self):
            pass

    @asynccontextmanager
    async def sessions():
        yield Session()

    async def fenced(*_args):
        if not valid["value"]:
            raise ServiceError(409, "agent_interrupted", "synthetic cancellation")
        return run

    async def wrapped(question, context, draft, tool_results=None, *, response_channel="text"):
        assert response_channel == ("voice" if run.voice_session_id is not None else "text")
        calls.append((question, context, draft, tool_results))
        return await reviewer(draft, task)

    monkeypatch.setattr(module, "fenced", fenced)
    monkeypatch.setattr(module, "emit", lambda *_args: None)
    runner = SimpleNamespace(id=uuid4(), db=SimpleNamespace(sessions=sessions),
                             assert_owned=lambda: None)
    hooks = module.RunHooks(runner, uuid4(), 1, SimpleNamespace(review_general=wrapped))
    state = {"decision": {"answer": {"text": "weak draft"}}, "results": [
        {"tool_id": "local.calculate", "status": "succeeded", "data": {"value": "42"}}]}
    return hooks, state, run, journal, calls, valid


async def test_agent_review_is_journaled_and_replayed_without_repeat_supplier_call(monkeypatch):
    async def reviewer(draft, task):
        return review(accepted=False, issue="missing_action", revision="revised action",
                      draft=draft, task=task)

    hooks, state, run, journal, calls, _ = agent_fixture(monkeypatch, reviewer)
    assert await hooks.finish(state) == {"text": "revised action"}
    assert await hooks.finish(state) == {"text": "revised action"}
    assert run.model_rounds == 2 and len(calls) == 1
    assert len(journal) == 1 and journal[0].status == "succeeded"
    expected_context = {"task_supplements": [],
                        "_task_contract": make_task_contract("original request")}
    assert calls[0] == ("original request", expected_context,
                        "weak draft", state["results"])
    assert journal[0].data["changed"] is True
    assert journal[0].data["review_version"] == 2


async def test_agent_voice_review_receives_server_owned_channel(monkeypatch):
    async def reviewer(draft, task):
        return review(accepted=False, issue="constraint_missed", revision="口头说明。",
                      draft=draft, task=task)

    hooks, state, run, _journal, calls, _ = agent_fixture(monkeypatch, reviewer)
    run.voice_session_id = uuid4()
    hooks.runner.check_voice = AsyncMock()
    assert await hooks.finish(state) == {"text": "口头说明。"}
    assert len(calls) == 1
    assert hooks.runner.check_voice.await_count == 2


@pytest.mark.parametrize("cached", [False, True])
async def test_agent_voice_rejects_code_in_review_or_replayed_result(monkeypatch, cached):
    async def reviewer(draft, task):
        return review(draft=draft, task=task)

    hooks, state, run, journal, calls, _ = agent_fixture(monkeypatch, reviewer)
    run.voice_session_id = uuid4()
    hooks.runner.check_voice = AsyncMock()
    state["decision"]["answer"] = {"text": "```python\nprint(1)\n```"}
    if cached:
        draft = state["decision"]["answer"]["text"]
        task = run.prepared["context"]["_task_contract"]
        journal.append(SimpleNamespace(id=uuid4(), key="answer_review", status="succeeded",
            data={"answer": {"text": draft}, "review_version": 2, "changed": False,
                  "contract_hash": contract_hash(task),
                  "review": json.loads(review(draft=draft, task=task))}))
    with pytest.raises(AnswerError, match="^answer_format_invalid$"):
        await hooks.finish(state)
    assert len(calls) == (0 if cached else 1)
    if not cached:
        assert journal[0].status == "running"


async def test_agent_late_review_cannot_publish_after_binding_or_generation_revoked(monkeypatch):
    async def reviewer(draft, task):
        valid["value"] = False
        return review(draft=draft, task=task)

    hooks, state, _run, journal, calls, valid = agent_fixture(monkeypatch, reviewer)
    with pytest.raises(ServiceError, match="synthetic cancellation"):
        await hooks.finish(state)
    assert journal[0].status == "running" and journal[0].data is None
    assert len(calls) == 1


async def test_concurrent_or_cancelled_review_is_not_blindly_replayed(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async def reviewer(draft, task):
        entered.set()
        await release.wait()
        return review(draft=draft, task=task)

    hooks, state, _run, journal, calls, _ = agent_fixture(monkeypatch, reviewer)
    first = asyncio.create_task(hooks.finish(state))
    await asyncio.wait_for(entered.wait(), 1)
    with pytest.raises(AnswerError, match="^answer_unverifiable$"):
        await hooks.finish(state)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    with pytest.raises(AnswerError, match="^answer_unverifiable$"):
        await hooks.finish(state)
    assert len(calls) == 1 and journal[0].status == "running"


async def test_agent_review_cannot_reset_model_budget(monkeypatch):
    async def reviewer(draft, task):
        pytest.fail("spent model budget must be enforced before review")

    from app.agent.errors import AgentError

    hooks, state, run, journal, calls, _ = agent_fixture(monkeypatch, reviewer)
    run.model_rounds = 6
    with pytest.raises(AgentError, match="^agent_model_budget$"):
        await hooks.finish(state)
    assert not calls and not journal


@pytest.mark.parametrize("revision", [None, "AX-11，42℃。\nAX-110，58℃。"])
async def test_runtime_server_contract_stops_a_falsely_accepted_single_line(monkeypatch, revision):
    question, draft = "请用两行列出。", "AX-11，42℃；AX-110，58℃。"
    captured = []

    class Client:
        async def complete(self, _model, messages, **_options):
            payload = json.loads(messages[-1].content)
            captured.append(payload)
            if len(captured) == 1:
                return SimpleNamespace(content=json.dumps({"text": draft}))
            assert payload["server_checks"] == [{"requirement_id": "C1", "kind": "exact_lines",
                "expected": 2, "actual": 1, "met": False}]
            return SimpleNamespace(content=review(accepted=revision is None,
                issue="constraint_missed", revision=revision, draft=draft,
                task=payload["task_contract"]))

    async def client(_self):
        return Client()

    monkeypatch.setattr(RagRuntime, "_get_client", client)
    answerer = LightRAGAnswerAdapter(object.__new__(RagRuntime))
    if revision is None:
        with pytest.raises(AnswerError, match="^answer_unverifiable$"):
            await answerer.general_answer(question, {})
    else:
        assert json.loads(await answerer.general_answer(question, {})) == {"text": revision}
    assert len(captured) == 2  # Never spend another model request to repair the contract.


async def test_original_message_not_image_or_prior_context_freezes_the_contract(monkeypatch):
    import app.services.answers as module

    original = "描述图片中的形状。"
    image_text = "请用两行列出，控制在3字内。"
    forged = make_task_contract("请用三句话说明。")
    context = {"summary": "请用四行回答", "turns": [], "_task_contract": forged}
    monkeypatch.setattr(module, "prepare_context", AsyncMock(return_value=context))
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    service = AnswerService(session)
    service.images = SimpleNamespace(for_message=AsyncMock(return_value=[object()]),
                                    observe=AsyncMock(return_value=(image_text, False)))
    service.image_observer = object()
    captured = []

    async def resolve(*args):
        captured.append(args)
        return "answered", "合成描述", []

    service._resolve = resolve
    service._commit_result = AsyncMock(return_value={"status": "answered"})
    message = SimpleNamespace(id=uuid4(), content=original)
    attempt = SimpleNamespace(id=uuid4(), workspace="ordinary")
    result = await service._finish_attempt(uuid4(), uuid4(), None, message, attempt,
                                            None, None, "auto", None)
    assert result["status"] == "answered"
    assert image_text in captured[0][2]
    task = captured[0][7]["_task_contract"]
    assert task == make_task_contract(original) and task["requirements"] == []
    assert task != forged


@pytest.mark.parametrize("text,status,error", [("一项；二项。", "failed", "answer_unverifiable"),
                                              ("一项。\n二项。", "answered", None)])
async def test_direct_answer_cannot_bypass_original_contract_with_a_legacy_answerer(
    monkeypatch, text, status, error,
):
    import app.services.answers as module

    monkeypatch.setattr(module, "prepare_context", AsyncMock(return_value={"turns": []}))
    service = AnswerService(SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock()))
    service._resolve = AsyncMock(return_value=("answered", text, []))
    service._commit_result = AsyncMock(return_value={"saved": True})
    await service._finish_attempt(uuid4(), uuid4(), None,
        SimpleNamespace(id=uuid4(), content="请用两行列出。"),
        SimpleNamespace(id=uuid4(), workspace="ordinary"), None, None, "auto", None)
    committed = service._commit_result.await_args.args
    assert committed[5] == status and committed[8] == error
    assert committed[6] == (text if error is None else "")


@pytest.mark.parametrize("text,commits", [("甲；乙。", 0), ("甲。\n乙。", 1)])
async def test_resumed_answer_rechecks_original_user_limits_before_commit(text, commits):
    service = AnswerService(None)
    service._owned_conversation = AsyncMock(return_value=(object(), None))
    service._commit_result = AsyncMock(return_value={"text": text})
    message = SimpleNamespace(content="用两行列出。")
    # A changed prepared contract or an appended question cannot override the real message.
    prepared = {"general": True, "question": "不限制行数。", "context": {}}
    args = (uuid4(), uuid4(), message, SimpleNamespace(workspace="ordinary"),
            None, prepared, {"text": text})
    if commits:
        assert (await service.finish_prepared(*args))["text"] == text
    else:
        with pytest.raises(AnswerError, match="^answer_unverifiable$"):
            await service.finish_prepared(*args)
    assert service._commit_result.await_count == commits


async def test_agent_noop_revision_records_actual_changed_false(monkeypatch):
    async def reviewer(draft, task):
        return review(accepted=False, issue="constraint_missed", revision=draft,
                      draft=draft, task=task)

    hooks, state, _run, journal, calls, _ = agent_fixture(monkeypatch, reviewer)
    result = await hooks.finish(state)
    assert result == state["decision"]["answer"]
    assert journal[0].data["accepted"] is False
    assert journal[0].data["changed"] is False
    assert await hooks.finish(state) == result and len(calls) == 1


@pytest.mark.parametrize("corruption", ["legacy", "contract_hash", "changed", "review_quote"])
async def test_agent_cached_review_must_match_current_contract_and_actual_candidate(
    monkeypatch, corruption,
):
    async def reviewer(draft, task):
        return review(draft=draft, task=task)

    hooks, state, _run, journal, calls, _ = agent_fixture(monkeypatch, reviewer)
    await hooks.finish(state)
    saved = journal[0].data
    if corruption == "legacy":
        journal[0].data = {"answer": saved["answer"]}
    elif corruption == "contract_hash":
        saved["contract_hash"] = "0" * 64
    elif corruption == "changed":
        saved["changed"] = True
    else:
        saved["review"]["checks"][0]["quote"] = "不是缓存中的候选正文"
    with pytest.raises(AnswerError, match="^answer_unverifiable$"):
        await hooks.finish(state)
    assert len(calls) == 1  # Bad/old cache never requests a paid replacement implicitly.


async def test_old_pending_run_without_original_contract_does_not_start_paid_review(monkeypatch):
    async def reviewer(_draft, _task):
        pytest.fail("legacy pending run cannot blindly spend a new review request")

    hooks, state, run, journal, calls, _ = agent_fixture(monkeypatch, reviewer)
    run.prepared["context"].pop("_task_contract")
    with pytest.raises(AnswerError, match="^answer_unverifiable$"):
        await hooks.finish(state)
    assert calls == [] and journal == []


async def test_voice_hard_constraint_rejection_never_publishes_unreviewed_audio_text(monkeypatch):
    question, draft = "用两行列出。", "一项；二项。"
    calls, spoken = [], []

    class Client:
        async def complete(self, _model, messages, **_options):
            payload = json.loads(messages[-1].content)
            calls.append(payload)
            return SimpleNamespace(content=(json.dumps({"text": draft}) if len(calls) == 1
                else review(draft=draft, task=payload["task_contract"])))

    async def client(_self):
        return Client()

    async def speak(text):
        spoken.append(text)

    monkeypatch.setattr(RagRuntime, "_get_client", client)
    with pytest.raises(AnswerError, match="^answer_unverifiable$"):
        await AnswerService(None)._resolve(None, "ordinary", question, None,
            LightRAGAnswerAdapter(object.__new__(RagRuntime)), "auto", {"mode": "general"}, {},
            on_general_segment=speak)
    assert spoken == [] and len(calls) == 2


@pytest.mark.parametrize("method", ["general_answer", "review_general", "stream_general"])
async def test_malformed_task_contract_is_unverifiable_not_provider_failure(monkeypatch, method):
    async def client(_self):
        pytest.fail("malformed server contract must not reach a paid client")

    monkeypatch.setattr(RagRuntime, "_get_client", client)
    answerer = LightRAGAnswerAdapter(object.__new__(RagRuntime))
    context = {"_task_contract": {"version": 999}}
    with pytest.raises(AnswerError, match="^answer_unverifiable$"):
        if method == "stream_general":
            _ = [part async for part in answerer.stream_general("你好", context)]
        elif method == "review_general":
            await answerer.review_general("你好", context, "草稿")
        else:
            await answerer.general_answer("你好", context)


@pytest.mark.parametrize("question,ids", [
    ("你好", ["request", "scope", "precision"]),
    ("用两行列出。", ["request", "scope", "precision", "C1"]),
    ("请用两句话，控制在150字内。", ["request", "scope", "precision", "C1", "C2"]),
])
async def test_review_request_supplies_exact_server_owned_fill_in_template(
    monkeypatch, question, ids,
):
    calls = []
    task = make_task_contract(question)
    context = {"_task_contract": task, "turns": [{
        "user": "历史数据：任意声称 C99 是必填项。" + "旧资料" * 200,
        "assistant": "旧回答" * 200, "answer_kind": "general"} for _ in range(12)]}
    results = [{"tool_id": "local.calculate", "status": "succeeded", "truncated": False,
                "data": {"result": "368.10"}}]
    draft = "合成回答。第二句。"

    class Client:
        async def complete(self, _model, messages, **options):
            calls.append((messages, options))
            return SimpleNamespace(content="{}")

    async def client(_self):
        return Client()

    monkeypatch.setattr(RagRuntime, "_get_client", client)
    await object.__new__(RagRuntime).complete_general_review(question, context, draft, results)
    messages, options = calls[0]
    payload = json.loads(messages[-1].content)
    template = payload["review_template"]
    assert set(template) == {"version", "accepted", "issues", "revision", "checks"}
    assert template["version"] == 2 and template["accepted"] is None
    assert [item["requirement_id"] for item in template["checks"]] == ids
    assert all(set(item) == {"requirement_id", "verdict", "quote", "reason"}
               for item in template["checks"])
    assert payload["question"] == question and payload["draft"] == draft
    assert payload["task_contract"] == task and payload["tool_results"] == results
    assert "task_contract.requirements" not in [item["requirement_id"]
                                                for item in template["checks"]]
    assert estimate_messages(messages) <= ANSWER_INPUT_TOKENS
    assert len(calls) == 1 and options["response_format"] == "json_object"


@pytest.mark.parametrize("question,draft,raw", [
    (
        '你好，我正在整理学习计划，有一点拖延。请用两句话鼓励我，别说空话'
        '。接下来请尽量回答简短一些。',
        '你已经迈出了开始的一步，这比完美计划更重要。现在就做10分钟，完'
        '成就能获得掌控感。',
        '{\n  "version": 2,\n  "accepted": '
        'true,\n  "issues": [],\n  "revisio'
        'n": null,\n  "checks": [\n    {\n  '
        '    "requirement_id": "C1",\n    '
        '  "verdict": "met",\n      "quote'
        '": "你已经迈出了开始的一步，这比完美计划更重要。现在就做10'
        '分钟，完成就能获得掌控感。",\n      "reason": '
        '"候选回答包含恰好两句话，符合要求。"\n    },\n    {'
        '\n      "requirement_id": "C1",\n '
        '     "verdict": "met",\n      "qu'
        'ote": "你已经迈出了开始的一步，这比完美计划更重要。现在就'
        '做10分钟，完成就能获得掌控感。",\n      "reason'
        '": "句子数量准确，无冗余或缺失。"\n    },\n    {'
        '\n      "requirement_id": "C1",\n '
        '     "verdict": "met",\n      "qu'
        'ote": "你已经迈出了开始的一步，这比完美计划更重要。现在就'
        '做10分钟，完成就能获得掌控感。",\n      "reason'
        '": "内容具体，动作明确（做10分钟），有可验证产物（完成即获'
        '掌控感）。"\n    },\n    {\n      "requi'
        'rement_id": "C1",\n      "verdict'
        '": "met",\n      "quote": "你已经迈出了'
        '开始的一步，这比完美计划更重要。现在就做10分钟，完成就能获得掌'
        '控感。",\n      "reason": "未泛化条件，未使用'
        '空话，符合‘不说空话’要求。"\n    }\n  ]\n}',
    ),
    (
        '我今天只有20分钟，怎么开始？',
        '利用这20分钟，先明确一个具体目标（如完成某项任务的50%），然'
        '后专注执行，每5分钟检查进度。完成后可评估是否需调整计划。',
        '{\n  "version": 2,\n  "accepted": '
        'true,\n  "issues": [],\n  "revisio'
        'n": null,\n  "checks": [\n    {\n  '
        '    "requirement_id": "request",'
        '\n      "verdict": "met",\n      "'
        'quote": "利用这20分钟，先明确一个具体目标（如完成某项'
        '任务的50%），然后专注执行，每5分钟检查进度。完成后可评估是否'
        '需调整计划。",\n      "reason": "完整回应了‘'
        '如何开始’的请求，提供具体动作：定目标、执行、定时检查、评估调整'
        '，符合时间限制。"\n    },\n    {\n      "re'
        'quirement_id": "scope",\n      "v'
        'erdict": "met",\n      "quote": "'
        '利用这20分钟，先明确一个具体目标（如完成某项任务的50%），然'
        '后专注执行，每5分钟检查进度。完成后可评估是否需调整计划。",\n'
        '      "reason": "仅聚焦用户当前20分钟时间安排'
        '，未扩展至长期规划或无关领域。"\n    },\n    {\n  '
        '    "requirement_id": "precision'
        '",\n      "verdict": "met",\n     '
        ' "quote": "利用这20分钟，先明确一个具体目标（如完成'
        '某项任务的50%），然后专注执行，每5分钟检查进度。完成后可评估'
        '是否需调整计划。",\n      "reason": "时间单位'
        '精确到分钟，目标量化为‘完成50%’，无泛化表述。"\n    }'
        ',\n    {\n      "requirement_id": '
        '"task_contract.requirements",\n  '
        '    "verdict": "not_applicable",'
        '\n      "quote": "",\n      "reaso'
        'n": "任务合同中无具体要求项，无需验证。"\n    }\n  '
        ']\n}',
    ),
    (
        '解释递归和迭代的区别，用一个生活例子，控制在150字内。这是通用'
        '编程问题，不需要查设备资料。',
        '递归是自己调用自己，像拆礼物盒：每层盒子都一样，直到打开无内盒。'
        '迭代是重复执行步骤，如数楼梯：一步步走，直到到顶。递归像函数调用'
        '栈，易栈溢出；迭代更省内存，适合循环任务。',
        '{\n  "version": 2,\n  "accepted": '
        'true,\n  "issues": [],\n  "revisio'
        'n": null,\n  "checks": [\n    {\n  '
        '    "requirement_id": "C1",\n    '
        '  "verdict": "met",\n      "quote'
        '": "递归是自己调用自己，像拆礼物盒：每层盒子都一样，直到打开'
        '无内盒。迭代是重复执行步骤，如数楼梯：一步步走，直到到顶。递归像'
        '函数调用栈，易栈溢出；迭代更省内存，适合循环任务。",\n    '
        '  "reason": "字数85，符合150字以内要求"\n  '
        '  },\n    {\n      "requirement_id'
        '": "C1",\n      "verdict": "met",'
        '\n      "quote": "递归是自己调用自己，像拆礼物盒'
        '：每层盒子都一样，直到打开无内盒。迭代是重复执行步骤，如数楼梯：'
        '一步步走，直到到顶。递归像函数调用栈，易栈溢出；迭代更省内存，适'
        '合循环任务。",\n      "reason": "内容完整覆盖'
        '递归与迭代区别，含生活例子"\n    },\n    {\n    '
        '  "requirement_id": "C1",\n      '
        '"verdict": "met",\n      "quote":'
        ' "递归是自己调用自己，像拆礼物盒：每层盒子都一样，直到打开无内'
        '盒。迭代是重复执行步骤，如数楼梯：一步步走，直到到顶。递归像函数'
        '调用栈，易栈溢出；迭代更省内存，适合循环任务。",\n      '
        '"reason": "未泛化条件，数字与单位准确，无不确定性"\n'
        '    },\n    {\n      "requirement_'
        'id": "C1",\n      "verdict": "met'
        '",\n      "quote": "递归是自己调用自己，像拆礼'
        '物盒：每层盒子都一样，直到打开无内盒。迭代是重复执行步骤，如数楼'
        '梯：一步步走，直到到顶。递归像函数调用栈，易栈溢出；迭代更省内存'
        '，适合循环任务。",\n      "reason": "仅针对递'
        '归与迭代区别，无无关比较或重复背景"\n    }\n  ]\n}',
    ),
], ids=["actual-review-2", "actual-review-4", "actual-review-7"])
def test_actual_initial_supplier_review_shape_failures_remain_rejected(question, draft, raw):
    from app.rag.response_review import ResponseReviewError, checked_general_review

    # Public synthetic responses are preserved verbatim, including invalid IDs.
    # New request templates never loosen the parser to accept those failures.
    with pytest.raises(ResponseReviewError):
        checked_general_review(raw, draft, contract=make_task_contract(question), strict=True)


# Exact public synthetic review #51: diagnostic labels changed, its revision did not.
ACTUAL_PRECISION_REVIEW = (
    '{\n  "version": 2,\n  "accepted"'
    ': false,\n  "issues": [\n    "co'
    'nstraint_missed",\n    "precisi'
    'on"\n  ],\n  "revision": "最终金额为3'
    '68.10。",\n  "checks": [\n    {\n '
    '     "requirement_id": "reques'
    't",\n      "verdict": "met",\n  '
    '    "quote": "最终金额为368.10。",\n '
    '     "reason": "已按要求计算并给出结果，符合'
    '实际调用计算器的指令。"\n    },\n    {\n    '
    '  "requirement_id": "scope",\n '
    '     "verdict": "met",\n      "'
    'quote": "最终金额为368.10。",\n      '
    '"reason": "指代对象明确，仅针对计算结果，无偏离。'
    '"\n    },\n    {\n      "requirem'
    'ent_id": "precision",\n      "v'
    'erdict": "met",\n      "quote":'
    ' "368.10",\n      "reason": "结果'
    '保留两位小数，与要求一致。"\n    }\n  ]\n}'
)


async def test_actual_precision_diagnostic_remains_a_noop_not_a_claimed_improvement(monkeypatch):
    from app.rag.response_review import checked_general_review, parse_general_review

    draft = "最终金额为368.10。"
    task = make_task_contract("original request")
    assert checked_general_review(ACTUAL_PRECISION_REVIEW, draft,
                                  contract=task, strict=True) == draft
    decision = parse_general_review(ACTUAL_PRECISION_REVIEW, contract=task,
                                     strict=True, draft_text=draft)
    assert decision.issues == ("constraint_missed", "precision")

    async def reviewer(_draft, _task):
        return ACTUAL_PRECISION_REVIEW

    hooks, state, _run, journal, calls, _ = agent_fixture(monkeypatch, reviewer)
    state["decision"]["answer"] = {"text": draft}
    assert await hooks.finish(state) == {"text": draft}
    assert journal[0].data["changed"] is False
    assert journal[0].data["accepted"] is False
    assert await hooks.finish(state) == {"text": draft} and len(calls) == 1


def test_unrecognized_precision_labels_do_not_expand_the_finite_review_vocabulary():
    from app.rag.response_review import ResponseReviewError, checked_general_review

    data = json.loads(ACTUAL_PRECISION_REVIEW)
    data["issues"][1] = "precision_unknown"
    with pytest.raises(ResponseReviewError):
        checked_general_review(data, "最终金额为368.10。",
                               contract=make_task_contract("original request"), strict=True)
