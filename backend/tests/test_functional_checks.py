"""Manual supplier checks use only bounded, synthetic providers here."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_manual_check_is_idempotent_and_never_starts_on_read(tmp_path):
    from app.api.functional_checks import FunctionalChecks

    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def probe(kind):
        calls.append(kind)
        entered.set()
        await release.wait()

    checks = FunctionalChecks(tmp_path, lambda _kind: "a" * 64,
                              {"model": probe}, secure_path=lambda _path: None)
    assert checks.read("model")["state"] == "not_checked"
    assert calls == []
    request_id = uuid4()
    first = await checks.start("model", request_id)
    await entered.wait()
    assert first["state"] == "running"
    assert (await checks.start("model", request_id))["state"] == "running"
    assert calls == ["model"]
    with pytest.raises(ValueError, match="check_in_progress"):
        await checks.start("model", uuid4())
    release.set()
    await checks.wait("model")
    result = checks.read("model")
    assert result["state"] == "available"
    assert (await checks.start("model", request_id))["state"] == "available"
    assert calls == ["model"]
    assert result["request_id"] == str(request_id)


@pytest.mark.asyncio
async def test_auto_batch_deduplicates_concurrent_entries_and_reuses_fresh_results(tmp_path):
    from app.api.functional_checks import FunctionalChecks

    release = asyncio.Event()
    calls = []

    async def probe(kind):
        calls.append(kind)
        await release.wait()

    checks = FunctionalChecks(
        tmp_path, lambda kind: None if kind == "knowledge" else "a" * 64,
        {kind: probe for kind in ("model", "asr", "tts")},
        secure_path=lambda _path: None,
    )
    first, second = await asyncio.gather(checks.ensure_all(), checks.ensure_all())
    assert sorted(calls) == ["asr", "model", "tts"]
    assert all(first["checks"][kind]["state"] == "running"
               for kind in ("model", "asr", "tts"))
    assert first["checks"]["knowledge"]["state"] == "not_checked"
    assert second["checks"]["model"]["request_id"] == first["checks"]["model"]["request_id"]
    release.set()
    await asyncio.gather(*(checks.wait(kind) for kind in ("model", "asr", "tts")))
    cached = (await checks.ensure_all())["checks"]
    assert all(cached[kind]["state"] == "available" for kind in ("model", "asr", "tts"))
    assert sorted(calls) == ["asr", "model", "tts"]
    await checks.ensure_all(force=True)
    await asyncio.gather(*(checks.wait(kind) for kind in ("model", "asr", "tts")))
    assert sorted(calls) == ["asr", "asr", "model", "model", "tts", "tts"]


@pytest.mark.asyncio
async def test_auto_batch_skips_missing_configuration_and_rechecks_changed_fingerprint(tmp_path):
    from app.api.functional_checks import FunctionalChecks

    fingerprints = {"model": None, "asr": None, "tts": None}
    calls = []

    async def probe(kind):
        calls.append(kind)

    checks = FunctionalChecks(tmp_path, fingerprints.get,
                              {kind: probe for kind in fingerprints},
                              secure_path=lambda _path: None)
    empty = await checks.ensure_all()
    assert calls == []
    assert all(empty["checks"][kind]["reason"] == "configuration_unavailable"
               for kind in fingerprints)
    fingerprints["model"] = "a" * 64
    await checks.ensure_all()
    await checks.wait("model")
    assert calls == ["model"]
    fingerprints["model"] = "b" * 64
    assert checks.read("model")["reason"] == "configuration_changed"
    await checks.ensure_all()
    await checks.wait("model")
    assert calls == ["model", "model"]
    assert checks.read("model")["fingerprint"] == "b" * 64


@pytest.mark.asyncio
async def test_rejection_timeout_cancellation_and_expiry_are_distinct(tmp_path):
    from app.api.functional_checks import FunctionalChecks, ProbeFailure

    now = datetime(2026, 10, 7, tzinfo=UTC)
    fingerprint = ["a" * 64]

    async def reject(_kind):
        raise ProbeFailure("authentication_rejected")

    checks = FunctionalChecks(tmp_path, lambda _kind: fingerprint[0],
                              {"model": reject}, now=lambda: now,
                              secure_path=lambda _path: None)
    await checks.start("model", uuid4())
    await checks.wait("model")
    result = checks.read("model")
    assert result["state"] == "unavailable"
    assert result["reason"] == "authentication_rejected"
    assert result["fingerprint"] == fingerprint[0]
    assert datetime.fromisoformat(result["checked_at"])
    assert datetime.fromisoformat(result["expires_at"])
    fingerprint[0] = "b" * 64
    assert checks.read("model")["reason"] == "configuration_changed"
    fingerprint[0] = "a" * 64
    now += timedelta(hours=2)
    assert checks.read("model")["reason"] == "check_expired"

    async def never(_kind):
        await asyncio.Event().wait()

    timed = FunctionalChecks(tmp_path / "timeout", lambda _kind: "a" * 64,
                             {"model": never}, timeout=0.01,
                             secure_path=lambda _path: None)
    await timed.start("model", uuid4())
    await timed.wait("model")
    assert timed.read("model")["reason"] == "provider_timeout"
    blocked = FunctionalChecks(tmp_path / "cancel", lambda _kind: "a" * 64,
                               {"model": never}, secure_path=lambda _path: None)
    request_id = uuid4()
    await blocked.start("model", request_id)
    await blocked.cancel("model", request_id)
    assert blocked.read("model")["state"] == "not_checked"
    assert blocked.read("model")["reason"] == "cancelled"


@pytest.mark.asyncio
async def test_api_requires_explicit_cost_acceptance_and_cancel_is_idempotent(tmp_path):
    from app.api.functional_checks import FunctionalChecks

    calls = []
    release = asyncio.Event()

    async def probe(kind):
        calls.append(kind)
        await release.wait()

    app = create_app(Settings())
    app.state.functional_checks = FunctionalChecks(
        tmp_path, lambda _kind: "a" * 64, {"model": probe},
        secure_path=lambda _path: None,
    )
    async with AsyncClient(transport=ASGITransport(app),
                           base_url="http://127.0.0.1:5173",
                           headers={"Origin": "http://127.0.0.1:5173"}) as api:
        status = (await api.get("/api/status/functional")).json()
        assert status["checks"]["model"]["state"] == "not_checked"
        request_id = str(uuid4())
        url = "/api/status/functional/model"
        assert (await api.post(url, json={"request_id": request_id})).status_code == 422
        assert calls == []
        accepted = await api.post(url, json={"request_id": request_id, "accept_cost": True})
        assert accepted.status_code == 202
        await asyncio.sleep(0)
        assert calls == ["model"]
        duplicate = await api.post(url, json={"request_id": request_id, "accept_cost": True})
        assert duplicate.status_code == 202
        assert calls == ["model"]
        conflict = await api.post(url, json={"request_id": str(uuid4()), "accept_cost": True})
        assert conflict.status_code == 409
        cancelled = await api.post(url + "/cancel", json={"request_id": request_id})
        assert cancelled.status_code == 200
        assert cancelled.json()["state"] == "not_checked"
        assert calls == ["model"]


@pytest.mark.asyncio
async def test_auto_api_starts_one_bounded_batch_and_requires_cost_authorization(tmp_path):
    from app.api.functional_checks import FunctionalChecks

    calls = []

    async def probe(kind):
        calls.append(kind)

    app = create_app(Settings())
    checks = FunctionalChecks(
        tmp_path, lambda kind: None if kind == "knowledge" else "a" * 64,
        {kind: probe for kind in ("model", "asr", "tts")},
        secure_path=lambda _path: None,
    )
    app.state.functional_checks = checks
    async with AsyncClient(transport=ASGITransport(app),
                           base_url="http://127.0.0.1:5173",
                           headers={"Origin": "http://127.0.0.1:5173"}) as api:
        url = "/api/status/functional/auto"
        assert (await api.get("/api/status/functional")).status_code == 200
        assert calls == []
        assert (await api.post(url, json={})).status_code == 422
        first = await api.post(url, json={"accept_cost": True})
        assert first.status_code == 202
        assert first.json()["checks"]["knowledge"]["state"] == "not_checked"
        await asyncio.gather(*(checks.wait(kind) for kind in ("model", "asr", "tts")))
        cached = await api.post(url, json={"accept_cost": True})
        assert cached.status_code == 202
        assert cached.json()["checks"]["model"]["state"] == "available"
        assert sorted(calls) == ["asr", "model", "tts"]
        refreshed = await api.post(url, json={"accept_cost": True, "force": True})
        assert refreshed.status_code == 202
        await asyncio.gather(*(checks.wait(kind) for kind in ("model", "asr", "tts")))
        assert sorted(calls) == ["asr", "asr", "model", "model", "tts", "tts"]


@pytest.mark.asyncio
async def test_real_probe_adapters_are_bounded_with_synthetic_clients(monkeypatch):
    from types import SimpleNamespace

    from app.api.functional_checks import ProbeFailure
    from app.api.functional_probes import FunctionalProbes
    from app.providers.errors import ProviderError

    configured = Settings(voice_asr_key=SecretStr("synthetic-asr"),
                          voice_tts_key=SecretStr("synthetic-tts"))
    probes = FunctionalProbes(configured)
    before = probes.fingerprint("asr")
    changed = FunctionalProbes(configured.model_copy(update={
        "voice_asr_key": SecretStr("different-synthetic-asr"),
    }))
    assert before != changed.fingerprint("asr")
    assert "synthetic" not in str(before)

    calls = []

    class FakeClient:
        def __init__(self, _config, *, budget):
            assert budget.limit == 1

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def complete(self, model, messages, *, max_tokens):
            calls.append((model, len(messages), max_tokens))
            return SimpleNamespace(content="OK")

    monkeypatch.setattr("app.api.functional_probes.load_dashscope_config",
                        lambda _path: object())
    monkeypatch.setattr("app.api.functional_probes.DashScopeClient", FakeClient)
    await probes.model("model")
    assert calls == [("qwen-flash", 1, 16)]

    class RejectingClient(FakeClient):
        async def complete(self, *_args, **_kwargs):
            raise ProviderError("http", "dashscope", "qwen-flash", status=401)

    monkeypatch.setattr("app.api.functional_probes.DashScopeClient", RejectingClient)
    with pytest.raises(ProbeFailure, match="authentication_rejected"):
        await probes.model("model")

    class QuotaClient(FakeClient):
        async def complete(self, *_args, **_kwargs):
            raise ProviderError("http", "dashscope", "qwen-flash", status=429)

    monkeypatch.setattr("app.api.functional_probes.DashScopeClient", QuotaClient)
    with pytest.raises(ProbeFailure, match="quota_rejected"):
        await probes.model("model")

    class FakeTTS:
        def __init__(self, _key, _voice):
            pass

        async def stream(self, text):
            assert text == "你好"
            calls.append("tts")
            yield b"synthetic-audio"

    monkeypatch.setattr("app.api.functional_probes.MiniMaxTTS", FakeTTS)

    def fake_pcm(encoded):
        assert encoded == b"synthetic-audio"
        return b"\x00" * 3200

    monkeypatch.setattr(FunctionalProbes, "_pcm", staticmethod(fake_pcm))
    await probes.tts("tts")
    assert calls[-1] == "tts"


def test_tts_probe_rejects_non_audio_supplier_payload():
    from app.api.functional_checks import ProbeFailure
    from app.api.functional_probes import FunctionalProbes

    with pytest.raises(ProbeFailure, match="response_invalid"):
        FunctionalProbes._pcm(b"not-mp3")


@pytest.mark.asyncio
async def test_asr_probe_uses_one_synthetic_tts_clip_and_one_asr_stream(monkeypatch):
    from types import SimpleNamespace

    import av
    import numpy as np

    from app.api.functional_probes import FunctionalProbes

    probes = FunctionalProbes(Settings(voice_asr_key=SecretStr("fake-asr"),
                                       voice_tts_key=SecretStr("fake-tts")))
    calls = []

    class FakeTTS:
        def __init__(self, _key, _voice):
            calls.append("tts_init")

        async def stream(self, _text):
            calls.append("tts_stream")
            yield b"fake-mp3"

    class FakeDecoder:
        def feed(self, _encoded):
            return [object()]

        def finish(self):
            return []

    class FakeResampler:
        def __init__(self, **_kwargs):
            pass

        def resample(self, frame):
            return ([] if frame is None else [SimpleNamespace(
                to_ndarray=lambda: np.zeros(3200, dtype=np.int16),
            )])

    class FakeASR:
        def __init__(self, _key, _resource, *, app_key):
            calls.append("asr_init")

        async def recognize(self, audio):
            chunks = [chunk async for chunk in audio]
            assert sum(map(len, chunks)) == 6400
            calls.append("asr_recognize")
            yield "你好。", True

    monkeypatch.setattr("app.api.functional_probes.MiniMaxTTS", FakeTTS)
    monkeypatch.setattr("app.api.functional_probes.MP3Decoder", FakeDecoder)
    monkeypatch.setattr(av, "AudioResampler", FakeResampler)
    monkeypatch.setattr("app.api.functional_probes.VolcASR", FakeASR)
    await probes.asr("asr")
    assert calls == ["tts_init", "tts_stream", "asr_init", "asr_recognize"]


@pytest.mark.asyncio
async def test_knowledge_probe_stays_unchecked_until_same_database_readonly_path_exists(tmp_path):
    from app.api.functional_checks import FunctionalChecks, ProbeFailure
    from app.api.functional_probes import FunctionalProbes

    probes = FunctionalProbes(Settings())
    assert probes.fingerprint("knowledge") is None
    checks = FunctionalChecks(tmp_path, probes.fingerprint, {"knowledge": probes.knowledge},
                              secure_path=lambda _path: None)
    assert checks.read("knowledge") == {
        "service": "LightRAG retrieval and citation check", "state": "not_checked",
        "reason": "acceptance_kb_readonly_probe_unavailable", "checked_at": None,
        "expires_at": None, "fingerprint": None,
    }
    with pytest.raises(ProbeFailure, match="acceptance_kb_readonly_probe_unavailable"):
        await probes.knowledge("knowledge")


@pytest.mark.asyncio
async def test_shutdown_cancels_inflight_supplier_check(tmp_path):
    from app.api.functional_checks import FunctionalChecks

    entered = asyncio.Event()

    async def probe(_kind):
        entered.set()
        await asyncio.Event().wait()

    checks = FunctionalChecks(tmp_path, lambda _kind: "a" * 64,
                              {"model": probe}, secure_path=lambda _path: None)
    await checks.start("model", uuid4())
    await entered.wait()
    await checks.close()
    assert checks.read("model")["reason"] == "cancelled"


@pytest.mark.asyncio
async def test_local_status_uses_real_functional_evidence_only(tmp_path):
    from app.api.functional_checks import FunctionalChecks

    async def probe(_kind):
        return None

    app = create_app(Settings())
    checks = FunctionalChecks(tmp_path, lambda _kind: "a" * 64,
                              {"model": probe, "asr": probe, "tts": probe},
                              secure_path=lambda _path: None)
    app.state.functional_checks = checks
    for kind in ("model", "asr", "tts"):
        await checks.start(kind, uuid4())
        await checks.wait(kind)
    async with AsyncClient(transport=ASGITransport(app),
                           base_url="http://127.0.0.1:5173",
                           headers={"Origin": "http://127.0.0.1:5173"}) as api:
        report = (await api.post("/api/status/check")).json()
    assert report["checks"]["model_provider"]["state"] == "available"
    assert report["checks"]["speech_providers"]["state"] == "available"
    assert report["checks"]["knowledge_engine"]["state"] == "not_checked"
