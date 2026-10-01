"""Embedded worker ownership, binding validation and the controlled AnswerService bridge."""

import asyncio
import json
from contextlib import suppress
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select

from app.models import AnswerAttempt, Conversation, KnowledgeBase, LocalProfile
from app.rag.answer_adapter import LightRAGAnswerAdapter
from app.rag.query_adapter import LightRAGQueryAdapter
from app.services.answers import AnswerService
from app.services.conversation_retention import active_conversation
from app.services.errors import ServiceError
from app.voice.sessions import Binding, VoiceSession, VoiceSessions


def assistant_configured(settings) -> bool:
    from importlib.util import find_spec

    from app.voice.transport import transport_status

    return bool(settings.voice_assistant_enabled and settings.answer_enabled
                and transport_status(settings)["transport"] == "configured"
                and settings.voice_asr_key and settings.voice_tts_key
                and settings.voice_vad_model and settings.voice_vad_model.is_file()
                and all(find_spec(name) for name in ("livekit.rtc", "av", "onnxruntime")))


class VoiceRuntime:
    def __init__(self, app, *, worker_factory=None):
        self.app = app
        self.registry = VoiceSessions()
        self.worker_factory = worker_factory
        self.workers = {}
        self.monitor = None
        self.api = None
        self.clean = False

    async def start(self):
        settings = self.app.state.settings
        if not settings.voice_assistant_enabled:
            return
        from app.voice.transport import transport_status

        if transport_status(settings)["transport"] != "configured":
            return
        from aiohttp import ClientTimeout
        from livekit import api

        if not settings.livekit_api_key or not settings.livekit_api_secret:
            return
        self.api = api.LiveKitAPI(
            url=settings.livekit_url.replace("ws", "http", 1),
            api_key=settings.livekit_api_key.get_secret_value(),
            api_secret=settings.livekit_api_secret.get_secret_value(),
            timeout=ClientTimeout(total=8),
        )
        try:
            async with self.app.state.database.sessions() as session:
                owner = await session.scalar(select(LocalProfile.id))
            rooms = await self.api.room.list_rooms(api.ListRoomsRequest())
            for room in rooms.rooms:
                try:
                    metadata = json.loads(room.metadata or "{}")
                except ValueError:
                    continue
                if (room.name.startswith("citerag-voice-")
                    and metadata.get("citerag_voice_installation") == str(owner)):
                    await self.api.room.delete_room(api.DeleteRoomRequest(room=room.name))
            self.clean = True
        except Exception:
            # No unidentified room is touched and no provider/SDK exception is logged.
            self.clean = False
        self.monitor = asyncio.create_task(self._monitor(), name="voice-lease-monitor")

    async def binding(self, session, owner: UUID, conversation: UUID, *, lock=False) -> Binding:
        query = select(Conversation).where(Conversation.id == conversation,
                                          Conversation.owner_id == owner, active_conversation())
        chat = await session.scalar(query.with_for_update() if lock else query)
        if chat is None:
            raise ServiceError(404, "conversation_not_found", "聊天不存在或不可访问")
        if chat.archived_at is not None:
            raise ServiceError(409, "conversation_archived", "聊天已归档，请恢复后再通话")
        if chat.kb_id is None:
            return Binding(owner, conversation, None, 0, "ordinary")
        query = select(KnowledgeBase).where(KnowledgeBase.id == chat.kb_id,
                                           KnowledgeBase.owner_id == owner)
        kb = await session.scalar(query.with_for_update() if lock else query)
        if kb is None or kb.status != "ready":
            raise ServiceError(409, "kb_not_ready", "知识库未就绪，通话已暂停")
        return Binding(owner, conversation, kb.id, kb.revision, kb.active_workspace)

    async def check(self, call: VoiceSession, generation: int | None = None):
        self.app.state.owner.assert_owned()
        if not self.registry.current(call, call.generation if generation is None else generation):
            raise ServiceError(409, "voice_lease_expired", "通话租约或轮次已失效")
        async with self.app.state.database.sessions() as session:
            current = await self.binding(session, call.binding.owner, call.binding.conversation)
            if current != call.binding:
                raise ServiceError(409, "kb_changed", "资料修订或活动空间已变化，请重新开始")

    def token(self, call: VoiceSession, *, worker=False):
        from livekit import api

        settings = self.app.state.settings
        return (api.AccessToken(settings.livekit_api_key.get_secret_value(),
                                settings.livekit_api_secret.get_secret_value())
                .with_identity(call.assistant_identity if worker else call.identity)
                .with_ttl(timedelta(seconds=90))
                .with_grants(api.VideoGrants(
                    room_join=True, room=call.room, can_publish=True, can_subscribe=True,
                    can_publish_data=False, can_update_own_metadata=False,
                    can_publish_sources=["microphone"],
                )).to_jwt())

    def details(self, call):
        return {"session_id": str(call.id), "control_token": call.control,
                "conversation_id": str(call.binding.conversation), "room": call.room,
                "server_url": self.app.state.settings.livekit_url, "token": self.token(call),
                "assistant_identity": call.assistant_identity, "assistant": "starting",
                "purpose": "voice_assistant", "lease_seconds": self.registry.LEASE_SECONDS,
                "generation": call.generation}

    async def begin(self, session, owner, conversation, start_id):
        if not assistant_configured(self.app.state.settings):
            raise ServiceError(503, "voice_assistant_not_configured",
                               "语音助手未启用或 ASR/TTS/VAD/问答配置缺失，请使用文字问答")
        if not self.clean:
            raise ServiceError(503, "voice_cleanup_unavailable",
                               "本地房间回收未通过，请检查 LiveKit 后重启 API")
        binding = await self.binding(session, owner, conversation, lock=True)
        active = await session.scalar(select(AnswerAttempt.id).where(
            AnswerAttempt.conversation_id == conversation,
            AnswerAttempt.status.in_(("running", "waiting_input", "waiting_approval")),
        ).limit(1))
        if active:
            raise ServiceError(409, "answer_running", "请先结束当前回答")
        previous = self.registry.active(conversation)
        call = self.registry.create(binding, start_id)
        await session.commit()
        if previous is None:
            try:
                if self.worker_factory is None:
                    from app.voice.worker import LiveKitWorker

                    worker = LiveKitWorker(self, call)
                else:
                    worker = self.worker_factory(self, call)
                self.workers[call.id] = worker
                async def dispose():
                    await worker.close()
                    self.workers.pop(call.id, None)
                call.dispose_media = dispose
                from livekit import api

                await self.api.room.create_room(api.CreateRoomRequest(
                    name=call.room, empty_timeout=30, max_participants=2,
                    metadata=json.dumps({"citerag_voice_installation": str(owner)}),
                ))
                call.worker_task = asyncio.create_task(worker.run(), name="voice-livekit-worker")
            except Exception:
                await self.registry.close_call(call, "worker_start_failed")
                raise ServiceError(503, "voice_worker_failed",
                                   "语音 worker 未能启动，请检查本地模型与媒体配置") from None
        return self.details(call)

    async def answer(self, call, request_id, text, generation, on_general_segment=None):
        await self.check(call, generation)
        if self.app.state.agent_enabled and self.app.state.agent_runtime is not None:
            return await self.agent_answer(call, request_id, text, generation)
        gate = self.app.state.backup_gate
        await gate.enter()
        try:
            async with self.app.state.database.sessions() as session:
                runtime = self.app.state.rag_runtime
                retriever = self.app.state.query_adapter or LightRAGQueryAdapter(runtime)
                answerer = self.app.state.answer_adapter or LightRAGAnswerAdapter(runtime)
                def admitted(_conversation):
                    if not self.registry.current(call, generation):
                        raise ServiceError(409, "voice_interrupted", "语音轮次已中止")

                result = await AnswerService(session, admission=admitted,
                    commit_allowed=lambda: self.registry.current(call, generation),
                    expected_binding=(call.binding.revision, call.binding.workspace)).ask(
                    call.binding.owner, call.binding.conversation, request_id, text,
                    retriever, answerer, mode="auto",
                    on_general_segment=on_general_segment,
                )
            await self.check(call, generation)
            return result
        finally:
            await gate.leave()

    async def agent_answer(self, call, request_id, text, generation):
        """Final transcripts share the durable runner; approval is never inferred from ASR."""
        runner = self.app.state.agent_runtime
        run = await runner.start(call.binding.owner, call.binding.conversation,
                                 request_id, text, voice=call, voice_generation=generation)
        run_id, last_seq = run["id"], -1
        try:
            while True:
                await self.check(call, generation)
                state = await runner.get(call.binding.owner, call.binding.conversation, run_id)
                if state["seq"] != last_seq:
                    last_seq = state["seq"]
                    self.registry.emit(call, "agent", run=state)
                    if state["status"] in {"waiting_input", "waiting_approval"}:
                        call.phase = state["status"]
                        self.registry.emit(call, "phase", phase=call.phase)
                    elif state["status"] == "running" and call.phase != "generating":
                        call.phase = "generating"
                        self.registry.emit(call, "phase", phase=call.phase)
                if state["status"] not in {"running", "waiting_input", "waiting_approval"}:
                    async with self.app.state.database.sessions() as session:
                        from app.models import ConversationMessage
                        message = await session.get(ConversationMessage, state["message_id"])
                        attempt = await session.get(AnswerAttempt, state["attempt_id"])
                        view = await AnswerService(session)._view(message, attempt)
                    await self.check(call, generation)
                    return view
                await asyncio.sleep(0.1)
        except (asyncio.CancelledError, ServiceError):
            # Stop real model/tool work in addition to VoiceSessions' audio flush.
            await runner.cancel(call.binding.owner, call.binding.conversation, run_id,
                                code="voice_interrupted", internal=True)
            raise

    async def reconcile(self):
        for call in list(self.registry.calls.values()):
            if call.closed:
                continue
            try:
                await self.check(call)
            except Exception:
                await self.registry.close_call(call, "binding_or_lease_invalid")

    async def _monitor(self):
        while True:
            await asyncio.sleep(0.25)
            try:
                await self.reconcile()
            except Exception:
                # Cleanup failures keep the controller lease blocked, fail closed.
                self.clean = False

    async def close(self):
        if self.monitor:
            self.monitor.cancel()
            with suppress(asyncio.CancelledError):
                await self.monitor
        try:
            await self.registry.close()
        finally:
            if self.api:
                await self.api.aclose()
