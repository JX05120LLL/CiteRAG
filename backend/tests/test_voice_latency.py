"""Synthetic streaming voice regressions; never contacts a speech provider."""

import asyncio
import io
from types import SimpleNamespace
from uuid import uuid4

import av
import numpy as np
import pytest

from app.rag.answer_adapter import AnswerError
from app.rag.runtime import RagRuntime
from app.services import answers as answers_module
from app.services.answers import AnswerService
from app.services.conversation_context import ORDINARY_WORKSPACE
from app.services.token_budget import TokenBudgetExceeded
from app.voice.providers import SpeechError
from app.voice.sessions import Binding, VoiceSessions
from app.voice.turns import VoiceTurns
from app.voice.worker import LiveKitWorker


@pytest.mark.asyncio
async def test_ordinary_speech_starts_before_answer_is_saved():
    registry = VoiceSessions()
    call = registry.create(Binding(uuid4(), uuid4(), uuid4(), 1, "space"), uuid4())
    allow_finish = asyncio.Event()
    heard = []

    async def answer(_call, _request, _text, _generation, on_segment):
        await on_segment("第一句。")
        await allow_finish.wait()
        return {"saved": True, "status": "answered", "text": "第一句。第二句。"}

    async def play(_text, _generation):
        raise AssertionError("streaming ordinary answer must not replay the full answer")

    async def play_stream(segments, _generation):
        async for segment in segments:
            heard.append(segment)

    turns = VoiceTurns(registry, call, answer, play, play_stream=play_stream)
    await turns.submit(1, "你好")
    await asyncio.wait_for(_until(lambda: heard), 1)
    assert heard == ["第一句。"]
    assert not any(event["type"] == "answer" for event in call.events)
    allow_finish.set()
    await asyncio.wait_for(call.turn_task, 1)
    assert [event["type"] for event in call.events].count("answer") == 1


@pytest.mark.asyncio
async def test_ordinary_stream_is_cancelled_by_new_turn_without_old_audio():
    registry = VoiceSessions()
    call = registry.create(Binding(uuid4(), uuid4(), uuid4(), 1, "space"), uuid4())
    audio_started = asyncio.Event()
    cancelled = []

    async def answer(_call, _request, text, _generation, on_segment):
        await on_segment(text)
        if text == "old":
            await asyncio.Event().wait()
        return {"saved": True, "status": "answered", "text": text}

    async def play(_text, _generation):
        raise AssertionError("not expected")

    async def play_stream(segments, generation):
        try:
            async for segment in segments:
                if generation == 1:
                    audio_started.set()
                    await asyncio.Event().wait()
                else:
                    assert segment == "new"
        except asyncio.CancelledError:
            cancelled.append(generation)
            raise

    turns = VoiceTurns(registry, call, answer, play, play_stream=play_stream)
    await turns.submit(1, "old")
    await asyncio.wait_for(audio_started.wait(), 1)
    await turns.submit(2, "new")
    await asyncio.wait_for(call.turn_task, 1)
    assert 1 in cancelled
    assert not any(event["type"] == "answer" and event["answer"]["text"] == "old"
                   for event in call.events)


@pytest.mark.asyncio
async def test_streamed_tts_failure_keeps_the_saved_text_fallback():
    registry = VoiceSessions()
    call = registry.create(Binding(uuid4(), uuid4(), uuid4(), 1, "space"), uuid4())

    async def answer(_call, _request, _text, _generation, on_segment):
        await on_segment("已保存的文字。")
        return {"saved": True, "status": "answered", "text": "已保存的文字。"}

    async def play_stream(segments, _generation):
        async for _ in segments:
            raise SpeechError("tts_unavailable")

    turns = VoiceTurns(registry, call, answer, lambda *_: None, play_stream=play_stream)
    await turns.submit(1, "你好")
    await asyncio.wait_for(call.turn_task, 1)
    answers = [event for event in call.events if event["type"] == "answer"]
    errors = [event for event in call.events if event["type"] == "error"]
    assert len(answers) == 1 and answers[0]["answer"]["text"] == "已保存的文字。"
    assert errors[-1]["code"] == "tts_unavailable" and errors[-1]["text_available"]


