"""M3 image flow against disposable PostgreSQL and an isolated fake observer."""

import io
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from PIL import Image
from test_ingestion_lifecycle import FakeAdapter, application, finished, new_kb, upload
from test_postgres_local import migrate

from app.models import ImageAttachment
from app.providers.errors import ProviderError
from app.services.conversation_retention import sweep_expired_images
from app.services.errors import ServiceError

pytest_plugins = ["test_postgres_local"]
pytestmark = pytest.mark.postgres


class Observer:
    def __init__(self, uncertain=False):
        self.uncertain = uncertain
        self.calls = 0

    async def observe_images(self, images):
        self.calls += 1
        assert len(images) == 1 and images[0][0] == "image/png"
        content = ('{"observation":"红色铭牌，编号模糊","uncertain_identifiers":["AB?"]}'
                   if self.uncertain else
                   '{"observation":"红色铭牌，编号 AB-42","uncertain_identifiers":[]}')
        return type("Completion", (), {"content": content, "model": "qwen3.8-omni-flash"})()


class FailedObserver:
    def __init__(self, error):
        self.error = error

    async def observe_images(self, _images):
        raise self.error


class GeneralAnswer:
    async def route_with_context(self, question, candidates, context, documents):
        assert "红色铭牌" in question
        return '{"mode":"general"}'

    async def general_answer(self, question, context):
        assert "图片观察" in question
        return '{"text":"这张图显示红色铭牌。"}'


def synthetic_png():
    output = io.BytesIO()
    Image.new("RGB", (16, 12), color="red").save(output, format="PNG")
    return output.getvalue()


@pytest_asyncio.fixture
async def image_environment(schema_database, tmp_path):
    database, settings = schema_database
    await migrate(database)
    adapter = FakeAdapter()
    async with application(database, settings, tmp_path / "sources", adapter) as (api, app):
        kb = await new_kb(api)
        await finished(api, await upload(api, kb, data=b"A synthetic safety rule.\n"))
        chat = (await api.post("/api/conversations", json={"kb_id": kb})).json()["id"]
        app.state.answer_enabled = True
        app.state.answer_adapter = GeneralAnswer()
        observer = Observer()
        app.state.image_observer = observer
        yield api, database, app, chat, observer


@pytest.mark.asyncio
async def test_image_upload_observe_replay_and_chat_binding(image_environment):
    api, _db, _app, chat, observer = image_environment
    uploaded = await api.post(f"/api/conversations/{chat}/attachments", files={
        "file": ("plate.png", synthetic_png(), "image/png"),
    })
    assert uploaded.status_code == 201, uploaded.json()
    image_id = uploaded.json()["id"]
    other_chat = (await api.post("/api/conversations", json={
        "kb_id": (await api.get("/api/knowledge-bases")).json()["items"][0]["id"],
    })).json()["id"]
    other_image = await api.get(f"/api/conversations/{other_chat}/attachments/{image_id}")
    own_image = await api.get(f"/api/conversations/{chat}/attachments/{image_id}")
    assert other_image.status_code == 404
    assert own_image.content.startswith(b"\x89PNG")
    body = {"client_message_id": str(uuid4()), "text": "这张图片是什么？",
            "mode": "auto", "image_ids": [image_id]}
    first = await api.post(f"/api/conversations/{chat}/messages", json=body)
    assert first.status_code == 200, first.json()
    assert first.json()["status"] == "answered" and first.json()["citations"] == []
    assert first.json()["images"][0]["observation"] == "红色铭牌，编号 AB-42"
    assert observer.calls == 1
    replay = await api.post(f"/api/conversations/{chat}/messages", json=body)
    assert replay.json() == first.json() and observer.calls == 1
    assert (await api.post(f"/api/conversations/{chat}/messages", json={
        **body, "image_ids": [],
    })).status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize(("failure", "code"), [
    (ProviderError("permission", "dashscope", "qwen3.8-omni-flash", status=403),
     "image_provider_permission"),
    (ProviderError("quota", "dashscope", "qwen3.8-omni-flash", status=400),
     "image_provider_quota"),
    (ServiceError(503, "image_observation_invalid", "invalid observation"),
     "image_observation_invalid"),
])
async def test_image_failure_keeps_safe_specific_reason_for_retry(image_environment, failure, code):
    api, _db, app, chat, _observer = image_environment
    app.state.image_observer = FailedObserver(failure)
    image_id = (await api.post(f"/api/conversations/{chat}/attachments", files={
        "file": ("plate.png", synthetic_png(), "image/png"),
    })).json()["id"]
    first = (await api.post(f"/api/conversations/{chat}/messages", json={
        "client_message_id": str(uuid4()), "text": "这张图是什么？", "mode": "auto",
        "image_ids": [image_id],
    })).json()
    assert first["status"] == "failed" and first["error_code"] == code
    assert first["images"][0]["observation_status"] == "failed"
    app.state.image_observer = Observer()
    retried = (await api.post(
        f"/api/conversations/{chat}/messages/{first['message_id']}/retry",
        json={"attempt_id": str(uuid4())},
    )).json()
    assert retried["status"] == "answered"
    assert retried["images"][0]["observation_status"] == "ready"


