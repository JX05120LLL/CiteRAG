"""Short transactions for fenced task state. No provider runs inside a transaction."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from app.models import AgentEvent, AgentRun, ImageAttachment, MessageImage
from app.services.answers import AnswerService
from app.services.errors import ServiceError

ACTIVE = {"running", "waiting_input", "waiting_approval"}
TERMINAL = {"completed", "failed", "cancelled", "interrupted", "expired"}


def snapshot(run: AgentRun) -> dict:
    return {
        "id": run.id,
        "conversation_id": run.conversation_id,
        "message_id": run.message_id,
        "attempt_id": run.attempt_id,
        "status": run.status,
        "generation": run.generation,
        "waiting": run.waiting,
        "error_code": run.error_code,
        "model_rounds": run.model_rounds,
        "tool_attempts": run.tool_attempts,
        "active_ms": run.active_ms,
        "seq": run.event_seq,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
        "voice_session_id": run.voice_session_id,
    }


def emit(session, run: AgentRun, kind: str, payload: dict):
    run.event_seq += 1
    session.add(
        AgentEvent(
            run_id=run.id,
            seq=run.event_seq,
            generation=run.generation,
            type=kind,
            payload=jsonable_encoder(payload),
        )
    )


async def owned(session, owner: UUID, conversation_id: UUID, run_id: UUID, *, lock=False):
    # Lock ordering is always conversation -> run, as for start/legacy intake.
    conversation, _ = await AnswerService(session)._owned_conversation(
        owner, conversation_id, lock=lock
    )
    query = select(AgentRun).where(
        AgentRun.id == run_id,
        AgentRun.owner_id == owner,
        AgentRun.conversation_id == conversation.id,
    )
    run = await session.scalar(query.with_for_update() if lock else query)
    if run is None:
        raise ServiceError(404, "agent_not_found", "任务不存在或不可访问")
    return run


async def check_binding(session, run: AgentRun):
    conversation, kb = await AnswerService(session)._owned_conversation(
        run.owner_id, run.conversation_id
    )
    if conversation.archived_at is not None:
        raise ServiceError(409, "conversation_archived", "聊天已归档")
    if (
        conversation.kb_id != run.kb_id
        or kb is not None
        and (
            kb.status != "ready"
            or (kb.revision, kb.active_workspace) != (run.kb_revision, run.workspace)
        )
    ):
        raise ServiceError(409, "kb_changed", "资料修订或活动空间已变化")
    expired = await session.scalar(
        select(ImageAttachment.id)
        .join(MessageImage, MessageImage.attachment_id == ImageAttachment.id)
        .where(
            MessageImage.message_id == run.message_id,
            ImageAttachment.expires_at <= datetime.now(UTC),
        )
        .limit(1)
    )
    if expired:
        raise ServiceError(409, "image_expired", "图片已过期，任务不能继续")


async def fenced(session, run_id, generation, runner_id):
    query = select(AgentRun).where(AgentRun.id == run_id).with_for_update()
    run = await session.scalar(query)
    if (
        run is None
        or run.status != "running"
        or run.generation != generation
        or run.runner_id != runner_id
        or run.lease_until is None
        or run.lease_until <= datetime.now(UTC)
    ):
        raise ServiceError(409, "agent_interrupted", "任务已中止或执行租约失效")
    await check_binding(session, run)
    return run


def renew(run: AgentRun, runner_id):
    run.runner_id = runner_id
    run.lease_until = datetime.now(UTC) + timedelta(seconds=30)


async def recover_running(session):
    """Debit the unjournaled interval before fencing a crashed executor.

    The last durable heartbeat is lease_until minus the 30-second lease. Downtime
    may overcharge a crashed task, but restarting cannot replenish its budget.
    """
    now = datetime.now(UTC)
    runs = list(
        await session.scalars(
            select(AgentRun).where(AgentRun.status == "running").with_for_update()
        )
    )
    for run in runs:
        elapsed = (
            (now - (run.lease_until - timedelta(seconds=30))).total_seconds()
            if run.lease_until
            else 60
        )
        run.active_ms = min(60_000, run.active_ms + max(0, round(elapsed * 1000)))
        run.status, run.error_code = "interrupted", "server_restarted"
        run.runner_id, run.lease_until, run.finished_at = None, None, now
        emit(session, run, "terminal", {"status": run.status, "error_code": run.error_code})
