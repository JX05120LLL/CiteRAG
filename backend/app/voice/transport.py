from datetime import timedelta
from importlib.util import find_spec
from uuid import UUID, uuid4

from app.config import Settings
from app.services.errors import ServiceError


def transport_status(settings: Settings) -> dict[str, str]:
    state = "disabled"
    if settings.voice_transport_enabled:
        state = "configured" if (
            settings.livekit_api_key and settings.livekit_api_key.get_secret_value()
            and settings.livekit_api_secret and settings.livekit_api_secret.get_secret_value()
            and find_spec("livekit") is not None and find_spec("livekit.api") is not None
        ) else "not_configured"
    # Configuration alone says nothing about server connectivity or speech models.
    return {"transport": state, "assistant": "not_configured", "purpose": "media_test"}


def connection_details(settings: Settings, conversation_id: UUID) -> dict[str, str]:
    if not settings.voice_transport_enabled:
        raise ServiceError(503, "voice_transport_disabled", "本地音频连接测试尚未启用")
    if transport_status(settings)["transport"] != "configured":
        raise ServiceError(503, "voice_transport_not_configured", "LiveKit 本地配置或依赖尚未准备")
    from livekit import api

    room = "citerag-media-" + uuid4().hex
    identity = "media-client-" + uuid4().hex
    token = (api.AccessToken(settings.livekit_api_key.get_secret_value(),
                             settings.livekit_api_secret.get_secret_value())
             .with_identity(identity).with_ttl(timedelta(seconds=90))
             .with_grants(api.VideoGrants(
                 room_join=True, room=room, can_publish=True, can_subscribe=True,
                 can_publish_data=False, can_publish_sources=["microphone"],
                 can_update_own_metadata=False,
             )).to_jwt())
    return {"server_url": settings.livekit_url, "token": token, "room": room,
            "conversation_id": str(conversation_id), "assistant": "not_configured",
            "purpose": "media_test"}
