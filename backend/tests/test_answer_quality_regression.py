"""Public synthetic cases: service/source contracts, not real model quality.

The JSON corpus is also suitable for a separately authorized manual evaluation.
Database rows, route decisions, retrieval and model/verifier outputs are explicit
test doubles. Actual AnswerService selection, source mapping and output gates run.
No database, provider client, network connection or credentials are used.
"""

import json
from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.ingestion.parsing import ParsedBlock
from app.rag.answer_adapter import AnswerError
from app.rag.query_adapter import QueryError, RetrievedChunk
from app.services.answers import AnswerService

EVALUATION = json.loads(
    (Path(__file__).parents[1] / "evaluation" / "knowledge_quality.json").read_text(
        encoding="utf-8",
    ),
)
CORPORA = EVALUATION["corpora"]
Metadata = namedtuple("Metadata", "doc_code model_code edition filename")


def source_rows(corpus):
    """Supply synthetic rows with exact original offsets, without a database."""
    data = corpus["documents"][0]
    document = SimpleNamespace(
        id=uuid4(), source_key="public-synthetic-source", engine_doc_id="synthetic-engine-doc",
        parsed_text=data["text"], filename=data["filename"],
    )
    blocks, offset = [], 0
    for ordinal, paragraph in enumerate(data["text"].split("\n\n")):
        blocks.append(ParsedBlock(
            text=paragraph,
            locator={"kind": "lines", "line_start": ordinal * 2 + 1,
                     "line_end": ordinal * 2 + 1},
            start=offset, end=offset + len(paragraph),
        ))
        offset += len(paragraph) + 2
    return document, blocks


def summary_output(corpus, blocks, *, text=None):
    return json.dumps({
        "status": "answered", "text": text or corpus["summary_answer"],
        "evidence_ids": ["E1", "E2"],
        "support": [{"evidence_id": f"E{index}", "quote": block.text}
                    for index, block in enumerate(blocks, 1)],
    }, ensure_ascii=False)


@pytest.mark.parametrize("corpus", CORPORA, ids=lambda corpus: corpus["id"])
async def test_confirmed_code_routes_exactly_and_preserves_original_conditions(corpus):
    document, blocks = source_rows(corpus)
    kb = SimpleNamespace(id=uuid4(), name=corpus["knowledge_base"])
    session = SimpleNamespace(
        execute=AsyncMock(return_value=[
            Metadata(data["doc_code"], None, None, data["filename"])
            for data in corpus["documents"]
        ]),
        scalars=AsyncMock(side_effect=[[document], blocks]), commit=AsyncMock(),
    )
    router = SimpleNamespace(route_with_context=AsyncMock(return_value=(
        '{"mode":"exact","candidate_ids":["C1"]}'
    )))
    context = {"summary": "仅用于解指代，不是本轮证据", "turns": []}
    service = AnswerService(session)
    route = await service._route_auto(kb, corpus["exact_question"], router, context)
    assert route == {"mode": "exact", "filters": {
        "doc_code": corpus["documents"][0]["doc_code"],
    }}
    question, candidates, passed_context, documents = router.route_with_context.await_args.args
    assert question == corpus["exact_question"]
    assert candidates == [{"id": "C1", "field": "doc_code",
                           "value": corpus["documents"][0]["doc_code"]}]
    assert passed_context["knowledge_base"]["name"] == corpus["knowledge_base"]
    assert passed_context["knowledge_base"]["ready_document_count"] == 2
    assert {item["filename"] for item in documents} == {
        data["filename"] for data in corpus["documents"]
    }
    model = SimpleNamespace(answer_with_context=AsyncMock(return_value=json.dumps({
        "status": "answered", "text": corpus["exact_quote"], "evidence_ids": ["E1"],
    }, ensure_ascii=False)))
    retriever = SimpleNamespace(retrieve=AsyncMock(side_effect=AssertionError(
        "Exact selection must not fall back to semantic retrieval",
    )))
    status, text, citations = await service._resolve(
        kb.id, "synthetic-workspace", question, retriever, model, "auto", route, context,
    )
    assert (status, text) == ("answered", corpus["exact_quote"])
    assert citations == [{
        "evidence_id": "E1", "document_id": str(document.id),
        "filename": document.filename, "locator": blocks[0].locator,
        "excerpt": corpus["exact_quote"],
    }]
    retriever.retrieve.assert_not_awaited()


