"""Public synthetic safety tests: no provider keys, external writes or business data."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_agent_api import ready, settled
from test_postgres_local import environment  # noqa: F401

from app.models import AgentRun, KnowledgeBase, LocalProfile, ToolCall
from app.tools.gateway import ToolDefinition

pytest_plugins = ["test_postgres_local"]
pytestmark = pytest.mark.postgres


async def approval(api):
    app, chat, path = await ready(
        api,
        [
            {"action": "call_tool", "tool_id": "synthetic.write", "arguments": {}},
            {"action": "finish", "answer": {"text": "公开合成操作完成。"}},
        ],
    )
    calls = []

    async def write(_session, _context, _arguments):
        calls.append(True)
        return {"synthetic": True}

    app.state.tool_registry["synthetic.write"] = ToolDefinition(
        "synthetic.write",
        "合成操作",
        "any",
        True,
        "仅修改隔离测试中的内存",
        lambda x: x,
        write,
        effect="write",
    )
    response = await api.post(path, json={"client_message_id": str(uuid4()), "text": "合成审批"})
    assert response.status_code == 202, response.text
    run = response.json()["id"]
    return app, chat, path, await settled(api, path, run), calls


async def test_waiting_occupies_chat_and_policy_change_voids_approval(environment):  # noqa: F811
    api, _ = environment
    app, chat, path, run, calls = await approval(api)
    try:
        assert (await api.delete(f"/api/conversations/{chat}")).status_code == 409
        assert (
            await api.patch(f"/api/conversations/{chat}/archive", json={"archived": True})
        ).status_code == 409
        app.state.tool_registry["synthetic.write"] = replace(
            app.state.tool_registry["synthetic.write"], destination="changed-target"
        )
        response = await api.post(
            f"{path}/{run['id']}/resume",
            json={
                "request_id": str(uuid4()),
                "generation": run["generation"],
                "input": {"approve": True},
            },
        )
        assert response.status_code == 409 and not calls
    finally:
        await app.state.agent_runtime.close()


async def test_expired_wait_is_terminal_and_not_executed(environment):  # noqa: F811
    api, database = environment
    app, _, path, run, calls = await approval(api)
    try:
        async with database.sessions() as session:
            row = await session.get(AgentRun, UUID(run["id"]))
            row.wait_until = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()
        await app.state.agent_runtime.reconcile()
        assert (await api.get(f"{path}/{run['id']}")).json()["status"] == "expired"
        assert not calls
    finally:
        await app.state.agent_runtime.close()


async def test_cancelled_write_is_unknown_and_never_blindly_retried(environment):  # noqa: F811
    api, database = environment
    app, chat, path, run, _ = await approval(api)
    entered = asyncio.Event()

    async def uncertain(*args):
        entered.set()
        await asyncio.Event().wait()

    app.state.tool_registry["synthetic.write"] = replace(
        app.state.tool_registry["synthetic.write"], run=uncertain
    )
    try:
        approved = await api.post(
            f"{path}/{run['id']}/resume",
            json={
                "request_id": str(uuid4()),
                "generation": run["generation"],
                "input": {"approve": True},
            },
        )
        assert approved.status_code == 202, approved.text
        await asyncio.wait_for(entered.wait(), 2)
        await api.post(f"{path}/{run['id']}/cancel")
        async with database.sessions() as session:
            call = await session.scalar(select(ToolCall).where(ToolCall.run_id == UUID(run["id"])))
            assert call.status == "unknown" and call.error_code == "tool_result_unknown"
        retry = await api.post(
            f"/api/conversations/{chat}/messages/{run['message_id']}/retry",
            json={"attempt_id": str(uuid4())},
        )
        assert retry.status_code == 409, retry.text
    finally:
        await app.state.agent_runtime.close()


async def test_bound_revision_maintenance_aborts_waiting(environment):  # noqa: F811
    api, database = environment
    app, _, _ = await ready(api, [{"action": "request_input", "prompt": "补充范围", "fields": {}}])
    try:
        kb_id = (
            await api.post(
                "/api/knowledge-bases",
                json={"name": "公开合成库", "client_request_id": str(uuid4())},
            )
        ).json()["id"]
        async with database.sessions() as session:
            kb = await session.get(KnowledgeBase, UUID(kb_id))
            kb.status = "ready"
            await session.commit()
        chat = (await api.post("/api/conversations", json={"kb_id": kb_id})).json()["id"]
        path = f"/api/conversations/{chat}/agent-runs"
        run = (
            await api.post(path, json={"client_message_id": str(uuid4()), "text": "讨论资料"})
        ).json()
        assert (await settled(api, path, run["id"]))["status"] == "waiting_input"
        async with database.sessions() as session:
            kb = await session.get(KnowledgeBase, UUID(kb_id))
            kb.revision += 1
            kb.status = "maintaining"
            await session.commit()
        await app.state.agent_runtime.reconcile()
        result = (await api.get(f"{path}/{run['id']}")).json()
        assert result["status"] == "cancelled" and result["error_code"] == "kb_changed"
    finally:
        await app.state.agent_runtime.close()


async def test_voice_final_turn_uses_runner_and_explicit_control(environment):  # noqa: F811
    from app.voice.sessions import Binding
    from app.voice.turns import VoiceTurns

    api, database = environment
    app, chat, path = await ready(
        api,
        [
            {"action": "request_input", "prompt": "公开合成参数", "fields": {"minutes": "integer"}},
            {"action": "finish", "answer": {"text": "合成回答。"}},
        ],
    )
    voice = app.state.voice_runtime
    async with database.sessions() as session:
        owner = await session.scalar(select(LocalProfile.id))
    call = voice.registry.create(Binding(owner, UUID(chat), None, 0, "ordinary"), uuid4())
    played = []

    async def play(text, generation):
        played.append((text, generation))

    turns = VoiceTurns(voice.registry, call, voice.answer, play)
    try:
        await turns.submit(1, "合成语音最终转写")
        for _ in range(100):
            runs = (await api.get(path)).json()["items"]
            if runs and runs[0]["status"] == "waiting_input":
                break
            await asyncio.sleep(0.03)
        run = runs[0]
        assert run["voice_session_id"] == str(call.id) and not played
        body = {
            "request_id": str(uuid4()),
            "generation": run["generation"],
            "input": {"minutes": 10},
        }
        assert (await api.post(f"{path}/{run['id']}/resume", json=body)).status_code == 409
        body.update(voice_session_id=str(call.id), control_token=call.control)
        assert (await api.post(f"{path}/{run['id']}/resume", json=body)).status_code == 202
        await asyncio.wait_for(call.turn_task, 3)
        assert played == [("合成回答。", call.generation)]
        messages = (await api.get(f"/api/conversations/{chat}/messages")).json()["items"]
        assert len(messages) == 1 and messages[0]["route"] == "general"
    finally:
        await voice.registry.close_call(call, "test_finished")
        await app.state.agent_runtime.close()


async def test_lost_lease_cancels_actual_model_task_and_charges_budget(environment):  # noqa: F811
    api, database = environment
    app, _, path = await ready(api, [])
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def blocked(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    app.state.answer_adapter.agent_decision = blocked
    app.state.agent_runtime.heartbeat_seconds = 0.03
    try:
        run = (
            await api.post(
                path, json={"client_message_id": str(uuid4()), "text": "公开合成租约测试"}
            )
        ).json()
        await asyncio.wait_for(entered.wait(), 2)
        async with database.sessions() as session:
            row = await session.get(AgentRun, UUID(run["id"]))
            row.lease_until = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()
        await asyncio.wait_for(cancelled.wait(), 2)
        result = await settled(api, path, run["id"])
        assert result["status"] == "interrupted" and result["active_ms"] > 0
    finally:
        await app.state.agent_runtime.close()


@pytest.mark.parametrize("revocation", ["lease", "generation", "runner", "status"])
async def test_fence_rechecks_database_after_run_was_preloaded(environment, revocation):  # noqa: F811
    from app.agent.repository import fenced
    from app.services.errors import ServiceError

    api, database = environment
    app, _, _, run, _ = await approval(api)
    run_id = UUID(run["id"])
    try:
        async with database.sessions() as session:
            row = await session.get(AgentRun, run_id, with_for_update=True)
            row.status = "running"
            row.lease_until = datetime.now(UTC) + timedelta(seconds=30)
            row.runner_id = app.state.agent_runtime.id
            await session.commit()
        async with database.sessions() as reader:
            cached = await reader.get(AgentRun, run_id)
            generation, runner_id = cached.generation, cached.runner_id
            assert cached.status == "running" and cached.lease_until > datetime.now(UTC)
            async with database.sessions() as writer:
                changed = await writer.get(AgentRun, run_id, with_for_update=True)
                if revocation == "lease":
                    changed.lease_until = datetime.now(UTC) - timedelta(seconds=1)
                elif revocation == "generation":
                    changed.generation += 1
                elif revocation == "runner":
                    changed.runner_id = uuid4()
                else:
                    changed.status = "cancelled"
                await writer.commit()
            with pytest.raises(ServiceError) as rejected:
                await fenced(reader, run_id, generation, runner_id)
            assert rejected.value.code == "agent_interrupted"
    finally:
        await app.state.agent_runtime.close()


async def test_restart_charges_unjournaled_execution_interval(environment):  # noqa: F811
    from app.agent.repository import recover_running

    api, database = environment
    app, _, path, run, _ = await approval(api)
    try:
        # A restarted process has no live executor still unwinding the durable pause.
        await app.state.agent_runtime.close()
        async with database.sessions() as session:
            row = await session.get(AgentRun, UUID(run["id"]))
            row.status = "running"
            row.active_ms = 40_000
            row.runner_id = uuid4()
            row.lease_until = datetime.now(UTC) + timedelta(seconds=5)
            await session.commit()
        async with database.sessions() as session:
            await recover_running(session)
            await session.commit()
            row = await session.get(AgentRun, UUID(run["id"]))
            assert row.status == "interrupted" and row.active_ms == 60_000
            assert row.runner_id is None and row.lease_until is None
    finally:
        await app.state.agent_runtime.close()


async def test_image_agent_wait_expiry_and_retry_use_same_formal_message(environment):  # noqa: F811
    from test_m3_images_api import Observer, synthetic_png

    from app.models import ImageAttachment

    api, database = environment
    app, chat, path = await ready(
        api,
        [
            {"action": "request_input", "prompt": "公开合成补充", "fields": {}},
            {"action": "finish", "answer": {"text": "红色合成铭牌。"}},
        ],
    )
    app.state.image_observer = Observer()
    try:
        image = (
            await api.post(
                f"/api/conversations/{chat}/attachments",
                files={"file": ("public-synthetic.png", synthetic_png(), "image/png")},
            )
        ).json()
        accepted = await api.post(
            path,
            json={
                "client_message_id": str(uuid4()),
                "text": "描述合成图片",
                "image_ids": [image["id"]],
            },
        )
        assert accepted.status_code == 202, accepted.text
        run = accepted.json()
        waiting = await settled(api, path, run["id"])
        assert waiting["status"] == "waiting_input"
        assert "图片观察" in app.state.answer_adapter.inputs[0]["question"]
        async with database.sessions() as session:
            row = await session.get(ImageAttachment, UUID(image["id"]))
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()
        resumed = await api.post(
            f"{path}/{run['id']}/resume",
            json={
                "request_id": str(uuid4()),
                "generation": waiting["generation"],
                "input": {"detail": "继续"},
            },
        )
        assert resumed.status_code == 409
        await app.state.agent_runtime.reconcile()
        assert (await api.get(f"{path}/{run['id']}")).json()["status"] == "cancelled"
        messages = (await api.get(f"/api/conversations/{chat}/messages")).json()["items"]
        assert len(messages) == 1 and messages[0]["images"] == [] and messages[0]["hidden"]
        retry = await api.post(
            f"{path}/retry", json={"request_id": str(uuid4()), "message_id": run["message_id"]}
        )
        assert retry.status_code == 202, retry.text
        failed = await settled(api, path, retry.json()["id"])
        assert failed["status"] == "failed" and failed["error_code"] == "image_expired"
        assert len((await api.get(f"/api/conversations/{chat}/messages")).json()["items"]) == 1
    finally:
        await app.state.agent_runtime.close()


async def test_voice_generation_change_during_final_commit_cannot_save_old_answer(
    environment,  # noqa: F811
    monkeypatch,
):
    from app.services.answers import AnswerService
    from app.voice.sessions import Binding
    from app.voice.turns import VoiceTurns

    api, database = environment
    app, chat, _ = await ready(
        api,
        [
            {"action": "finish", "answer": {"text": "不可保存的旧轮次"}},
        ],
    )
    entered, release = asyncio.Event(), asyncio.Event()
    original = AnswerService._commit_result

    async def delayed(service, *args, **kwargs):
        entered.set()
        await release.wait()
        return await original(service, *args, **kwargs)

    monkeypatch.setattr(AnswerService, "_commit_result", delayed)
    voice = app.state.voice_runtime
    async with database.sessions() as session:
        owner = await session.scalar(select(LocalProfile.id))
    call = voice.registry.create(Binding(owner, UUID(chat), None, 0, "ordinary"), uuid4())
    played = []

    async def play(text, generation):
        played.append(text)

    turns = VoiceTurns(voice.registry, call, voice.answer, play)
    try:
        await turns.submit(1, "合成语音提问")
        task = call.turn_task
        await asyncio.wait_for(entered.wait(), 2)
        # Simulate the generation fence becoming visible before cancellation finishes.
        call.generation += 1
        release.set()
        await asyncio.wait_for(task, 3)
        messages = (await api.get(f"/api/conversations/{chat}/messages")).json()["items"]
        assert messages[0]["status"] == "interrupted" and not messages[0]["text"]
        assert not played
    finally:
        release.set()
        await voice.registry.close_call(call, "test_finished")
        await app.state.agent_runtime.close()


async def test_terminal_checkpoint_cleanup_does_not_repeat_or_starve_later_runs(
    environment,  # noqa: F811
    monkeypatch,
):
    api, database = environment
    app, _, path = await ready(
        api,
        [
            {"action": "finish", "answer": {"text": "合成终结记录"}},
        ],
    )
    try:
        run = (
            await api.post(path, json={"client_message_id": str(uuid4()), "text": "合成清理测试"})
        ).json()
        assert (await settled(api, path, run["id"]))["status"] == "completed"
        async with database.sessions() as session:
            row = await session.get(AgentRun, UUID(run["id"]))
            row.finished_at = datetime.now(UTC) - timedelta(days=8)
            await session.commit()
        deleted = []
        saver = app.state.agent_runtime.saver
        original = saver.adelete_thread

        async def tracked(thread):
            deleted.append(thread)
            await original(thread)

        monkeypatch.setattr(saver, "adelete_thread", tracked)
        async with database.sessions() as session:
            await session.get(AgentRun, UUID(run["id"]), with_for_update=True)
            await asyncio.wait_for(app.state.agent_runtime.cleanup_checkpoints(), 2)
            assert deleted == []  # A concurrent resume holds the run lock.
            await session.rollback()
        await app.state.agent_runtime.cleanup_checkpoints()
        await app.state.agent_runtime.cleanup_checkpoints()
        assert deleted == [run["id"]]
    finally:
        await app.state.agent_runtime.close()
