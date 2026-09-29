"""Offline transport boundaries; no microphone, cloud or model calls."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.main import create_app
from app.services.errors import ServiceError
from app.voice.transport import connection_details, transport_status


def test_saved_voice_keys_are_loaded_only_when_assistant_is_explicitly_enabled(monkeypatch):
    from app.credentials import CredentialRecord

    calls = []
    def load(path, provider):
        calls.append((path, provider))
        return CredentialRecord(2, provider, "api-key", "cn-beijing", None, {}, provider,
                                SecretStr("public-synthetic-" + provider), "synthetic-hash")
    monkeypatch.setattr("app.credentials.load_credential", load)
    monkeypatch.delenv("CITERAG_VOICE_ASR_KEY", raising=False)
    monkeypatch.delenv("CITERAG_VOICE_ASR_APP_KEY", raising=False)
    monkeypatch.delenv("CITERAG_VOICE_TTS_KEY", raising=False)
    monkeypatch.setenv("CITERAG_VOICE_ASSISTANT_ENABLED", "false")
    assert Settings.from_env().voice_asr_key is None
    assert calls == []
    monkeypatch.setenv("CITERAG_VOICE_ASSISTANT_ENABLED", "true")
    settings = Settings.from_env()
    assert settings.voice_asr_key.get_secret_value() == "public-synthetic-volcengine"
    assert settings.voice_tts_key.get_secret_value() == "public-synthetic-minimax"
    assert settings.voice_asr_app_key is None
    assert [provider for _, provider in calls] == ["volcengine", "minimax"]


def test_explicit_voice_environment_keys_override_saved_records(monkeypatch):
    def forbidden(*args):
        raise AssertionError("Explicit keys must not read local credentials")
    monkeypatch.setattr("app.credentials.load_credential", forbidden)
    monkeypatch.setenv("CITERAG_VOICE_ASSISTANT_ENABLED", "true")
    monkeypatch.setenv("CITERAG_VOICE_ASR_KEY", "public-synthetic-env-asr")
    monkeypatch.setenv("CITERAG_VOICE_TTS_KEY", "public-synthetic-env-tts")
    assert Settings.from_env().voice_asr_key.get_secret_value() == "public-synthetic-env-asr"


def test_legacy_saved_asr_keeps_its_app_id_and_token_together(monkeypatch):
    from app.credentials import CredentialError, CredentialRecord

    def load(path, provider):
        if provider == "minimax":
            raise CredentialError("not configured")
        return CredentialRecord(1, provider, "app-token", "cn-beijing", None, {}, "12345",
                                SecretStr("public-synthetic-legacy-token"), "synthetic-hash")
    monkeypatch.setattr("app.credentials.load_credential", load)
    monkeypatch.setenv("CITERAG_VOICE_ASSISTANT_ENABLED", "true")
    for name in ("CITERAG_VOICE_ASR_KEY", "CITERAG_VOICE_ASR_APP_KEY", "CITERAG_VOICE_TTS_KEY"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings.from_env()
    assert settings.voice_asr_app_key.get_secret_value() == "12345"
    assert settings.voice_asr_key.get_secret_value() == "public-synthetic-legacy-token"
    assert settings.voice_tts_key is None


def test_explicit_app_id_never_mixes_with_saved_api_key(monkeypatch):
    def forbidden(*args):
        raise AssertionError("Explicit partial legacy configuration must not mix saved keys")
    monkeypatch.setattr("app.credentials.load_credential", forbidden)
    monkeypatch.setenv("CITERAG_VOICE_ASSISTANT_ENABLED", "true")
    monkeypatch.delenv("CITERAG_VOICE_ASR_KEY", raising=False)
    monkeypatch.setenv("CITERAG_VOICE_ASR_APP_KEY", "12345")
    monkeypatch.setenv("CITERAG_VOICE_TTS_KEY", "public-synthetic-env-tts")
    assert Settings.from_env().voice_asr_key is None


def test_missing_or_invalid_saved_voice_keys_remain_not_configured_without_diagnostics(
    monkeypatch, capsys,
):
    from app.credentials import CredentialError

    def invalid(*args):
        raise CredentialError("private diagnostic must not escape")
    monkeypatch.setattr("app.credentials.load_credential", invalid)
    monkeypatch.setenv("CITERAG_VOICE_ASSISTANT_ENABLED", "true")
    for name in ("CITERAG_VOICE_ASR_KEY", "CITERAG_VOICE_TTS_KEY", "CITERAG_VOICE_ASR_APP_KEY"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings.from_env()
    assert settings.voice_asr_key is None and settings.voice_tts_key is None
    assert "private" not in str(settings)
    assert capsys.readouterr() == ("", "")


def test_asr_binary_envelope_and_final_transcript():
    import gzip
    import json
    import struct

    from app.voice.providers import asr_packet, asr_response

    packet = asr_packet(b"\0\0" * 320, audio=True, final=True)
    assert packet[:4] == bytes([0x11, 0x22, 0x01, 0])
    assert gzip.decompress(packet[8:]) == b"\0\0" * 320
    payload = gzip.compress(json.dumps({"result": {"text": "你好"}}).encode())
    response = bytes([0x11, 0x93, 0x11, 0]) + struct.pack(">iI", -3, len(payload)) + payload
    assert asr_response(response) == ("你好", True, -3)
    with pytest.raises(ValueError):
        asr_response(response[:-1])


def test_asr_provider_errors_never_echo_private_payload():
    import struct

    from app.voice.providers import SpeechError, asr_response

    private = b"private provider diagnostics"
    with pytest.raises(SpeechError) as caught:
        asr_response(bytes([0x11, 0xF0, 0x10, 0]) +
                     struct.pack(">II", 45000001, len(private)) + private)
    assert str(caught.value) == "asr_provider_failed"


@pytest.mark.asyncio
async def test_minimax_stream_rejects_error_and_returns_only_audio():
    import httpx

    from app.voice.providers import MiniMaxTTS, SpeechError

    async def reply(request):
        assert request.url == "https://api.minimax.cn/v1/t2a_v2"
        return httpx.Response(200, text='data: {"base_resp":{"status_code":0},'
                              '"data":{"audio":"0102","status":1}}\n\n'
                              'data: {"base_resp":{"status_code":0},'
                              '"data":{"status":2}}\n\n')

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        tts = MiniMaxTTS("synthetic-test-key", "male-qn-qingse", client=client)
        assert b"".join([part async for part in tts.stream("合成测试文字")]) == b"\x01\x02"

    async def denied(request):
        return httpx.Response(200, text='data: {"base_resp":{"status_code":1004,'
                              '"status_msg":"private"}}\n\n')

    async with httpx.AsyncClient(transport=httpx.MockTransport(denied)) as client:
        with pytest.raises(SpeechError, match="tts_provider_failed"):
            _ = [part async for part in MiniMaxTTS("test", "voice", client=client).stream("text")]


def configured(**values):
    return Settings(voice_transport_enabled=True, livekit_api_key=SecretStr("devkey"),
                    livekit_api_secret=SecretStr("secret"), **values)


@pytest.mark.asyncio
async def test_voice_lease_single_controller_expiry_and_late_result():
    from app.voice.sessions import Binding, VoiceSessions

    now = [100.0]
    registry = VoiceSessions(clock=lambda: now[0])
    binding = Binding(uuid4(), uuid4(), uuid4(), 1, "workspace")
    call = registry.create(binding, uuid4())
    assert registry.create(binding, call.start_id) is call
    with pytest.raises(ServiceError) as caught:
        registry.create(binding, uuid4())
    assert caught.value.code == "voice_active"
    with pytest.raises(ServiceError) as caught:
        registry.authorize(call.id, binding.owner, "wrong")
    assert caught.value.code == "voice_control_forbidden"
    generation = call.generation
    registry.interrupt(call, "barge_in")
    assert not registry.current(call, generation)
    now[0] += 41
    assert not registry.current(call, call.generation)
    assert registry.expired() == [call]
    await registry.close_call(call, "lease_expired")
    assert not registry.active(binding.conversation)


def test_final_transcript_dedup_and_correction_keep_history():
    from app.voice.sessions import Binding, VoiceSessions

    registry = VoiceSessions()
    call = registry.create(Binding(uuid4(), uuid4(), uuid4(), 3, "workspace"), uuid4())
    first = registry.transcript(call, 1, "你好", 1)
    assert first and registry.transcript(call, 1, "你好", 1) is None
    assert registry.transcript(call, 0, "late", 1) is None
    corrected = registry.transcript(call, 1, "你好，请介绍资料", 2)
    assert corrected and corrected[0] != first[0]
    assert call.transcripts[(1, 1)] == "你好"
    assert registry.transcript(call, 1, "older", 1) is None


@pytest.mark.asyncio
async def test_interrupt_cancels_inflight_work_and_clears_actual_audio():
    import asyncio

    from app.voice.sessions import Binding, VoiceSessions

    registry = VoiceSessions()
    call = registry.create(Binding(uuid4(), uuid4(), uuid4(), 3, "workspace"), uuid4())
    audio = []
    call.flush_audio = lambda: audio.clear()
    audio.extend([b"pending audio", b"more audio"])
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def pending():
        entered.set()
        try:
            await asyncio.sleep(30)
        finally:
            cancelled.set()

    call.turn_task = asyncio.create_task(pending())
    await entered.wait()
    registry.interrupt(call, "false_interruption")
    await cancelled.wait()
    assert audio == [] and call.turn_task.cancelled()
    assert call.events[-1]["type"] == "interrupted"
    await registry.close_call(call, "hangup")


def test_vad_endpoint_ignores_clicks_and_bounds_speech():
    from app.voice.vad import Endpoint

    endpoint = Endpoint()
    assert endpoint.feed(b"\0" * 1024, 0.8) == "silence"
    assert endpoint.feed(b"\0" * 1024, 0.0) == "silence"
    assert [endpoint.feed(b"\0" * 1024, 0.9) for _ in range(3)][-1] == "start"
    assert endpoint.feed(b"\0" * 1024, 0.1) == "speech"
    assert [endpoint.feed(b"\0" * 1024, 0.1) for _ in range(19)][-1] == "end"
    assert not endpoint.speaking and len(endpoint.pre_roll) <= 6


@pytest.mark.parametrize("url", [
    "wss://cloud.example", "ws://0.0.0.0:7880", "ws://127.0.0.1.evil:7880",
    "ws://user:password@127.0.0.1:7880", "ws://127.0.0.1:7880/path",
    "ws://127.0.0.1:7880?token=secret", "http://127.0.0.1:7880",
    "ws://127.0.0.2:7880",
])
def test_only_loopback_websocket_origins(url):
    with pytest.raises(ValidationError):
        configured(livekit_url=url)


def test_status_is_configuration_not_a_connectivity_or_assistant_claim():
    assert transport_status(Settings()) == {
        "transport": "disabled", "assistant": "not_configured", "purpose": "media_test",
    }
    assert transport_status(Settings(voice_transport_enabled=True))["transport"] == "not_configured"
    assert transport_status(configured())["transport"] == "configured"


def test_disabled_tokens_fail_closed():
    with pytest.raises(ServiceError) as caught:
        connection_details(Settings(), uuid4())
    assert caught.value.code == "voice_transport_disabled"


def test_real_sdk_token_has_short_lived_microphone_only_grants():
    import jwt

    settings = configured()
    conversation = uuid4()
    value = connection_details(settings, conversation)
    claims = jwt.decode(value["token"], "secret", algorithms=["HS256"], issuer="devkey")
    assert claims["exp"] - claims["nbf"] == 90
    assert value["conversation_id"] == str(conversation)
    assert value["assistant"] == "not_configured"
    assert claims["video"] == {
        "roomJoin": True, "room": value["room"], "canPublish": True,
        "canSubscribe": True, "canPublishData": False, "canPublishSources": ["microphone"],
        "canUpdateOwnMetadata": False,
    }
    # Rooms are unique and contain neither business IDs nor document names.
    other = connection_details(settings, conversation)
    assert other["room"] != value["room"]
    assert str(conversation) not in value["room"]


def test_status_is_read_only_and_tokens_keep_http_origin_guard():
    with TestClient(create_app(configured()), base_url="http://127.0.0.1",
                    client=("127.0.0.1", 50000)) as client:
        response = client.get("/api/voice/status")
        assert response.status_code == 200
        assert response.json() == transport_status(configured())
        assert response.headers["cache-control"] == "no-store"
        assert client.post(f"/api/conversations/{uuid4()}/voice/token").status_code == 403


@pytest.mark.asyncio
async def test_token_route_rejects_unowned_or_unready_chat(monkeypatch):
    from app.api.voice import token

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=configured())))
    owner, chat = uuid4(), uuid4()

    async def inaccessible(*args):
        raise ServiceError(404, "conversation_not_found", "聊天不可访问")

    monkeypatch.setattr("app.api.voice.ConversationService.get_owned", inaccessible)
    with pytest.raises(ServiceError) as caught:
        await token(chat, request, owner, SimpleNamespace())
    assert caught.value.code == "conversation_not_found"

    async def owned(*args):
        return SimpleNamespace(kb_id=uuid4(), archived_at=None)

    async def blocked(*args):
        return "blocked"

    monkeypatch.setattr("app.api.voice.ConversationService.get_owned", owned)
    with pytest.raises(ServiceError) as caught:
        await token(chat, request, owner, SimpleNamespace(scalar=blocked))
    assert caught.value.code == "kb_not_ready"


@pytest.mark.asyncio
async def test_token_route_rejects_running_answer_and_accepts_ready_chat(monkeypatch):
    from unittest.mock import AsyncMock

    from app.api.voice import token

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=configured())))
    owner, chat = uuid4(), uuid4()
    monkeypatch.setattr("app.api.voice.ConversationService.get_owned",
                        AsyncMock(return_value=SimpleNamespace(kb_id=uuid4(), archived_at=None)))
    session = SimpleNamespace(scalar=AsyncMock(side_effect=["ready", uuid4()]))
    with pytest.raises(ServiceError) as caught:
        await token(chat, request, owner, session)
    assert caught.value.code == "answer_running"
    session = SimpleNamespace(scalar=AsyncMock(side_effect=["ready", None]))
    value = await token(chat, request, owner, session)
    assert value["conversation_id"] == str(chat)
    assert value["purpose"] == "media_test"

    monkeypatch.setattr(
        "app.api.voice.ConversationService.get_owned",
        AsyncMock(return_value=SimpleNamespace(kb_id=uuid4(), archived_at="archived")),
    )
    with pytest.raises(ServiceError) as caught:
        await token(chat, request, owner, SimpleNamespace(scalar=AsyncMock()))
    assert caught.value.code == "conversation_archived"
