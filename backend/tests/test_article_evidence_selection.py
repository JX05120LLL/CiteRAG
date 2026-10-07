"""An oversized verified Markdown chunk may yield one complete, located article."""

# Pytest fixture injection looks like a redefinition to Ruff.
# ruff: noqa: F811

import json
from types import SimpleNamespace

import pytest
from test_ingestion_lifecycle import finished, new_kb, upload
from test_m13_answer_api import m13_environment  # noqa: F401

from app.rag import source_mapping
from app.rag.answer_adapter import AnswerError, checked_answer
from app.rag.query_adapter import RetrievedChunk
from app.services.token_budget import EVIDENCE_TOKENS, estimate_json_tokens

pytest_plugins = ["test_postgres_local"]


def _document(article: str, *, tail: bool = False):
    leading = "前文" * (1000 if tail else 700)
    trailing = "后文" * (700 if tail else 1000)
    paragraphs = ["# 旅行社条例", leading, article, trailing]
    original = "\n\n".join(paragraphs)
    blocks = []
    for text in paragraphs:
        start = original.index(text)
        line = original.count("\n", 0, start) + 1
        blocks.append(SimpleNamespace(text=text, start=start, end=start + len(text),
                                      locator={"kind": "lines", "line_start": line,
                                               "line_end": line + text.count("\n")}))
    located = source_mapping.locate_chunk(original, original, blocks)
    assert located is not None
    assert estimate_json_tokens([{"id": "E1", "text": located.quote}]) > EVIDENCE_TOKENS
    return original, blocks, located


@pytest.mark.parametrize("tail", [False, True])
def test_select_complete_article_from_middle_or_tail_of_verified_chunk(tail):
    article = "第二十八条 购物安排应载明购物次数、停留时间和购物场所的名称。"
    original, blocks, located = _document(article, tail=tail)
    chosen = source_mapping.select_verified_article_block(
        "旅行社条例第二十八条对购物安排要求载明哪些细节？",
        located, original, blocks,
    )
    assert chosen is not None
    assert chosen.quote == article
    assert chosen.locator == blocks[2].locator
    assert original[chosen.start:chosen.end] == chosen.quote
    assert estimate_json_tokens([{"id": "E1", "text": chosen.quote}]) < EVIDENCE_TOKENS


def test_duplicate_article_number_does_not_choose_an_ambiguous_location():
    first = "第二十八条 第一版本。"
    second = "第二十八条 第二版本。"
    original = "\n\n".join(("# 条例", first, second))
    blocks = []
    for text in ("# 条例", first, second):
        start = original.index(text)
        line = original.count("\n", 0, start) + 1
        blocks.append(SimpleNamespace(text=text, start=start, end=start + len(text),
                                      locator={"kind": "lines", "line_start": line,
                                               "line_end": line}))
    located = source_mapping.locate_chunk(original, original, blocks)
    assert located is not None
    assert source_mapping.select_verified_article_block(
        "第二十八条是什么？", located, original, blocks,
    ) is None


def test_duplicate_article_number_within_one_paragraph_block_fails_closed():
    original, blocks, located = _document(
        "第二十八条 第一版本。\n第二十八条 第二版本。",
    )
    assert source_mapping.select_verified_article_block(
        "第二十八条是什么？", located, original, blocks,
    ) is None


def test_reference_to_article_in_other_article_is_not_its_heading():
    original, blocks, located = _document("第二十九条 具体要求见第二十八条。")
    assert source_mapping.select_verified_article_block(
        "第二十八条是什么？", located, original, blocks,
    ) is None


def test_foreign_block_and_unquoted_answer_cannot_gain_a_source_location():
    original, blocks, located = _document("第二十八条 只规定行程。")
    foreign = SimpleNamespace(text="第二十八条 购物次数为三次。", start=blocks[2].start,
                              end=blocks[2].end, locator=blocks[2].locator)
    assert source_mapping.select_verified_article_block(
        "第二十八条购物次数是多少？", located, original, [foreign],
    ) is None
    chosen = source_mapping.select_verified_article_block(
        "第二十八条购物次数是多少？", located, original, blocks,
    )
    assert chosen is not None and chosen.quote == "第二十八条 只规定行程。"
    raw = json.dumps({"status": "answered", "text": "购物次数为三次。", "evidence_ids": ["E1"]})
    with pytest.raises(AnswerError, match="answer_source_mismatch"):
        checked_answer(raw, {"E1": chosen.quote})


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_long_verified_chunk_answers_with_article_line_citation(m13_environment):
    api, _db, app, _adapter = m13_environment
    article = "第二十八条 购物安排应载明购物次数、停留时间和购物场所的名称。"
    original, blocks, _located = _document(article)
    kb = await new_kb(api)
    job = await finished(api, await upload(api, kb, data=original.encode("utf-8")))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    source_key = "source_" + job["document_ids"][0].replace("-", "")
    app.state.answer_enabled = True

    class Retrieval:
        async def retrieve(self, *_args):
            return [RetrievedChunk("synthetic_chunk", source_key, original)]

    class Answer:
        async def answer(self, _question, evidence):
            assert evidence == [{"id": "E1", "text": article}]
            return json.dumps({"status": "answered", "text": "购物次数、停留时间和购物场所的名称",
                               "evidence_ids": ["E1"]}, ensure_ascii=False)

    app.state.query_adapter = Retrieval()
    app.state.answer_adapter = Answer()
    response = await api.post(f"/api/conversations/{chat}/messages", json={
        "client_message_id": "f2f90877-5e11-4543-a90b-4f2cfe7c3d44",
        "text": "旅行社条例第二十八条对购物安排要求载明哪些细节？", "mode": "semantic",
    })
    assert response.status_code == 200
    view = response.json()
    assert view["status"] == "answered", view
    assert view["citations"][0]["locator"] == blocks[2].locator
    assert view["citations"][0]["excerpt"] == article
