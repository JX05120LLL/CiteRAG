"""Text chunks spanning paragraphs still need exact, checkable line citations."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.rag.query_adapter import LightRAGQueryAdapter, QueryError
from app.rag.source_mapping import locate_chunk


def _block(original: str, text: str, locator: dict):
    start = original.index(text)
    return SimpleNamespace(text=text, start=start, end=start + len(text), locator=locator)


def test_unique_chunk_across_blank_line_blocks_has_source_line_range():
    original = "# CDC\n\nSource: public PDF\n\nQuitting smoking can add up to 10 years."
    blocks = [
        _block(original, "# CDC", {"kind": "lines", "line_start": 1, "line_end": 1}),
        _block(original, "Source: public PDF", {
            "kind": "lines", "line_start": 3, "line_end": 3,
        }),
        _block(original, "Quitting smoking can add up to 10 years.", {
            "kind": "lines", "line_start": 5, "line_end": 5,
        }),
    ]
    located = locate_chunk(original, original, blocks)
    assert located is not None
    assert located.quote == original
    assert located.locator == {"kind": "lines", "line_start": 1, "line_end": 5}


def test_cross_block_chunk_rejects_unmapped_nonwhitespace_text():
    original = "first\n\nmissing source\n\nlast"
    blocks = [
        _block(original, "first", {"kind": "lines", "line_start": 1, "line_end": 1}),
        _block(original, "last", {"kind": "lines", "line_start": 5, "line_end": 5}),
    ]
    assert locate_chunk(original, original, blocks) is None


def test_cross_page_chunk_still_needs_page_specific_location():
    original = "first page\n\nsecond page"
    blocks = [
        _block(original, "first page", {"kind": "page", "page": 1}),
        _block(original, "second page", {"kind": "page", "page": 2}),
    ]
    assert locate_chunk(original, original, blocks) is None


def test_unicode_offsets_and_line_numbers_follow_original_text():
    original = "标题🙂\n\n依据：公开文件\n\n有效期十年"
    blocks = [
        _block(original, "标题🙂", {"kind": "lines", "line_start": 1, "line_end": 1}),
        _block(original, "依据：公开文件", {
            "kind": "lines", "line_start": 3, "line_end": 3,
        }),
        _block(original, "有效期十年", {"kind": "lines", "line_start": 5, "line_end": 5}),
    ]
    located = locate_chunk(original, original, blocks)
    assert located is not None
    assert original[located.start:located.end] == original
    assert located.locator == {"kind": "lines", "line_start": 1, "line_end": 5}


def test_repeated_text_and_other_file_cannot_borrow_a_location():
    original = "same\n\nsame"
    blocks = [
        SimpleNamespace(text="same", start=0, end=4,
                        locator={"kind": "lines", "line_start": 1, "line_end": 1}),
        SimpleNamespace(text="same", start=6, end=10,
                        locator={"kind": "lines", "line_start": 3, "line_end": 3}),
    ]
    assert locate_chunk("same", original, blocks) is None
    assert locate_chunk("a fact from another file", original, blocks) is None


def test_cross_block_rejects_incorrect_line_metadata():
    original = "heading\n\nfact"
    blocks = [
        _block(original, "heading", {"kind": "lines", "line_start": 1, "line_end": 1}),
        _block(original, "fact", {"kind": "lines", "line_start": 99, "line_end": 99}),
    ]
    assert locate_chunk(original, original, blocks) is None


def test_cross_block_rejects_boolean_line_number():
    original = "heading\n\nfact"
    blocks = [
        _block(original, "heading", {"kind": "lines", "line_start": True,
                                     "line_end": 1}),
        _block(original, "fact", {"kind": "lines", "line_start": 3,
                                  "line_end": 3}),
    ]
    assert locate_chunk(original, original, blocks) is None


@pytest.mark.asyncio
async def test_other_knowledge_base_source_cannot_gain_a_line_citation():
    class OtherBaseRuntime:
        async def query_data(self, _kb_id, _workspace, _question):
            return {"status": "success", "data": {
                "chunks": [{"chunk_id": "other_chunk", "file_path": "other_source",
                            "content": "other private text", "reference_id": "R1"}],
                "references": [{"reference_id": "R1", "file_path": "other_source"}],
            }}, 1

        async def get_for_workspace(self, _kb_id, _workspace):
            raise AssertionError("foreign source must fail before opening its workspace")

    with pytest.raises(QueryError):
        await LightRAGQueryAdapter(OtherBaseRuntime()).retrieve(
            uuid4(), "selected_workspace", "question", {"selected_source": "selected_doc"},
        )