@pytest.mark.parametrize("corpus", CORPORA, ids=lambda corpus: corpus["id"])
@pytest.mark.parametrize("followup", [False, True], ids=["summary", "followup"])
async def test_semantic_summary_and_followup_retrieve_fresh_evidence(corpus, followup):
    document, blocks = source_rows(corpus)
    session = SimpleNamespace(
        scalars=AsyncMock(side_effect=[[document], blocks, blocks]), commit=AsyncMock(),
    )
    kb = uuid4()
    question = corpus["followup_question"] if followup else corpus["summary_question"]
    retrieval_question = corpus["followup_query"] if followup else question
    context = {"summary": "用户希望简洁", "turns": [{
        "user": corpus["summary_question"], "assistant": "历史中的错误数字 999 不可作为证据",
        "answer_kind": "knowledge",
    }]}
    retriever = SimpleNamespace(retrieve=AsyncMock(return_value=[
        RetrievedChunk(f"public-chunk-{index}", document.source_key, block.text)
        for index, block in enumerate(blocks)
    ]))
    model = SimpleNamespace(
        answer_with_context=AsyncMock(return_value=summary_output(corpus, blocks)),
        verify_answer=AsyncMock(return_value='{"supported":true}'),
    )
    result = await AnswerService(session)._resolve(
        kb, "synthetic-workspace", question, retriever, model, "auto",
        {"mode": "semantic", "query": retrieval_question}, context,
    )
    assert result[:2] == ("answered", corpus["summary_answer"])
    retriever.retrieve.assert_awaited_once_with(kb, "synthetic-workspace", retrieval_question, {
        document.source_key: document.engine_doc_id,
    })
    actual_question, evidence, actual_context = model.answer_with_context.await_args.args
    assert actual_question == question and actual_context == context
    assert evidence == [{"id": f"E{index}", "text": block.text}
                        for index, block in enumerate(blocks, 1)]
    model.verify_answer.assert_awaited_once_with(corpus["summary_answer"], evidence)
    assert [citation["locator"] for citation in result[2]] == [block.locator for block in blocks]
    assert all(citation["document_id"] == str(document.id) for citation in result[2])
    assert "999" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize("corpus", CORPORA, ids=lambda corpus: corpus["id"])
async def test_empty_retrieval_does_not_fall_back_to_ordinary_model(corpus):
    document, _ = source_rows(corpus)
    session = SimpleNamespace(scalars=AsyncMock(return_value=[document]), commit=AsyncMock())
    retriever = SimpleNamespace(retrieve=AsyncMock(return_value=[]))
    model = SimpleNamespace(answer=AsyncMock(), general_answer=AsyncMock())
    result = await AnswerService(session)._resolve(
        uuid4(), "synthetic-workspace", corpus["unsupported_question"], retriever, model,
        "auto", {"mode": "semantic"}, {},
    )
    assert result == ("insufficient_evidence", "", [])
    model.answer.assert_not_awaited()
    model.general_answer.assert_not_awaited()


@pytest.mark.parametrize("corpus", CORPORA, ids=lambda corpus: corpus["id"])
@pytest.mark.parametrize("corruption", ["altered-content", "foreign-source"])
async def test_retrieved_text_must_map_to_the_current_managed_original(corpus, corruption):
    document, blocks = source_rows(corpus)
    session = SimpleNamespace(
        scalars=AsyncMock(side_effect=[[document], blocks]), commit=AsyncMock(),
    )
    source = document.source_key if corruption == "altered-content" else "foreign-library-source"
    content = corpus["false_answer"] if corruption == "altered-content" else blocks[0].text
    retriever = SimpleNamespace(retrieve=AsyncMock(return_value=[
        RetrievedChunk("public-chunk", source, content),
    ]))
    model = SimpleNamespace(answer=AsyncMock(), general_answer=AsyncMock())
    call = AnswerService(session)._resolve(
        uuid4(), "synthetic-workspace", corpus["summary_question"], retriever, model,
        "auto", {"mode": "semantic"}, {},
    )
    if corruption == "foreign-source":
        with pytest.raises(QueryError, match="^retrieval_failed$"):
            await call
    else:
        assert await call == ("insufficient_evidence", "", [])
    model.answer.assert_not_awaited()
    model.general_answer.assert_not_awaited()


