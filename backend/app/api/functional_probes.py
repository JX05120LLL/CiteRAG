"""Small explicit provider requests; no probe runs while building this module."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from pathlib import Path

from app.api.functional_checks import FunctionalChecks, Kind, ProbeFailure
from app.config import Settings
from app.credentials import CredentialError, load_dashscope_config
from app.providers.dashscope import DashScopeClient
from app.providers.errors import ProviderError
from app.providers.types import Message, RequestBudget
from app.rag.runtime import MODEL_CREDENTIAL_RECORD
from app.validation import model_fingerprint
from app.voice.audio import MP3Decoder
from app.voice.providers import MiniMaxTTS, SpeechError, VolcASR

_TTS_TEXT = "你好"


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode("utf-8")).hexdigest()


class FunctionalProbes:
    def __init__(self, settings: Settings):
        self.settings = settings

    def fingerprint(self, kind: Kind) -> str | None:
        if kind == "model":
            try:
                config = load_dashscope_config(MODEL_CREDENTIAL_RECORD)
                return model_fingerprint(config, config.credential_ciphertext_sha256)
            except (CredentialError, OSError, ValueError):
                return None
        if kind in {"asr", "tts"}:
            tts = self.settings.voice_tts_key
            asr = self.settings.voice_asr_key
            if not tts or (kind == "asr" and not asr):
                return None
            values: dict[str, object] = {
                "schema": 1, "kind": kind, "tts_voice": self.settings.voice_tts_voice,
                "tts_key_hash": hashlib.sha256(tts.get_secret_value().encode()).hexdigest(),
            }
            if kind == "asr":
                values["asr_resource"] = self.settings.voice_asr_resource
                values["asr_key_hash"] = hashlib.sha256(asr.get_secret_value().encode()).hexdigest()
                app_key = self.settings.voice_asr_app_key
                values["asr_app_key_hash"] = (hashlib.sha256(
                    app_key.get_secret_value().encode()).hexdigest() if app_key else None)
            return _digest(values)
        # The current LightRAG path initializes storage and requires a separately
        # configured engine database. A same-database, read-only acceptance-KB
        # retrieval path is not yet proven safe, so do not call it here.
        return None

    async def model(self, _kind: Kind) -> None:
        try:
            config = load_dashscope_config(MODEL_CREDENTIAL_RECORD)
            async with DashScopeClient(config, budget=RequestBudget(1)) as client:
                result = await client.complete(
                    "qwen-flash", [Message(role="user", content="只回复 OK")],
                    max_tokens=16,
                )
            if not result.content.strip():
                raise ProbeFailure("response_invalid")
        except ProviderError as error:
            if error.status in {401, 403}:
                raise ProbeFailure("authentication_rejected") from None
            if error.status in {402, 429}:
                raise ProbeFailure("quota_rejected") from None
            raise ProbeFailure("provider_unavailable") from None

    async def _tts_audio(self) -> bytes:
        key = self.settings.voice_tts_key
        if key is None:
            raise ProbeFailure("provider_unavailable")
        encoded = bytearray()
        try:
            async for chunk in MiniMaxTTS(key.get_secret_value(),
                                          self.settings.voice_tts_voice).stream(_TTS_TEXT):
                encoded.extend(chunk)
                if len(encoded) > 500_000:
                    raise ProbeFailure("response_invalid")
        except SpeechError as error:
            raise ProbeFailure(_speech_category(error)) from None
        if not encoded:
            raise ProbeFailure("response_invalid")
        return bytes(encoded)

    async def tts(self, _kind: Kind) -> None:
        self._pcm(await self._tts_audio())

    @staticmethod
    def _pcm(encoded: bytes) -> bytes:
        """Require decodable, short speech before declaring TTS usable."""
        import av

        try:
            decoder = MP3Decoder()
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            pcm = bytearray()
            for offset in range(0, len(encoded) + 4096, 4096):
                frames = (decoder.feed(encoded[offset:offset + 4096])
                          if offset < len(encoded) else decoder.finish())
                for frame in frames:
                    for mono in resampler.resample(frame):
                        pcm.extend(mono.to_ndarray().astype("<i2").tobytes())
                        if len(pcm) > 160_000:
                            raise ProbeFailure("response_invalid")
            for mono in resampler.resample(None):
                pcm.extend(mono.to_ndarray().astype("<i2").tobytes())
                if len(pcm) > 160_000:
                    raise ProbeFailure("response_invalid")
            if not 3_200 <= len(pcm) <= 160_000:
                raise ProbeFailure("response_invalid")
            return bytes(pcm)
        except ProbeFailure:
            raise
        except Exception:
            raise ProbeFailure("response_invalid") from None

    async def asr(self, _kind: Kind) -> None:
        key = self.settings.voice_asr_key
        if key is None:
            raise ProbeFailure("provider_unavailable")
        # A single TTS call supplies a deterministic spoken sample. No audio file,
        # user recording, or business document is stored or read.
        encoded = await self._tts_audio()
        try:
            pcm = self._pcm(encoded)

            async def audio() -> AsyncIterator[bytes]:
                for start in range(0, len(pcm), 3200):
                    yield pcm[start:start + 3200]

            app_key = self.settings.voice_asr_app_key
            recognizer = VolcASR(
                key.get_secret_value(), self.settings.voice_asr_resource,
                app_key=app_key.get_secret_value() if app_key else "",
            )
            async for text, final in recognizer.recognize(audio()):
                if final:
                    if "你好" not in text.replace(" ", ""):
                        raise ProbeFailure("response_invalid")
                    return
            raise ProbeFailure("response_invalid")
        except SpeechError as error:
            raise ProbeFailure(_speech_category(error)) from None

    async def knowledge(self, _kind: Kind) -> None:
        raise ProbeFailure("acceptance_kb_readonly_probe_unavailable")


def _speech_category(error: SpeechError) -> str:
    code = str(error)
    if code.endswith("auth_rejected"):
        return "authentication_rejected"
    if code.endswith("quota_rejected"):
        return "quota_rejected"
    return "provider_unavailable"


def build_functional_checks(settings: Settings, root: Path) -> FunctionalChecks:
    probes = FunctionalProbes(settings)
    return FunctionalChecks(root, probes.fingerprint, {
        "model": probes.model, "asr": probes.asr,
        "tts": probes.tts, "knowledge": probes.knowledge,
    }, timeout={"model": 55, "asr": 60, "tts": 35, "knowledge": 1})
