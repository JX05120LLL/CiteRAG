"""Existing source mapping and support verifier stay authoritative for Agent answers."""

import json
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_api import SyntheticAnswerer, settled
from test_ingestion_lifecycle import finished, new_kb, upload
from test_m13_answer_api import Query, m13_environment, no_provider_access  # noqa: F401

from app.agent.runner import AgentRunner
from app.models import KnowledgeBase
from app.rag.query_adapter import RetrievedChunk

pytest_plugins = ["test_postgres_local"]
pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("supported", [True, False])
async def test_agent_knowledge_finish_still_requires_current_source_and_fact_support(
    m13_environment,  # noqa: F811
    supported,
):  # noqa: F811
    api, database, app, _ = m13_environment
    kb = await new_kb(api)
    job = await finished(api, await upload(api, kb, data=b"The safe limit is 42 C.\n"))
    assert job["status"] == "succeeded"
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    app.state.answer_enabled = app.state.agent_enabled = True
    app.state.query_adapter = Query(
        [
            RetrievedChunk(
                "chunk_a",
                "source_" + job["document_ids"][0].replace("-", ""),
                "The safe limit is 42 C.",
            )
        ]
    )

    class Answer(SyntheticAnswerer):
        async def route_with_context(self, *args):
            return '{"mode":"semantic"}'

        async def verify_answer(self, text, evidence):
            assert evidence and text == "The safe temperature threshold is 42 C."
            return json.dumps({"supported": supported})

    app.state.answer_adapter = Answer(
        [
            {
                "action": "finish",
                "answer": {
                    "status": "answered",
                    "text": "The safe temperature threshold is 42 C.",
                    "evidence_ids": ["E1"],
                    "support": [{"evidence_id": "E1", "quote": "The safe limit is 42 C."}],
                },
            }
        ]
    )
    app.state.agent_runtime = AgentRunner(app, InMemorySaver())
    try:
        path = f"/api/conversations/{chat}/agent-runs"
        accepted = await api.post(
            path, json={"client_message_id": str(uuid4()), "text": "What is the limit?"}
        )
        assert accepted.status_code == 202, accepted.text
        result = await settled(api, path, accepted.json()["id"])
        messages = (await api.get(f"/api/conversations/{chat}/messages")).json()["items"]
        if supported:
            assert result["status"] == "completed"
            assert messages[0]["citations"][0]["locator"]["line_start"] == 1
            async with database.sessions() as session:
                library = await session.get(KnowledgeBase, UUID(kb))
                library.revision += 1
                library.hide_history_before_revision = library.revision
                await session.commit()
            replay = await api.get(f"{path}/{result['id']}/events")
            saved = next(
                line
                for line in replay.text.splitlines()
                if line.startswith("data: ") and '"answer"' in line
            )
            assert json.loads(saved[6:])["answer"]["citations"] == []
        else:
            assert result["status"] == "failed"
            assert result["error_code"] == "answer_unsupported_claims"
            assert messages[0]["citations"] == []
    finally:
        await app.state.agent_runtime.close()
