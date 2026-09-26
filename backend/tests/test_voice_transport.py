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


def configured(**values):
    return Settings(voice_transport_enabled=True, livekit_api_key=SecretStr("devkey"),
                    livekit_api_secret=SecretStr("secret"), **values)


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
        return SimpleNamespace(kb_id=uuid4())

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
                        AsyncMock(return_value=SimpleNamespace(kb_id=uuid4())))
    session = SimpleNamespace(scalar=AsyncMock(side_effect=["ready", uuid4()]))
    with pytest.raises(ServiceError) as caught:
        await token(chat, request, owner, session)
    assert caught.value.code == "answer_running"
    session = SimpleNamespace(scalar=AsyncMock(side_effect=["ready", None]))
    value = await token(chat, request, owner, session)
    assert value["conversation_id"] == str(chat)
    assert value["purpose"] == "media_test"
