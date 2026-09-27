"""Public synthetic voice regressions. No speech provider credentials or paid requests."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_ingestion_lifecycle import finished, new_kb, upload
from test_m13_answer_api import m13_environment, no_provider_access  # noqa: F401

from app.models import KnowledgeBase, LocalProfile
from app.voice.providers import SpeechError
from app.voice.sessions import Binding, VoiceSessions

pytest_plugins = ["test_postgres_local"]


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_voice_commit_guard_saves_interrupted_without_fabricated_kb_change(
    m13_environment,  # noqa: F811
):
    from uuid import UUID

    from app.services.answers import AnswerService
    from app.services.errors import ServiceError

    api, db, app, _ = m13_environment
    kb = await new_kb(api)
    await finished(api, await upload(api, kb))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    async with db.sessions() as session:
        owner = await session.scalar(select(LocalProfile.id))
        with pytest.raises(ServiceError) as caught:
            await AnswerService(session, commit_allowed=lambda: False).ask(
                owner, UUID(chat), uuid4(), "你好", None, None, mode="auto")
    assert caught.value.code == "request_interrupted"
    history = (await api.get(f"/api/conversations/{chat}/messages")).json()["items"]
    assert history[0]["status"] == "interrupted" and history[0]["saved"]
    assert history[0]["text"] == "" and history[0]["citations"] == []


def test_streaming_mp3_decoder_accepts_split_id3_header():
    import io

    import av
    import numpy as np

    from app.voice.audio import MP3Decoder

    output = io.BytesIO()
    with av.open(output, mode="w", format="mp3") as container:
        stream = container.add_stream("libmp3lame", rate=24000)
        stream.layout = "mono"
        frame = av.AudioFrame.from_ndarray(np.zeros((1, 24000), dtype=np.float32),
                                         format="fltp", layout="mono")
        frame.sample_rate = 24000
        for packet in (*stream.encode(frame), *stream.encode(None)):
            container.mux(packet)
    encoded = output.getvalue()
    assert encoded.startswith(b"ID3")
    decoder = MP3Decoder()
    frames = []
    for index in range(0, len(encoded), 7):
        frames.extend(decoder.feed(encoded[index:index + 7]))
    frames.extend(decoder.finish())
    assert sum(frame.samples for frame in frames) >= 24000


@pytest.mark.asyncio
async def test_cleanup_revokes_media_even_when_worker_already_failed():
    registry = VoiceSessions()
    call = registry.create(Binding(uuid4(), uuid4(), uuid4(), 1, "space"), uuid4())
    disposed = []

    async def broken():
        raise RuntimeError("synthetic failure")

    async def dispose():
        disposed.append(True)

    call.worker_task = asyncio.create_task(broken())
    await asyncio.sleep(0)
    call.dispose_media = dispose
    await registry.close_call(call, "hangup")
    assert disposed == [True] and call.closed


@pytest.mark.asyncio
async def test_failed_room_cleanup_keeps_gate_until_successful_retry():
    from app.services.errors import ServiceError

    registry = VoiceSessions()
    call = registry.create(Binding(uuid4(), uuid4(), uuid4(), 1, "space"), uuid4())
    attempts = []

    async def dispose():
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError("synthetic room unavailable")

    call.dispose_media = dispose
    with pytest.raises(RuntimeError):
        await registry.close_call(call, "hangup")
    with pytest.raises(ServiceError, match="请先挂断"):
        registry.require_text(call.binding.conversation)
    await registry.close_call(call, "hangup")
    registry.require_text(call.binding.conversation)
    assert call.closed and len(attempts) == 2


@pytest.mark.asyncio
async def test_restart_recovers_only_rooms_owned_by_this_installation(monkeypatch):
    import json
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from pydantic import SecretStr

    from app.config import Settings
    from app.voice.runtime import VoiceRuntime

    owner, deleted = uuid4(), []

    @asynccontextmanager
    async def sessions():
        async def scalar(query):
            return owner
        yield SimpleNamespace(scalar=scalar)

    async def list_rooms(body):
        return SimpleNamespace(rooms=[
            SimpleNamespace(name="citerag-voice-owned", metadata=json.dumps({
                "citerag_voice_installation": str(owner)})),
            SimpleNamespace(name="citerag-voice-foreign", metadata=json.dumps({
                "citerag_voice_installation": str(uuid4())})),
            SimpleNamespace(name="unidentified", metadata="{}"),
        ])

    async def delete_room(body):
        deleted.append(body.room)

    async def close():
        pass

    client = SimpleNamespace(room=SimpleNamespace(list_rooms=list_rooms, delete_room=delete_room),
                             aclose=close)
    monkeypatch.setattr("livekit.api.LiveKitAPI", lambda **kwargs: client)
    settings = Settings(voice_assistant_enabled=True, voice_transport_enabled=True,
                        livekit_api_key=SecretStr("synthetic"),
                        livekit_api_secret=SecretStr("synthetic-secret-at-least-thirty-two-bytes"))
    runtime = VoiceRuntime(SimpleNamespace(state=SimpleNamespace(
        settings=settings, database=SimpleNamespace(sessions=sessions))))
    await runtime.start()
    assert runtime.clean and deleted == ["citerag-voice-owned"]
    assert runtime.registry.calls == {}
    await runtime.close()


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.parametrize("change", ["maintaining", "blocked", "revision", "workspace"])
async def test_server_text_gate_and_binding_changes_abort_call(m13_environment, change):  # noqa: F811
    api, db, app, _ = m13_environment
    kb = await new_kb(api)
    await finished(api, await upload(api, kb))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    runtime = app.state.voice_runtime
    from uuid import UUID
    async with db.sessions() as session:
        owner = await session.scalar(select(LocalProfile.id))
        binding = await runtime.binding(session, owner, UUID(chat))
    call = runtime.registry.create(binding, uuid4())
    for path, body in [
        (f"/api/conversations/{chat}/messages", {"client_message_id": str(uuid4()), "text": "new"}),
        (f"/api/conversations/{chat}/messages/stream",
         {"client_message_id": str(uuid4()), "text": "new"}),
        (f"/api/conversations/{chat}/messages/{uuid4()}/retry", {"attempt_id": str(uuid4())}),
    ]:
        response = await api.post(path, json=body)
        assert response.status_code == 409 and response.json()["detail"]["code"] == "voice_active"
    async with db.sessions() as session:
        base = await session.get(KnowledgeBase, UUID(kb))
        if change in {"maintaining", "blocked"}:
            base.status = change
        elif change == "revision":
            base.revision += 1
        else:
            base.active_workspace = "synthetic_new_workspace"
        await session.commit()
    await runtime.reconcile()
    assert call.closed and not runtime.registry.active(UUID(chat))
    assert call.events[-1]["type"] == "ended"


@pytest.mark.asyncio
async def test_tts_failure_keeps_committed_text_and_true_sources():
    from app.voice.turns import VoiceTurns

    registry = VoiceSessions()
    call = registry.create(Binding(uuid4(), uuid4(), uuid4(), 2, "space"), uuid4())
    requests = []

    async def answer(call, request_id, text, generation):
        requests.append(request_id)
        return {"status": "answered", "saved": True, "text": "Synthetic source text",
                "route": "semantic", "citations": [{"evidence_id": "E1"}]}

    async def play(text, generation):
        raise SpeechError("tts_provider_failed")

    turns = VoiceTurns(registry, call, answer, play)
    await turns.submit(1, "synthetic question")
    await call.turn_task
    assert len(requests) == 1
    saved = [event for event in call.events if event["type"] == "answer"]
    assert saved[0]["answer"]["citations"] == [{"evidence_id": "E1"}]
    assert call.events[-1]["code"] == "tts_provider_failed"
    assert call.events[-1]["text_available"] is True
    await turns.submit(1, "synthetic question")
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_late_answer_and_audio_cannot_revive_after_barge_in():
    from app.voice.turns import VoiceTurns

    registry = VoiceSessions()
    call = registry.create(Binding(uuid4(), uuid4(), uuid4(), 2, "space"), uuid4())
    release = asyncio.Event()
    played = []

    async def answer(call, request_id, text, generation):
        try:
            await release.wait()
        except asyncio.CancelledError:
            pass  # Simulate a provider which delivers a late result despite cancellation.
        return {"status": "answered", "saved": True, "text": "late", "citations": []}

    async def play(text, generation):
        played.append(text)

    turns = VoiceTurns(registry, call, answer, play)
    await turns.submit(1, "question")
    await asyncio.sleep(0)
    registry.interrupt(call, "barge_in")
    release.set()
    await call.turn_task
    assert played == []
    assert not [event for event in call.events if event["type"] == "answer"]


@pytest.mark.asyncio
async def test_correction_cancels_old_turn_and_uses_new_stable_request():
    from app.voice.turns import VoiceTurns

    registry = VoiceSessions()
    call = registry.create(Binding(uuid4(), uuid4(), uuid4(), 2, "space"), uuid4())
    entered = asyncio.Event()
    requests = []

    async def answer(call, request_id, text, generation):
        requests.append((request_id, text))
        if text == "original":
            entered.set()
            await asyncio.sleep(30)
        return {"status": "answered", "saved": True, "text": text, "citations": []}

    async def play(text, generation):
        return None

    turns = VoiceTurns(registry, call, answer, play)
    await turns.submit(1, "original")
    await entered.wait()
    await turns.submit(1, "corrected", 2)
    await call.turn_task
    assert len(requests) == 2 and requests[0][0] != requests[1][0]
    assert call.transcripts[(1, 1)] == "original"
    saved = [event for event in call.events if event["type"] == "answer"]
    assert saved[-1]["answer"]["text"] == "corrected"


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_session_api_single_control_retry_end_and_durable_voice_input(
    m13_environment, monkeypatch,  # noqa: F811
):
    from types import SimpleNamespace
    from uuid import UUID

    from pydantic import SecretStr

    from app.voice.turns import VoiceTurns

    api, db, app, _ = m13_environment
    kb = await new_kb(api)
    await finished(api, await upload(api, kb))
    chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
    runtime = app.state.voice_runtime
    app.state.answer_enabled = True
    app.state.settings = app.state.settings.model_copy(update={
        "livekit_api_key": SecretStr("synthetic-dev-key"),
        "livekit_api_secret": SecretStr("synthetic-secret-at-least-thirty-two-bytes"),
    })
    monkeypatch.setattr("app.voice.runtime.assistant_configured", lambda settings: True)
    rooms = []

    async def create_room(body):
        rooms.append(body.name)

    class SyntheticWorker:
        def __init__(self, runtime, call):
            self.call = call
            self.turns = VoiceTurns(runtime.registry, call, runtime.answer, self.play)
            self.closed = False

        async def run(self):
            await asyncio.sleep(60)

        async def play(self, text, generation):
            return None

        async def close(self):
            self.closed = True

        def reset_input(self):
            pass

    runtime.worker_factory = SyntheticWorker
    runtime.api = SimpleNamespace(room=SimpleNamespace(create_room=create_room))
    runtime.clean = True
    body = {"client_request_id": str(uuid4())}
    first = await api.post(f"/api/conversations/{chat}/voice/sessions", json=body)
    assert first.status_code == 201, first.json()
    value = first.json()
    replay = await api.post(f"/api/conversations/{chat}/voice/sessions", json=body)
    assert replay.json()["session_id"] == value["session_id"] and len(rooms) == 1
    competing = await api.post(f"/api/conversations/{chat}/voice/sessions",
                               json={"client_request_id": str(uuid4())})
    assert competing.status_code == 409
    route = f"/api/voice/sessions/{value['session_id']}"
    denied = await api.post(route + "/renew", json={})
    assert denied.status_code == 403
    control = {"X-CiteRAG-Voice-Control": value["control_token"]}
    assert (await api.post(route + "/renew", headers=control, json={})).status_code == 200
    call = runtime.registry.calls[UUID(value["session_id"])]
    worker = runtime.workers[call.id]
    await worker.turns.submit(1, "你好")  # Real AnswerService courtesy route; no model request.
    await call.turn_task
    history = (await api.get(f"/api/conversations/{chat}/messages")).json()["items"]
    assert len(history) == 1 and history[0]["route"] == "chat" and history[0]["saved"]
    assert history[0]["citations"] == []
    assert (await api.post(route + "/stop", headers=control)).status_code == 200
    ended = await api.post(route + "/end", headers=control)
    assert ended.status_code == 200 and worker.closed and call.closed
    assert call.dispose_media is None and call.worker_task is None and call.turn_task is None
    assert call.transcripts == {} and call.id not in runtime.workers
    assert (await api.post(route + "/end", headers=control)).status_code == 200
    assert (await api.post(route + "/renew", headers=control, json={})).status_code == 409
    assert len((await api.get(f"/api/conversations/{chat}/messages")).json()["items"]) == 1
    # The test-only API stand-in does not own a network client.
    runtime.api = None


@pytest.mark.asyncio
async def test_worker_binding_failure_masks_old_answers_even_before_monitor():
    from types import SimpleNamespace

    from app.services.errors import ServiceError
    from app.voice.worker import LiveKitWorker

    registry = VoiceSessions()
    call = registry.create(Binding(uuid4(), uuid4(), uuid4(), 2, "space"), uuid4())

    async def connect(url, token):
        return None

    async def check(call):
        raise ServiceError(409, "kb_changed", "synthetic revision change")

    worker = LiveKitWorker.__new__(LiveKitWorker)
    worker.call, worker.registry = call, registry
    worker.settings = SimpleNamespace(livekit_url="ws://127.0.0.1:17890")
    worker.runtime = SimpleNamespace(check=check, token=lambda call, **kwargs: "synthetic")
    worker.room = SimpleNamespace(on=lambda event: lambda callback: callback, connect=connect)
    worker.done = asyncio.Event()
    await worker.run()
    await asyncio.sleep(0)
    await call.closing
    assert call.closed
    assert call.events[-1]["reason"] == "binding_or_lease_invalid"
