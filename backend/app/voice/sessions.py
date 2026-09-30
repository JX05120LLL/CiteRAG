"""Single-owner leases. Durable messages remain exclusively in AnswerService."""

import asyncio
import hmac
import secrets
import time
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from uuid import UUID, uuid4, uuid5

from app.services.errors import ServiceError


@dataclass(frozen=True)
class Binding:
    owner: UUID
    conversation: UUID
    kb: UUID | None
    revision: int
    workspace: str


@dataclass
class VoiceSession:
    id: UUID
    binding: Binding
    start_id: UUID
    control: str
    deadline: float
    max_deadline: float
    room: str
    identity: str
    assistant_identity: str
    generation: int = 0
    seq: int = 0
    phase: str = "connecting"
    closed: bool = False
    closing: asyncio.Task | None = None
    worker_task: asyncio.Task | None = None
    turn_task: asyncio.Task | None = None
    flush_audio: Callable[[], None] = lambda: None
    dispose_media: Callable[[], Awaitable[None]] | None = None
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    events: deque = field(default_factory=lambda: deque(maxlen=100))
    transcripts: dict[tuple[int, int], str] = field(default_factory=dict)
    last_utterance: int = 0
    last_revision: int = 0


class VoiceSessions:
    LEASE_SECONDS = 40

    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.calls: dict[UUID, VoiceSession] = {}

    def active(self, conversation: UUID) -> VoiceSession | None:
        # Expired but not yet revoked media still blocks text until cleanup completes.
        return next((call for call in self.calls.values()
                     if not call.closed and call.binding.conversation == conversation), None)

    def require_text(self, conversation: UUID) -> None:
        if self.active(conversation):
            raise ServiceError(409, "voice_active", "请先挂断当前聊天的通话，再发送文字或重试")

    def create(self, binding: Binding, start_id: UUID) -> VoiceSession:
        active = self.active(binding.conversation)
        if active:
            if active.start_id == start_id and active.binding == binding:
                return active
            raise ServiceError(409, "voice_active", "此聊天已由一个标签页控制通话，请先挂断")
        if sum(not call.closed for call in self.calls.values()) >= 4:
            raise ServiceError(409, "voice_capacity", "本机通话处理已满，请先挂断已有通话")
        for identifier in list(self.calls):
            if self.calls[identifier].closed:
                del self.calls[identifier]
        identifier = uuid4()
        call = VoiceSession(identifier, binding, start_id, secrets.token_urlsafe(32),
                            self.clock() + self.LEASE_SECONDS, self.clock() + 2700,
                            "citerag-voice-" + identifier.hex,
                            "voice-client-" + uuid4().hex, "voice-worker-" + identifier.hex)
        self.calls[identifier] = call
        return call

    def authorize(self, identifier: UUID, owner: UUID, control: str) -> VoiceSession:
        call = self.calls.get(identifier)
        if call is None or call.binding.owner != owner:
            raise ServiceError(404, "voice_session_missing", "通话已失效，请重新开始")
        if not hmac.compare_digest(call.control, control):
            raise ServiceError(403, "voice_control_forbidden", "此标签页不是当前通话控制端")
        return call

    def current(self, call: VoiceSession, generation: int) -> bool:
        return (not call.closed and call.closing is None and call.generation == generation
                and self.clock() < min(call.deadline, call.max_deadline))

    def renew(self, call: VoiceSession) -> None:
        if not self.current(call, call.generation):
            raise ServiceError(409, "voice_lease_expired", "通话租约已失效，请重新开始")
        call.deadline = min(self.clock() + self.LEASE_SECONDS, call.max_deadline)

    def emit(self, call: VoiceSession, kind: str, **value) -> None:
        call.seq += 1
        call.events.append({"seq": call.seq, "type": kind, "generation": call.generation,
                            "session_id": str(call.id), **value})
        call.wake.set()

    def interrupt(self, call: VoiceSession, reason: str) -> None:
        call.generation += 1
        call.flush_audio()
        if call.turn_task and not call.turn_task.done():
            call.turn_task.cancel()
        self.emit(call, "interrupted", reason=reason)

    def transcript(self, call: VoiceSession, utterance: int, text: str,
                   revision: int = 1) -> tuple[UUID, str] | None:
        if (not self.current(call, call.generation) or utterance < 1 or revision < 1
            or utterance < call.last_utterance or len(call.transcripts) >= 200):
            return None
        key = utterance, revision
        if utterance == call.last_utterance and revision < call.last_revision:
            return None
        if key in call.transcripts:
            if call.transcripts[key] != text:
                raise ServiceError(409, "transcript_conflict", "已确认的转写不能被无痕覆盖")
            return None
        if utterance == call.last_utterance and revision <= call.last_revision:
            return None
        if not text.strip() or len(text) > 1000 or any(
            ord(char) < 32 and char not in "\n\t" for char in text
        ):
            raise ServiceError(422, "transcript_invalid", "转写为空、过长或格式不符合要求")
        call.transcripts[key] = text
        call.last_utterance, call.last_revision = utterance, revision
        return uuid5(call.id, f"utterance:{utterance}:revision:{revision}"), text

    def expired(self) -> list[VoiceSession]:
        return [call for call in self.calls.values()
                if not call.closed and not self.current(call, call.generation)]

    async def close_call(self, call: VoiceSession, reason: str) -> None:
        if call.closing:
            if not call.closing.done() or call.closing.exception() is None:
                await asyncio.shield(call.closing)
                return
            call.closing = None
        if call.closed:
            return
        self.interrupt(call, reason)
        call.phase = "ending"

        async def cleanup():
            tasks = [task for task in (call.turn_task, call.worker_task)
                     if task and task is not asyncio.current_task()]
            for task in tasks:
                task.cancel()
            done, pending = await asyncio.wait(tasks, timeout=5) if tasks else (set(), set())
            for task in done:
                # Failed tasks must not prevent actual room/audio revocation.
                with suppress(asyncio.CancelledError, Exception):
                    await task
            if call.dispose_media:
                await call.dispose_media()
            if pending:
                raise ServiceError(503, "voice_cleanup_failed",
                                   "旧轮次尚未退出，媒体已撤销，请再次挂断")
            # A closed idempotency tombstone must not retain the worker or raw audio queues.
            call.flush_audio = lambda: None
            call.dispose_media = None
            call.worker_task = call.turn_task = None
            call.transcripts.clear()
            call.closed = True
            call.phase = "ended"
            self.emit(call, "ended", reason=reason)

        call.closing = asyncio.create_task(cleanup())
        await asyncio.shield(call.closing)

    async def close(self):
        for call in list(self.calls.values()):
            await self.close_call(call, "server_stopped")