@pytest.mark.parametrize("corpus", CORPORA, ids=lambda corpus: corpus["id"])
async def test_correct_quote_does_not_authorize_a_changed_number(corpus):
    _, blocks = source_rows(corpus)
    evidence = [{"id": f"E{index}", "text": block.text}
                for index, block in enumerate(blocks, 1)]
    model = SimpleNamespace(verify_answer=AsyncMock(return_value='{"supported":false}'))
    with pytest.raises(AnswerError, match="^answer_unsupported_claims$"):
        await AnswerService._checked_result(model, summary_output(
            corpus, blocks, text=corpus["false_answer"],
        ), evidence, [])
    model.verify_answer.assert_awaited_once_with(corpus["false_answer"], evidence)


@pytest.mark.parametrize("verdict", ["{}", '{"supported":1}', '{"supported":"true"}',
                                    '{"supported":true,"extra":"untrusted"}', "not-json"])
async def test_uncertain_or_malformed_fact_verdict_never_publishes_summary(verdict):
    corpus = CORPORA[0]
    _, blocks = source_rows(corpus)
    evidence = [{"id": f"E{index}", "text": block.text}
                for index, block in enumerate(blocks, 1)]
    model = SimpleNamespace(verify_answer=AsyncMock(return_value=verdict))
    with pytest.raises(AnswerError, match="^answer_verification_unavailable$"):
        await AnswerService._checked_result(model, summary_output(corpus, blocks), evidence, [])


@pytest.mark.parametrize("corpus", CORPORA, ids=lambda corpus: corpus["id"])
async def test_conflict_outcome_cannot_publish_one_selected_value(corpus):
    evidence = [{"id": "E1", "text": corpus["exact_quote"]},
                {"id": "E2", "text": corpus["conflicting_quote"]}]
    model = SimpleNamespace(verify_answer=AsyncMock())
    result = await AnswerService._checked_result(model, json.dumps({
        "status": "conflicting_evidence", "text": "", "evidence_ids": [], "support": [],
    }), evidence, [{"evidence_id": "E1", "excerpt": corpus["exact_quote"]}])
    assert result == ("conflicting_evidence", "", [])
    model.verify_answer.assert_not_awaited()


@pytest.mark.parametrize("injection", ["unknown-reference", "wrong-quote", "invented-url"])
async def test_unverifiable_source_metadata_never_reaches_fact_verifier(injection):
    corpus = CORPORA[0]
    _, blocks = source_rows(corpus)
    data = json.loads(summary_output(corpus, blocks))
    expected_error = "answer_source_mismatch"
    if injection == "unknown-reference":
        data["evidence_ids"] = ["E9"]
        expected_error = "answer_reference_invalid"
    elif injection == "wrong-quote":
        data["support"][0]["quote"] = "EQ-17 可在通电时更换滤芯。"
    else:
        data["text"] += " https://unverified.invalid/report"
    model = SimpleNamespace(verify_answer=AsyncMock(return_value='{"supported":true}'))
    with pytest.raises(AnswerError, match=f"^{expected_error}$"):
        await AnswerService._checked_result(model, json.dumps(data), [
            {"id": f"E{index}", "text": block.text}
            for index, block in enumerate(blocks, 1)
        ], [])
    model.verify_answer.assert_not_awaited()


@pytest.mark.parametrize("raw", [
    '{"text":"你好","evidence_ids":["E1"]}',
    '{"text":"你好","citations":[{"filename":"fake.txt"}]}',
    '{"text":"请见 https://unverified.invalid/report"}',
])
async def test_ordinary_response_cannot_supply_fabricated_citations(raw):
    model = SimpleNamespace(general_answer=AsyncMock(return_value=raw))
    with pytest.raises(AnswerError, match="^answer_format_invalid$"):
        await AnswerService(None)._resolve(
            None, "ordinary", "你好", None, model, "auto", {"mode": "general"}, {},
        )


@pytest.mark.parametrize("question", ["你好", "什么是检索增强生成？", "能讲得更简短一点吗？"])
async def test_ordinary_response_uses_chat_context_without_knowledge_sources(question):
    context = {"summary": "用户偏好简短回答", "turns": [{
        "user": "什么是检索增强生成？", "assistant": "结合检索结果生成回答。",
        "answer_kind": "general",
    }]}
    model = SimpleNamespace(general_answer=AsyncMock(return_value='{"text":"公开合成回答。"}'))
    result = await AnswerService(None)._resolve(
        None, "ordinary", question, None, model, "auto", {"mode": "general"}, context,
    )
    assert result == ("answered", "公开合成回答。", [])
    model.general_answer.assert_awaited_once_with(question, context)