@pytest.mark.asyncio
async def test_segmented_tts_uses_one_track_and_emits_text_with_audio(monkeypatch):
    import livekit

    buffer = io.BytesIO()
    with av.open(buffer, mode="w", format="mp3") as container:
        stream = container.add_stream("libmp3lame", rate=24000)
        stream.layout = "mono"
        frame = av.AudioFrame.from_ndarray(np.zeros((1, 2400), dtype=np.float32),
                                           format="fltp", layout="mono")
        frame.sample_rate = 24000
        for packet in (*stream.encode(frame), *stream.encode(None)):
            container.mux(packet)
    encoded = buffer.getvalue()
    frames, requests, publications = [], [], []

    class Source:
        def __init__(self, *_args, **_kwargs):
            pass

        async def capture_frame(self, frame):
            frames.append(frame)

        async def wait_for_playout(self):
            pass

        def clear_queue(self):
            pass

        async def aclose(self):
            pass

    class Participant:
        async def publish_track(self, _track, _options):
            publications.append("published")
            return SimpleNamespace(sid="track")

        async def unpublish_track(self, _sid):
            publications.append("unpublished")

    rtc = SimpleNamespace(
        AudioSource=Source,
        LocalAudioTrack=SimpleNamespace(create_audio_track=lambda *_: object()),
        TrackPublishOptions=lambda **_kwargs: object(),
        TrackSource=SimpleNamespace(SOURCE_MICROPHONE="microphone"),
        AudioFrame=lambda data, rate, channels, samples: (data, rate, channels, samples),
    )
    monkeypatch.setattr(livekit, "rtc", rtc, raising=False)
    registry = VoiceSessions()
    call = registry.create(Binding(uuid4(), uuid4(), uuid4(), 1, "space"), uuid4())

    async def check(_call, _generation):
        pass

    class TTS:
        async def stream(self, text):
            requests.append(text)
            for offset in range(0, len(encoded), 101):
                yield encoded[offset:offset + 101]

    worker = object.__new__(LiveKitWorker)
    worker.registry, worker.call = registry, call
    worker.runtime = SimpleNamespace(check=check)
    worker.room = SimpleNamespace(local_participant=Participant())
    worker.tts = TTS()
    worker.source = worker.publication = None

    async def segments():
        yield "第一句。"
        yield "第二句。"

    await worker.play_stream(segments(), call.generation)
    assert requests == ["第一句。", "第二句。"]
    assert publications == ["published", "unpublished"]
    assert frames
    assert [event["text"] for event in call.events if event["type"] == "speech_text"] == requests


async def _until(predicate):
    while not predicate():
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_general_stream_sends_stable_phrases_before_model_completion():
    first = asyncio.Event()
    finish = asyncio.Event()
    heard = []

    class Answerer:
        async def stream_general(self, _question, _context):
            yield "你好，今天"
            yield "可以聊聊这个问题。"
            first.set()
            await finish.wait()
            yield "我们继续。"

    async def segment(text):
        heard.append(text)

    async def resolve():
        return await AnswerService(None)._resolve(
            uuid4(), "space", "你好", None, Answerer(), "auto",
            {"mode": "general"}, {}, on_general_segment=segment,
        )

    task = asyncio.create_task(resolve())
    await asyncio.wait_for(first.wait(), 1)
    assert heard == ["你好，今天可以聊聊这个问题。"]
    finish.set()
    assert await task == ("answered", "你好，今天可以聊聊这个问题。我们继续。", [])
    assert heard == ["你好，今天可以聊聊这个问题。", "我们继续。"]


@pytest.mark.asyncio
async def test_general_stream_rejects_url_split_across_tokens():
    class Answerer:
        async def stream_general(self, _question, _context):
            yield "参考 http"
            yield "s://example.com"

    with pytest.raises(AnswerError, match="answer_format_invalid"):
        await AnswerService(None)._resolve(
            uuid4(), "space", "你好", None, Answerer(), "auto",
            {"mode": "general"}, {}, on_general_segment=lambda _text: None,
        )


@pytest.mark.asyncio
async def test_ordinary_voice_stream_keeps_own_summary_without_kb(monkeypatch):
    owner, conversation_id = uuid4(), uuid4()
    chat = SimpleNamespace(kb_id=None)
    attempt = SimpleNamespace(
        id=uuid4(), kb_revision=0, workspace=ORDINARY_WORKSPACE,
        status="running", text=None, citations=[], error_code=None,
    )
    message = SimpleNamespace(content="继续说说", query_filter=None)
    heard, contexts = [], []

    class Session:
        async def scalar(self, _query):
            return chat

        async def get(self, _model, _id, **_kwargs):
            return attempt

        async def commit(self):
            pass

    class Answerer:
        async def stream_general(self, _question, context):
            contexts.append(context)
            yield "你好，继续聊。"

    async def context(*_args):
        return {"summary": "我喜欢简洁回答", "turns": []}

    async def segment(text):
        heard.append(text)

    async def view(_message, current):
        return {"status": current.status, "text": current.text,
                "citations": current.citations}

    monkeypatch.setattr(answers_module, "prepare_context", context)
    service = AnswerService(Session())
    monkeypatch.setattr(service, "_view", view)
    result = await service._finish_attempt(
        owner, conversation_id, None, message, attempt, None, Answerer(),
        "auto", None, on_general_segment=segment,
    )
    assert result == {"status": "answered", "text": "你好，继续聊。", "citations": []}
    assert heard == ["你好，继续聊。"]
    assert contexts[0]["summary"] == "我喜欢简洁回答"


@pytest.mark.asyncio
async def test_general_speech_budget_stops_before_provider(monkeypatch):
    runtime = object.__new__(RagRuntime)

    async def provider():
        raise AssertionError("oversize input must not reach the provider")

    monkeypatch.setattr(runtime, "_get_client", provider)
    with pytest.raises(TokenBudgetExceeded):
        async for _piece in runtime.stream_general("x" * 10000, {}):
            pass
