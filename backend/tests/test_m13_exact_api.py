from uuid import uuid4

import pytest
from sqlalchemy import select
from test_ingestion_lifecycle import FakeAdapter, application, finished, new_kb, upload
from test_postgres_local import migrate

from app.models import ConversationMessage, Document
from app.rag.answer_adapter import AnswerError
from app.rag.query_adapter import RetrievedChunk

pytest_plugins = ["test_postgres_local"]
pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def no_provider_access(monkeypatch):
    async def fake_start(_runtime, _assert_owned):
        return None
    monkeypatch.setattr("app.rag.runtime.RagRuntime.start", fake_start)


@pytest.mark.asyncio
async def test_confirmed_attributes_use_exact_values_and_original_block(schema_database, tmp_path):
    database, settings = schema_database
    await migrate(database)
    async with application(database, settings, tmp_path / "sources", FakeAdapter()) as (api, app):
        kb = await new_kb(api)
        first = await finished(api, await upload(api, kb, data=b"A001 limit is 42 C.\n"))
        second = await finished(api, await upload(api, kb, data=b"A0010 limit is 58 C.\n"))
        first_id, second_id = first["document_ids"][0], second["document_ids"][0]
        for doc, code, model in [(first_id, "A001", "X100"),
                                  (second_id, "A0010", "X100 Pro")]:
            response = await api.patch(f"/api/documents/{doc}/attributes", json={
                "doc_code": code, "model_code": model, "edition": "2025",
            })
            assert response.status_code == 200
        chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
        app.state.answer_enabled = True

        class NoSemantic:
            async def retrieve(self, *_args):
                raise AssertionError("Exact lookup must not use Top-K retrieval")

        class Quote:
            async def answer(self, _question, evidence):
                assert len(evidence) == 1
                return ('{"status":"answered","text":"A001 limit is 42 C.",'
                        '"evidence_ids":["E1"]}')

        app.state.query_adapter = NoSemantic()
        app.state.answer_adapter = Quote()
        response = await api.post(f"/api/conversations/{chat}/messages", json={
            "client_message_id": str(uuid4()), "text": "What is the X100 limit?",
            "mode": "exact", "exact": {"model_code": "X100", "phrase": "A001"},
        })
        assert response.status_code == 200, response.json()
        assert response.json()["status"] == "answered"
        assert response.json()["citations"][0]["document_id"] == first_id
        assert response.json()["citations"][0]["locator"]["line_start"] == 1
        assert response.json()["citations"][0]["excerpt"] == "A001 limit is 42 C."
        no_match = await api.post(f"/api/conversations/{chat}/messages", json={
            "client_message_id": str(uuid4()), "text": "Find A001 in X100 Pro",
            "mode": "exact", "exact": {"model_code": "X100 Pro", "phrase": "A001"},
        })
        assert no_match.status_code == 200
        assert no_match.json()["status"] == "insufficient_evidence"
        assert no_match.json()["citations"] == []
        changed = await api.patch(f"/api/documents/{second_id}/attributes", json={
            "doc_code": "A0010", "model_code": "X100", "edition": "2025",
        })
        assert changed.status_code == 200
        ambiguous = await api.post(f"/api/conversations/{chat}/messages", json={
            "client_message_id": str(uuid4()), "text": "Which X100 document?",
            "mode": "exact", "exact": {"model_code": "X100"},
        })
        assert ambiguous.status_code == 200
        assert ambiguous.json()["status"] == "needs_clarification"
        assert ambiguous.json()["citations"] == []


