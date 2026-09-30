"""A final transcript creates one ordinary durable AnswerService input."""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress

from app.services.errors import ServiceError
from app.voice.providers import SpeechError
from app.voice.sessions import VoiceSession, VoiceSessions


class VoiceTurns:
    def __init__(self, registry: VoiceSessions, call: VoiceSession,
                 answer: Callable[..., Awaitable[dict]],
                 play: Callable[[str, int], Awaitable[None]], *,
                 play_stream: Callable[[AsyncIterator[str], int], Awaitable[None]] | None = None):
        self.registry, self.call, self.answer, self.play = registry, call, answer, play
        self.play_stream = play_stream
        self.lock = asyncio.Lock()

    async def submit(self, utterance: int, text: str, revision: int = 1):
        async with self.lock:
            call, registry = self.call, self.registry
            accepted = registry.transcript(call, utterance, text, revision)
            if accepted is None:
                return
            previous = call.turn_task
            registry.interrupt(call, "transcript_corrected" if revision > 1 else "new_utterance")
            if previous:
                with suppress(asyncio.CancelledError):
                    await previous
            if not registry.current(call, call.generation):
                return
            request_id, text = accepted
            call.turn_started_at = time.monotonic()
            registry.emit(call, "transcript", text=text, final=True,
                          utterance=utterance, revision=revision, request_id=str(request_id))
            generation = call.generation

            async def produce():
                call.phase = "generating"
                registry.emit(call, "phase", phase=call.phase)
                segments: asyncio.Queue[str | None] = asyncio.Queue()
                playback: asyncio.Task | None = None
                first_segment = True

                async def queued_segments() -> AsyncIterator[str]:
                    while (segment := await segments.get()) is not None:
                        yield segment

                async def on_segment(segment: str) -> None:
                    nonlocal playback, first_segment
                    if not registry.current(call, generation):
                        raise ServiceError(409, "voice_interrupted", "语音轮次已中止")
                    if first_segment and call.turn_started_at is not None:
                        elapsed_ms = round((time.monotonic() - call.turn_started_at) * 1000)
                        registry.emit(call, "timing", metric="first_text",
                                      elapsed_ms=elapsed_ms)
                        first_segment = False
                    segments.put_nowait(segment)
                    if playback is None and self.play_stream is not None:
                        call.phase = "speaking"
                        registry.emit(call, "phase", phase=call.phase)
                        playback = asyncio.create_task(
                            self.play_stream(queued_segments(), generation),
                            name="voice-stream-playback",
                        )
                try:
                    if self.play_stream is None:
                        view = await self.answer(call, request_id, text, generation)
                    else:
                        view = await self.answer(call, request_id, text, generation, on_segment)
                    if not registry.current(call, generation):
                        return
                    if not view.get("saved") or view.get("status") == "running":
                        raise ServiceError(409, "answer_not_saved", "回答尚未确认保存")
                    if playback is not None:
                        segments.put_nowait(None)
                        try:
                            await playback
                        except SpeechError:
                            registry.emit(call, "answer", answer=view)
                            raise
                        if not registry.current(call, generation):
                            return
                        registry.emit(call, "answer", answer=view)
                    else:
                        registry.emit(call, "answer", answer=view)
                    if playback is None and view["status"] == "answered" and view.get("text"):
                        call.phase = "speaking"
                        registry.emit(call, "phase", phase=call.phase)
                        await self.play(view["text"], generation)
                    if not registry.current(call, generation):
                        return
                    if registry.current(call, generation):
                        call.phase = "listening"
                        registry.emit(call, "phase", phase=call.phase)
                except asyncio.CancelledError:
                    raise
                except (SpeechError, ServiceError) as error:
                    if registry.current(call, generation):
                        code = str(error) if isinstance(error, SpeechError) else error.code
                        registry.emit(call, "error", code=code,
                                      text_available=isinstance(error, SpeechError))
                        call.phase = "listening"
                except Exception:
                    if registry.current(call, generation):
                        call.phase = "listening"
                        registry.emit(call, "error", code="voice_turn_failed", text_available=False)
                finally:
                    if playback is not None and not playback.done():
                        playback.cancel()
                        with suppress(asyncio.CancelledError):
                            await playback

            call.turn_task = asyncio.create_task(produce(), name="voice-answer-turn")