@pytest.mark.asyncio
async def test_uncertain_identifier_needs_human_confirmation_before_retry(image_environment):
    api, _db, _app, chat, observer = image_environment
    observer.uncertain = True
    image_id = (await api.post(f"/api/conversations/{chat}/attachments", files={
        "file": ("plate.png", synthetic_png(), "image/png"),
    })).json()["id"]
    body = {"client_message_id": str(uuid4()), "text": "根据图片查型号",
            "mode": "auto", "image_ids": [image_id]}
    first = (await api.post(f"/api/conversations/{chat}/messages", json=body)).json()
    assert first["status"] == "needs_clarification" and first["citations"] == []
    assert first["images"][0]["needs_confirmation"] is True
    confirmed = await api.post(f"/api/conversations/{chat}/attachments/{image_id}/confirm",
                               json={"identifier": "AB-42"})
    assert confirmed.status_code == 200
    retry = await api.post(f"/api/conversations/{chat}/messages/{first['message_id']}/retry",
                           json={"attempt_id": str(uuid4())})
    assert retry.status_code == 200, retry.json()
    assert retry.json()["status"] == "answered"
    assert observer.calls == 1


@pytest.mark.asyncio
async def test_all_uncertain_images_require_confirmation_before_retry(image_environment):
    api, db, _app, chat, observer = image_environment
    observer.uncertain = True
    image_ids = []
    for index in range(2):
        response = await api.post(f"/api/conversations/{chat}/attachments", files={
            "file": (f"plate-{index}.png", synthetic_png(), "image/png"),
        })
        image_ids.append(response.json()["id"])
    # Ensure UUID order disagrees with creation time, so image labels follow time.
    image_ids.sort(key=UUID, reverse=True)
    async with db.sessions() as session:
        for position, image_id in enumerate(image_ids):
            image = await session.get(ImageAttachment, UUID(image_id))
            image.created_at = datetime.now(UTC) - timedelta(minutes=2 - position)
        await session.commit()
    first = (await api.post(f"/api/conversations/{chat}/messages", json={
        "client_message_id": str(uuid4()), "text": "根据两张图查编号",
        "mode": "auto", "image_ids": image_ids,
    })).json()
    assert first["status"] == "needs_clarification"
    assert [image["id"] for image in first["images"]] == image_ids
    await api.post(f"/api/conversations/{chat}/attachments/{image_ids[0]}/confirm",
                   json={"identifier": "AB-42"})
    premature = await api.post(
        f"/api/conversations/{chat}/messages/{first['message_id']}/retry",
        json={"attempt_id": str(uuid4())},
    )
    assert premature.status_code == 409
    assert premature.json()["detail"]["code"] == "answer_not_retryable"
    await api.post(f"/api/conversations/{chat}/attachments/{image_ids[1]}/confirm",
                   json={"identifier": "AB-43"})
    retry = await api.post(
        f"/api/conversations/{chat}/messages/{first['message_id']}/retry",
        json={"attempt_id": str(uuid4())},
    )
    assert retry.status_code == 200 and retry.json()["status"] == "answered"


@pytest.mark.asyncio
async def test_active_voice_blocks_image_upload(image_environment, monkeypatch):
    api, _db, app, chat, _observer = image_environment

    def active_voice(_conversation):
        raise ServiceError(409, "voice_active", "请先挂断通话")

    monkeypatch.setattr(app.state.voice_runtime.registry, "require_text", active_voice)
    response = await api.post(f"/api/conversations/{chat}/attachments", files={
        "file": ("plate.png", synthetic_png(), "image/png"),
    })
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "voice_active"
    assert not list(app.state.image_store.root.iterdir())


@pytest.mark.asyncio
async def test_expiry_redacts_derived_answer_and_removes_image(image_environment):
    api, db, app, chat, _observer = image_environment
    image_id = (await api.post(f"/api/conversations/{chat}/attachments", files={
        "file": ("plate.png", synthetic_png(), "image/png"),
    })).json()["id"]
    response = await api.post(f"/api/conversations/{chat}/messages", json={
        "client_message_id": str(uuid4()), "text": "这张图片是什么？", "mode": "auto",
        "image_ids": [image_id],
    })
    assert response.status_code == 200
    async with db.sessions() as session:
        image = await session.get(ImageAttachment, UUID(image_id))
        image.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    before_sweep = (await api.get(f"/api/conversations/{chat}/messages")).json()["items"]
    assert before_sweep[0]["hidden"] is True
    assert before_sweep[0]["text"] == "" and before_sweep[0]["images"] == []
    assert await sweep_expired_images(db, app.state.image_store) == 1
    history = (await api.get(f"/api/conversations/{chat}/messages")).json()["items"]
    assert history[0]["status"] == "interrupted" and history[0]["text"] == ""
    assert history[0]["images"] == []
    assert (await api.get(f"/api/conversations/{chat}/attachments/{image_id}")).status_code == 404
