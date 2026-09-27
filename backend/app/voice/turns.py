"""A final transcript creates one ordinary durable AnswerService input."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from uuid import UUID

from app.services.errors import ServiceError
from app.voice.providers import SpeechError
from app.voice.sessions import VoiceSession, VoiceSessions


class VoiceTurns:
    def __init__(self, registry: VoiceSessions, call: VoiceSession,
                 answer: Callable[[VoiceSession, UUID, str, int], Awaitable[dict]],
                 play: Callable[[str, int], Awaitable[None]]):
        self.registry, self.call, self.answer, self.play = registry, call, answer, play
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
            registry.emit(call, "transcript", text=text, final=True,
                          utterance=utterance, revision=revision, request_id=str(request_id))
            generation = call.generation

            async def produce():
                call.phase = "generating"
                registry.emit(call, "phase", phase=call.phase)
                try:
                    view = await self.answer(call, request_id, text, generation)
                    if not registry.current(call, generation):
                        return
                    if not view.get("saved") or view.get("status") == "running":
                        raise ServiceError(409, "answer_not_saved", "回答尚未确认保存")
                    registry.emit(call, "answer", answer=view)
                    if view["status"] == "answered" and view.get("text"):
                        call.phase = "speaking"
                        registry.emit(call, "phase", phase=call.phase)
                        await self.play(view["text"], generation)
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

            call.turn_task = asyncio.create_task(produce(), name="voice-answer-turn")