@pytest.mark.asyncio
async def test_auto_route_uses_confirmed_literal_and_reuses_route_on_retry(
    schema_database, tmp_path,
):
    database, settings = schema_database
    await migrate(database)
    async with application(database, settings, tmp_path / "sources", FakeAdapter()) as (api, app):
        kb = await new_kb(api)
        first = await finished(api, await upload(api, kb, data=b"X100 limit is 42 C.\n"))
        second = await finished(api, await upload(api, kb, data=b"X100 Pro limit is 58 C.\n"))
        first_id, second_id = first["document_ids"][0], second["document_ids"][0]
        for document_id, model in [(first_id, "X100"), (second_id, "X100 Pro")]:
            response = await api.patch(f"/api/documents/{document_id}/attributes", json={
                "doc_code": None, "model_code": model, "edition": None,
            })
            assert response.status_code == 200
        chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
        app.state.answer_enabled = True

        class NoSemantic:
            async def retrieve(self, *_args):
                raise AssertionError("Automatic exact routing must not use Top-K")

        class Router:
            routes = 0
            answers = 0

            async def route_query(self, question, candidates):
                self.routes += 1
                assert question == "查 X100 Pro 的上限"
                assert [(item["field"], item["value"]) for item in candidates] == [
                    ("model_code", "X100 Pro"),
                ]
                return '{"mode":"exact","candidate_ids":["C1"]}'

            async def answer(self, _question, evidence):
                self.answers += 1
                assert evidence[0]["text"] == "X100 Pro limit is 58 C."
                if self.answers == 1:
                    raise AnswerError("answer_unavailable")
                return ('{"status":"answered","text":"X100 Pro limit is 58 C.",'
                        '"evidence_ids":["E1"]}')

        router = Router()
        app.state.query_adapter = NoSemantic()
        app.state.answer_adapter = router
        key = str(uuid4())
        body = {"client_message_id": key, "text": "查 X100 Pro 的上限", "mode": "auto"}
        failed = await api.post(f"/api/conversations/{chat}/messages", json=body)
        assert failed.status_code == 200, failed.json()
        assert failed.json()["status"] == "failed"
        async with database.sessions() as session:
            message = await session.scalar(select(ConversationMessage).where(
                ConversationMessage.client_message_id == key,
            ))
            assert message.query_filter == {"mode": "exact", "filters": {
                "model_code": "X100 Pro",
            }}
        replay = await api.post(f"/api/conversations/{chat}/messages", json=body)
        assert replay.json()["message_id"] == failed.json()["message_id"]
        retry = await api.post(
            f"/api/conversations/{chat}/messages/{failed.json()['message_id']}/retry",
            json={"attempt_id": str(uuid4())},
        )
        assert retry.status_code == 200, retry.json()
        assert retry.json()["status"] == "answered"
        assert retry.json()["citations"][0]["document_id"] == second_id
        assert router.routes == 1

        class Unsupported:
            async def route_query(self, question, candidates):
                assert question == "一共有多少订单？"
                assert candidates == []
                return '{"mode":"unsupported"}'

            async def answer(self, *_args):
                raise AssertionError("Unsupported business queries must not generate an answer")

        app.state.answer_adapter = Unsupported()
        unsupported = await api.post(f"/api/conversations/{chat}/messages", json={
            "client_message_id": str(uuid4()), "text": "一共有多少订单？", "mode": "auto",
        })
        assert unsupported.status_code == 200, unsupported.json()
        assert unsupported.json()["status"] == "needs_clarification"
        assert unsupported.json()["route"] == "unsupported"
        assert unsupported.json()["citations"] == []
        assert "全集统计" in unsupported.json()["text"]

        async with database.sessions() as session:
            source_key = (await session.get(Document, second_id)).source_key

        class Semantic:
            async def route_query(self, question, candidates):
                assert question == "解释 X100 Pro 的上限"
                assert candidates == [{"id": "C1", "field": "model_code", "value": "X100 Pro"}]
                return '{"mode":"semantic"}'

            async def answer(self, _question, evidence):
                assert evidence[0]["text"] == "X100 Pro limit is 58 C."
                return ('{"status":"answered","text":"X100 Pro limit is 58 C.",'
                        '"evidence_ids":["E1"]}')

        class SemanticRetriever:
            async def retrieve(self, _kb, _workspace, _question, sources):
                assert source_key in sources
                return [RetrievedChunk("synthetic", source_key, "X100 Pro limit is 58 C.")]

        app.state.query_adapter = SemanticRetriever()
        app.state.answer_adapter = Semantic()
        semantic = await api.post(f"/api/conversations/{chat}/messages", json={
            "client_message_id": str(uuid4()), "text": "解释 X100 Pro 的上限", "mode": "auto",
        })
        assert semantic.status_code == 200, semantic.json()
        assert semantic.json()["status"] == "answered"
        assert semantic.json()["citations"][0]["document_id"] == second_id


@pytest.mark.asyncio
async def test_auto_literal_route_finds_order_code_only_in_exact_source(schema_database, tmp_path):
    database, settings = schema_database
    await migrate(database)
    async with application(database, settings, tmp_path / "sources", FakeAdapter()) as (api, app):
        kb = await new_kb(api)
        first = await finished(api, await upload(
            api, kb, data="订单 ORD-001 金额 42 元\n".encode(),
        ))
        await finished(api, await upload(api, kb, data="订单 ORD-0010 金额 58 元\n".encode()))
        chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
        app.state.answer_enabled = True

        class NoSemantic:
            async def retrieve(self, *_args):
                raise AssertionError("Literal lookup must not use Top-K retrieval")

        class Router:
            async def route_query(self, question, candidates):
                assert question == "查订单 ORD-001 的金额"
                assert candidates == []
                return '{"mode":"literal","phrase":"ORD-001"}'

            async def answer(self, _question, evidence):
                assert len(evidence) == 1
                assert evidence[0]["text"] == "订单 ORD-001 金额 42 元"
                return ('{"status":"answered","text":"订单 ORD-001 金额 42 元",'
                        '"evidence_ids":["E1"]}')

        app.state.query_adapter = NoSemantic()
        app.state.answer_adapter = Router()
        result = await api.post(f"/api/conversations/{chat}/messages", json={
            "client_message_id": str(uuid4()), "text": "查订单 ORD-001 的金额", "mode": "auto",
        })
        assert result.status_code == 200, result.json()
        assert result.json()["status"] == "answered"
        assert result.json()["route"] == "literal"
        assert result.json()["citations"][0]["document_id"] == first["document_ids"][0]
        assert result.json()["citations"][0]["locator"]["line_start"] == 1

        class NoMatch:
            async def route_query(self, _question, _candidates):
                return '{"mode":"literal","phrase":"ORD-999"}'

            async def answer(self, *_args):
                raise AssertionError("Missing literal must not reach answer generation")

        app.state.answer_adapter = NoMatch()
        absent = await api.post(f"/api/conversations/{chat}/messages", json={
            "client_message_id": str(uuid4()), "text": "查订单 ORD-999 的金额", "mode": "auto",
        })
        assert absent.status_code == 200, absent.json()
        assert absent.json()["status"] == "insufficient_evidence"
        assert absent.json()["citations"] == []

        await finished(api, await upload(api, kb, data="归档 ORD-001 金额 99 元\n".encode()))
        app.state.answer_adapter = Router()
        ambiguous = await api.post(f"/api/conversations/{chat}/messages", json={
            "client_message_id": str(uuid4()), "text": "查订单 ORD-001 的金额", "mode": "auto",
        })
        assert ambiguous.status_code == 200, ambiguous.json()
        assert ambiguous.json()["status"] == "needs_clarification"
        assert ambiguous.json()["citations"] == []
        assert "多个资料" in ambiguous.json()["text"]
