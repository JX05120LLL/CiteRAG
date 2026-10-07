"""Single API-owner runner, bounded tasks, lease renewal and explicit durable resumption."""

import asyncio
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator
from sqlalchemy import select, update

from app.agent.errors import AgentPaused
from app.agent.graph import GRAPH_VERSION, AgentError
from app.agent.planner import AgentAnswerer, drive
from app.agent.repository import ACTIVE, check_binding, emit, fenced, owned, renew, snapshot
from app.models import AgentRun, AnswerAttempt, ConversationMessage, KnowledgeBase, ToolCall
from app.rag.answer_adapter import AnswerError, LightRAGAnswerAdapter
from app.rag.owner import OwnerLost
from app.rag.query_adapter import LightRAGQueryAdapter, QueryError
from app.services.answers import AnswerService
from app.services.errors import ServiceError


class AgentRunner:
    heartbeat_seconds = 10

    def __init__(self, app, saver):
        self.app, self.saver = app, saver
        self.db = app.state.database
        self.registry = app.state.tool_registry
        self.id = uuid4()
        self.tasks: dict[UUID, asyncio.Task] = {}
        self.admission = asyncio.Lock()
        self.closed = False
        self.monitor = None

    def assert_owned(self):
        if self.closed:
            raise ServiceError(503, "agent_unavailable", "任务执行器已关闭")
        if self.app.state.owner is not None:
            self.app.state.owner.assert_owned()

    async def start(
        self,
        owner,
        conversation_id,
        request_id,
        question,
        *,
        image_ids=None,
        voice=None,
        voice_generation=None,
        retry_message_id=None,
    ):
        self.assert_owned()
        if not self.app.state.agent_enabled or not self.app.state.answer_enabled:
            raise ServiceError(503, "agent_disabled", "自动工具尚未启用，请使用现有问答")
        async with self.admission, self.db.sessions() as session:
            service = AnswerService(
                session,
                image_store=self.app.state.image_store,
                image_observer=self.app.state.image_observer or self.app.state.rag_runtime,
                admission=None if voice else self.app.state.voice_runtime.registry.require_text,
            )
            chat, kb = await service._owned_conversation(owner, conversation_id, lock=True)
            existing = await session.scalar(
                select(AgentRun).where(
                    AgentRun.conversation_id == conversation_id, AgentRun.request_id == request_id
                )
            )
            if existing:
                message = await session.get(ConversationMessage, existing.message_id)
                linked = (await service.images.for_message(message.id)) if service.images else []
                if (
                    (retry_message_id is not None and message.id != retry_message_id)
                    or (retry_message_id is None and message.content != question)
                    or retry_message_id is None
                    and {x.id for x in linked} != set(image_ids or [])
                    or existing.voice_session_id != (voice.id if voice else None)
                ):
                    raise ServiceError(409, "idempotency_conflict", "同一请求不能更换提问或附件")
                return snapshot(existing)
            if len(self.tasks) >= 2:
                raise ServiceError(409, "agent_capacity", "当前有两个任务执行中，请稍后再试")
            if chat.archived_at is not None:
                raise ServiceError(409, "conversation_archived", "聊天已归档")
            run_id = uuid4()

            async def accepted(view):
                attempt = await session.get(AnswerAttempt, view["attempt_id"])
                run = AgentRun(
                    id=run_id,
                    owner_id=owner,
                    conversation_id=conversation_id,
                    request_id=request_id,
                    message_id=view["message_id"],
                    attempt_id=attempt.id,
                    kb_id=chat.kb_id,
                    kb_revision=attempt.kb_revision,
                    workspace=attempt.workspace,
                    graph_version=GRAPH_VERSION,
                    status="running",
                    generation=1,
                    voice_session_id=voice.id if voice else None,
                    voice_generation=voice_generation,
                )
                renew(run, self.id)
                session.add(run)
                await session.flush()
                emit(session, run, "phase", {"phase": "accepted"})
                await session.commit()

            if retry_message_id is not None:
                await service.retry(
                    owner,
                    conversation_id,
                    retry_message_id,
                    request_id,
                    None,
                    None,
                    on_accepted=accepted,
                    defer_finish=True,
                )
            else:
                await service.ask(
                    owner,
                    conversation_id,
                    request_id,
                    question,
                    None,
                    None,
                    mode="auto",
                    image_ids=image_ids,
                    on_accepted=accepted,
                    defer_finish=True,
                )
            run = await session.get(AgentRun, run_id)
            if run is None:
                raise ServiceError(
                    409, "agent_attempt_conflict", "该消息已有旧回答，请按原路径重试"
                )
            result = snapshot(run)
            self._launch(run.id, run.generation)
            return result

    def _launch(self, run_id, generation, *, resume=None, continuing=False):
        if run_id in self.tasks:
            raise ServiceError(409, "agent_in_progress", "任务已由当前执行器处理")
        self.tasks[run_id] = asyncio.create_task(
            self._execute(run_id, generation, resume=resume, continuing=continuing),
            name=f"agent-{run_id}",
        )

    async def prepare_generation(self, run_id, generation, prepared):
        async with self.db.sessions() as session:
            run = await fenced(session, run_id, generation, self.id)
            run.prepared = prepared
            await session.commit()

    async def pause(self, run_id, generation, waiting):
        async with self.db.sessions() as session:
            run = await fenced(session, run_id, generation, self.id)
            run.waiting = waiting
            run.status = "waiting_approval" if waiting["kind"] == "approval" else "waiting_input"
            run.wait_until = datetime.now(UTC) + timedelta(hours=24)
            run.runner_id, run.lease_until = None, None
            attempt = await session.get(AnswerAttempt, run.attempt_id)
            attempt.status = run.status
            emit(session, run, run.status, {"waiting": waiting})
            await session.commit()

    async def _heartbeat(self, run_id, generation, execution, started, initial_ms):
        try:
            while True:
                await asyncio.sleep(self.heartbeat_seconds)
                self.assert_owned()
                async with self.db.sessions() as session:
                    run = await session.get(AgentRun, run_id)
                    if run is not None and run.status in {"waiting_input", "waiting_approval"}:
                        return
                    run = await fenced(session, run_id, generation, self.id)
                    run.active_ms = max(
                        run.active_ms, initial_ms + round((time.monotonic() - started) * 1000)
                    )
                    renew(run, self.id)
                    await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            # A lost owner/lease or failed heartbeat must stop the actual provider task.
            execution.cancel()

    async def _execute(self, run_id, generation, *, resume=None, continuing=False):
        started = time.monotonic()
        heartbeat = None
        gate = self.app.state.backup_gate
        admitted = False
        initial_ms = None
        try:
            await gate.wait_and_enter()
            admitted = True
            started = time.monotonic()
            async with self.db.sessions() as session:
                run = await fenced(session, run_id, generation, self.id)
                initial_ms = run.active_ms
                remaining = (60_000 - run.active_ms) / 1000
                heartbeat = asyncio.create_task(
                    self._heartbeat(run_id, generation, asyncio.current_task(), started, initial_ms)
                )
                owner, conversation_id, prepared = run.owner_id, run.conversation_id, run.prepared
                message = await session.get(ConversationMessage, run.message_id)
                attempt = await session.get(AnswerAttempt, run.attempt_id)
                kb = await session.get(KnowledgeBase, run.kb_id) if run.kb_id else None
                await session.commit()
                runtime = self.app.state.rag_runtime
                base = self.app.state.answer_adapter or LightRAGAnswerAdapter(runtime)
                retriever = self.app.state.query_adapter or LightRAGQueryAdapter(runtime)
                service = AnswerService(
                    session,
                    image_store=self.app.state.image_store,
                    image_observer=self.app.state.image_observer or runtime,
                    commit_allowed=lambda: self.can_commit(run),
                )
                async with asyncio.timeout(max(0.001, remaining)):
                    if resume is not None or continuing:
                        raw = await drive(
                            self, run_id, generation, base, resume=resume, continuing=continuing
                        )
                        await self.guard_commit(run_id, generation)
                        view = await service.finish_prepared(
                            owner, conversation_id, message, attempt, base, prepared, raw
                        )
                    else:
                        view = await service._finish_attempt(
                            owner,
                            conversation_id,
                            kb,
                            message,
                            attempt,
                            retriever,
                            AgentAnswerer(self, run_id, generation, base),
                            "auto",
                            None,
                        )
                async with self.db.sessions() as final:
                    current = await final.get(AgentRun, run_id, with_for_update=True)
                    if current is not None and current.generation == generation:
                        if current.status == "running":
                            current.status = (
                                "completed"
                                if view.get("status") not in {"failed", "partial", "interrupted"}
                                else "failed"
                            )
                            current.error_code = view.get("error_code")
                            current.finished_at = datetime.now(UTC)
                            emit(final, current, "answer_saved", {"answer": view})
                            emit(final, current, "terminal", {"status": current.status})
                        await final.commit()
        except AgentPaused:
            pass
        except asyncio.CancelledError:
            await self._terminal(run_id, generation, "interrupted", "agent_execution_lost")
            raise
        except TimeoutError:
            await self._terminal(run_id, generation, "failed", "agent_time_budget")
        except (ServiceError, AgentError, AnswerError, QueryError) as error:
            code = error.code if isinstance(error, ServiceError) else str(error)
            await self._terminal(run_id, generation, "failed", code)
        except Exception:
            # A final boundary must not log provider credentials or private context.
            await self._terminal(run_id, generation, "failed", "agent_unavailable")
        finally:
            try:
                if heartbeat is not None:
                    heartbeat.cancel()
                    with suppress(asyncio.CancelledError, ServiceError):
                        await heartbeat
                async with self.db.sessions() as session:
                    run = await session.get(AgentRun, run_id, with_for_update=True)
                    if run is not None and run.generation == generation and initial_ms is not None:
                        run.active_ms = max(
                            run.active_ms, initial_ms + round((time.monotonic() - started) * 1000)
                        )
                        if run.status not in ACTIVE:
                            run.runner_id, run.lease_until = None, None
                        await session.commit()
            finally:
                self.tasks.pop(run_id, None)
                if admitted:
                    await gate.leave()

    async def guard_commit(self, run_id, generation):
        async with self.db.sessions() as session:
            await fenced(session, run_id, generation, self.id)

    def can_commit(self, run):
        if self.closed or run.id not in self.tasks:
            return False
        owner = self.app.state.owner
        if owner is not None:
            try:
                owner.assert_owned()
            except OwnerLost:
                return False
        if run.voice_session_id is not None:
            registry = self.app.state.voice_runtime.registry
            call = registry.calls.get(run.voice_session_id)
            return call is not None and registry.current(call, run.voice_generation)
        return True

    async def _terminal(self, run_id, generation, status, code):
        async with self.db.sessions() as session:
            run = await session.get(AgentRun, run_id, with_for_update=True)
            if run is None or run.generation != generation or run.status not in ACTIVE:
                return
            if code == "agent_execution_lost" and run.status in {
                "waiting_input",
                "waiting_approval",
            }:
                # Graceful shutdown can race the pause unwind. The wait is already durable.
                return
            run.status, run.error_code = status, code
            run.finished_at, run.waiting = datetime.now(UTC), None
            attempt = await session.get(AnswerAttempt, run.attempt_id)
            if attempt.status in ACTIVE:
                attempt.status = (
                    "interrupted" if status in {"cancelled", "interrupted", "expired"} else "failed"
                )
                attempt.error_code, attempt.finished_at = code, run.finished_at
                attempt.citations = []
            if status != "interrupted":
                await session.execute(
                    update(ToolCall)
                    .where(ToolCall.run_id == run.id, ToolCall.status == "pending_approval")
                    .values(status="rejected", error_code=code, finished_at=run.finished_at)
                )
            emit(session, run, "terminal", {"status": status, "error_code": code})
            await session.commit()

    async def get(self, owner, conversation_id, run_id):
        async with self.db.sessions() as session:
            return snapshot(await owned(session, owner, conversation_id, run_id))

    async def resume(self, owner, conversation_id, run_id, request_id, generation, payload):
        self.assert_owned()
        previous = self.tasks.get(run_id)
        if previous is not None:
            state = await self.get(owner, conversation_id, run_id)
            if state["status"] in {"waiting_input", "waiting_approval"}:
                # Wait outside DB locks: pause is durable slightly before the old task unwinds.
                try:
                    await asyncio.wait_for(asyncio.shield(previous), 2)
                except TimeoutError:
                    raise ServiceError(
                        409, "agent_in_progress", "任务正在释放执行资源，请稍后重试"
                    ) from None
        async with self.admission, self.db.sessions() as session:
            run = await owned(session, owner, conversation_id, run_id, lock=True)
            from app.models import AgentStep

            decision_key = f"resume:{request_id}"
            earlier = await session.scalar(
                select(AgentStep).where(AgentStep.run_id == run.id, AgentStep.key == decision_key)
            )
            if earlier is not None:
                if earlier.data != payload:
                    raise ServiceError(409, "idempotency_conflict", "恢复请求不能更换参数")
                return snapshot(run)
            if run.resume_id == request_id:
                if run.resume_payload != payload:
                    raise ServiceError(409, "idempotency_conflict", "恢复请求不能更换参数")
                return snapshot(run)
            if (
                run.generation != generation
                or run.status not in {"waiting_input", "waiting_approval", "interrupted"}
                or run.id in self.tasks
            ):
                raise ServiceError(409, "agent_resume_closed", "任务状态已变化，请刷新")
            await check_binding(session, run)
            other = await session.scalar(
                select(AgentRun.id)
                .where(
                    AgentRun.conversation_id == conversation_id,
                    AgentRun.id != run.id,
                    AgentRun.status.in_(ACTIVE),
                )
                .limit(1)
            )
            if other:
                raise ServiceError(409, "agent_in_progress", "当前聊天已有另一个任务")
            if run.graph_version != GRAPH_VERSION or run.prepared is None:
                raise ServiceError(409, "agent_version_changed", "任务无法由当前版本恢复")
            if run.wait_until is not None and run.wait_until <= datetime.now(UTC):
                raise ServiceError(409, "agent_expired", "等待已到期，请重新提问")
            if run.status == "interrupted" and run.error_code not in {
                "server_restarted",
                "agent_execution_lost",
            }:
                raise ServiceError(409, "agent_resume_closed", "该中断不可恢复")
            if len(self.tasks) >= 2:
                raise ServiceError(409, "agent_capacity", "任务执行器繁忙")
            continuing = run.status == "interrupted"
            waiting = run.waiting or {}
            if waiting.get("kind") == "input":
                fields = waiting.get("fields", {})
                schema = {
                    "type": "object",
                    "properties": {k: {"type": v} for k, v in fields.items()},
                    "required": list(fields),
                    "additionalProperties": not fields,
                }
                natural = (set(payload) == {"detail"}
                           and isinstance(payload["detail"], str)
                           and 1 <= len(payload["detail"].strip()) <= 1000)
                replan = (payload == {"replan": True} and not waiting.get("tool_id")
                          and run.model_rounds < 6)
                if not natural and not replan and list(
                    Draft202012Validator(schema).iter_errors(payload)
                ):
                    raise ServiceError(422, "agent_input_invalid", "请按等待字段补充参数")
                if waiting.get("tool_id"):
                    spec = self.registry.get(waiting["tool_id"])
                    if (spec is None or spec.version != waiting.get("tool_version")
                        or spec.policy_hash != waiting.get("policy_hash")):
                        raise ServiceError(
                            409, "tool_scope_changed", "Tool scope changed; refresh task",
                        )
            elif waiting.get("kind") == "approval":
                if set(payload) != {"approve"} or not isinstance(payload["approve"], bool):
                    raise ServiceError(422, "agent_input_invalid", "需要明确批准或拒绝")
                call = await session.get(ToolCall, UUID(waiting["call_id"]), with_for_update=True)
                spec = self.registry.get(waiting["tool_id"])
                if (
                    call is None
                    or call.run_id != run.id
                    or call.status != "pending_approval"
                    or spec is None
                    or spec.version != waiting["tool_version"]
                    or call.arguments != waiting["arguments"]
                    or spec.policy_hash != waiting.get("policy_hash")
                ):
                    raise ServiceError(409, "tool_approval_changed", "操作内容已变化，不能批准")
                call.decision_id = request_id
                if payload["approve"]:
                    call.approved_at = datetime.now(UTC)
                else:
                    call.status, call.finished_at = "rejected", datetime.now(UTC)
            elif not continuing:
                raise ServiceError(409, "agent_resume_closed", "任务没有可恢复的等待对象")
            attempt = await session.get(AnswerAttempt, run.attempt_id)
            run.resume_id, run.resume_payload = request_id, payload
            session.add(
                AgentStep(
                    run_id=run.id, key=decision_key, kind="resume", status="succeeded", data=payload
                )
            )
            run.generation += 1
            run.status, run.waiting, run.error_code = "running", None, None
            run.finished_at, run.wait_until = None, None
            renew(run, self.id)
            attempt.status, attempt.finished_at, attempt.error_code = "running", None, None
            emit(session, run, "phase", {"phase": "resuming"})
            await session.commit()
            result = snapshot(run)
            self._launch(
                run.id,
                run.generation,
                resume=None if continuing else payload,
                continuing=continuing,
            )
            return result

    async def cancel(
        self, owner, conversation_id, run_id, *, code="agent_cancelled", internal=False
    ):
        async with self.db.sessions() as session:
            run = (
                await session.get(AgentRun, run_id, with_for_update=True)
                if internal
                else await owned(session, owner, conversation_id, run_id, lock=True)
            )
            if run is None or run.owner_id != owner or run.conversation_id != conversation_id:
                raise ServiceError(404, "agent_not_found", "任务不存在")
            if run.status not in ACTIVE:
                return snapshot(run)
            # Persist cancellation before cancelling actual async operations.
            run.generation += 1
            run.status, run.error_code = (
                ("expired" if code == "agent_expired" else "cancelled"),
                code,
            )
            run.runner_id, run.lease_until, run.wait_until = None, None, None
            run.waiting, run.finished_at = None, datetime.now(UTC)
            attempt = await session.get(AnswerAttempt, run.attempt_id)
            attempt.status, attempt.error_code = "interrupted", code
            attempt.finished_at, attempt.citations = run.finished_at, []
            await session.execute(
                update(ToolCall)
                .where(ToolCall.run_id == run.id, ToolCall.status == "pending_approval")
                .values(status="rejected", error_code=code, finished_at=run.finished_at)
            )
            emit(session, run, "terminal", {"status": run.status, "error_code": code})
            await session.commit()
            result = snapshot(run)
        task = self.tasks.get(run_id)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        return result

    async def check_voice(self, run):
        registry = self.app.state.voice_runtime.registry
        call = registry.calls.get(run.voice_session_id)
        if call is None or not registry.current(call, run.voice_generation):
            raise ServiceError(409, "voice_interrupted", "语音会话或轮次已失效")

    async def reconcile(self):
        if self.closed:
            return
        async with self.db.sessions() as session:
            rows = list(await session.scalars(select(AgentRun).where(AgentRun.status.in_(ACTIVE))))
        for run in rows:
            try:
                async with self.db.sessions() as session:
                    await check_binding(session, run)
                if run.voice_session_id:
                    await self.check_voice(run)
                if run.wait_until and run.wait_until <= datetime.now(UTC):
                    raise ServiceError(409, "agent_expired", "等待已到期")
            except ServiceError as error:
                with suppress(ServiceError):
                    await self.cancel(
                        run.owner_id, run.conversation_id, run.id, code=error.code, internal=True
                    )

    async def start_monitor(self):
        await self.reconcile()

        async def monitor():
            while True:
                await asyncio.sleep(5)
                await self.app.state.backup_gate.wait_and_enter()
                try:
                    await self.reconcile()
                    await self.cleanup_checkpoints()
                finally:
                    await self.app.state.backup_gate.leave()

        self.monitor = asyncio.create_task(monitor(), name="agent-monitor")

    async def cleanup_checkpoints(self):
        from sqlalchemy import null, text

        from app.agent.checkpoints import checkpoint_namespace
        from app.models import AgentStep

        cutoff = datetime.now(UTC) - timedelta(days=7)
        async with self.db.sessions() as session:
            ids = list(
                await session.scalars(
                    select(AgentRun.id)
                    .where(
                        AgentRun.finished_at < cutoff,
                        AgentRun.prepared.is_not(None),
                        ~AgentRun.status.in_(ACTIVE),
                    )
                    .limit(50)
                    .with_for_update(skip_locked=True)
                )
            )
            for run_id in ids:
                await self.saver.adelete_thread(str(run_id))
            if ids:
                await session.execute(
                    update(AgentRun)
                    .where(AgentRun.id.in_(ids))
                    .values(prepared=null(), resume_payload=null())
                )
                await session.execute(
                    update(AgentStep)
                    .where(AgentStep.run_id.in_(ids), AgentStep.kind == "model")
                    .values(data=None)
                )
            namespace = checkpoint_namespace(await session.scalar(text("SELECT current_schema()")))
            # Checkpoints have no business FK. Remove orphan threads after owner deletion/retention.
            exists = await session.scalar(
                text("SELECT to_regclass(:table)"), {"table": namespace + ".checkpoints"}
            )
            if exists:
                orphaned = list(
                    await session.scalars(
                        text(
                            f'SELECT DISTINCT thread_id FROM "{namespace}".checkpoints '
                            "WHERE thread_id NOT IN (SELECT id::text FROM agent_runs) LIMIT 50"
                        )
                    )
                )
                for thread in orphaned:
                    await self.saver.adelete_thread(thread)
            await session.commit()

    async def close(self):
        for task in list(self.tasks.values()):
            task.cancel()
        for task in list(self.tasks.values()):
            with suppress(asyncio.CancelledError):
                await task
        if self.monitor:
            self.monitor.cancel()
            with suppress(asyncio.CancelledError):
                await self.monitor
        self.closed = True
