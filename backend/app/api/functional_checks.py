"""Explicit, bounded functional checks with private, non-secret evidence."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.validation import _restrict_path

Kind = Literal["model", "asr", "tts", "knowledge"]
State = Literal["running", "available", "unavailable", "not_checked"]
Reason = Literal[
    "check_running", "check_succeeded", "never_checked", "check_expired",
    "configuration_changed", "configuration_unavailable", "interrupted",
    "cancelled", "authentication_rejected", "quota_rejected", "provider_timeout",
    "provider_unavailable", "response_invalid",
    "acceptance_kb_readonly_probe_unavailable",
]
KINDS: tuple[Kind, ...] = ("model", "asr", "tts", "knowledge")
SERVICES: dict[Kind, str] = {
    "model": "DashScope qwen-flash",
    "asr": "VolcEngine bigmodel ASR with MiniMax speech-02-turbo fixture",
    "tts": "MiniMax speech-02-turbo",
    "knowledge": "LightRAG retrieval and citation check",
}


class ProbeFailure(Exception):
    def __init__(self, reason: Reason):
        if reason not in {
            "authentication_rejected", "quota_rejected", "provider_unavailable",
            "response_invalid", "acceptance_kb_readonly_probe_unavailable",
        }:
            raise ValueError("invalid probe failure category")
        self.reason = reason
        super().__init__(reason)


class FunctionalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    kind: Kind
    service: str = Field(min_length=1, max_length=100)
    state: State
    reason: Reason
    request_id: UUID
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    checked_at: datetime
    expires_at: datetime

    def public(self) -> dict[str, str]:
        return {
            "service": self.service,
            "state": self.state, "reason": self.reason,
            "request_id": str(self.request_id),
            "checked_at": self.checked_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "fingerprint": self.fingerprint,
        }


class FunctionalChecks:
    """One in-flight request per service; a matching request ID never re-sends."""

    def __init__(
        self,
        root: Path,
        fingerprint: Callable[[Kind], str | None],
        probes: Mapping[Kind, Callable[[Kind], Awaitable[None]]],
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        timeout: float | Mapping[Kind, float] = 25,
        ttl: timedelta = timedelta(hours=1),
        secure_path: Callable[[Path], None] = _restrict_path,
    ):
        self.root = Path(root)
        self.fingerprint = fingerprint
        self.probes = probes
        self.now = now
        self.timeout = timeout
        self.ttl = ttl
        self.secure_path = secure_path
        self.lock = asyncio.Lock()
        self.tasks: dict[Kind, asyncio.Task] = {}

    def _path(self, kind: Kind) -> Path:
        if kind not in KINDS:
            raise ValueError("unknown_check")
        return self.root / f"functional-{kind}.json"

    def _stored(self, kind: Kind) -> FunctionalRecord | None:
        path = self._path(kind)
        try:
            if path.is_symlink() or path.stat().st_size > 2048:
                return None
            record = FunctionalRecord.model_validate_json(path.read_bytes())
            return record if record.kind == kind else None
        except (OSError, ValueError):
            return None

    def _write(self, record: FunctionalRecord) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.secure_path(self.root)
        target = self._path(record.kind)
        if target.is_symlink():
            raise OSError("functional check target is unsafe")
        temporary = self.root / f".{record.kind}-{os.urandom(16).hex()}.tmp"
        payload = json.dumps(record.model_dump(mode="json"), separators=(",", ":"))
        if len(payload.encode("utf-8")) > 2048:
            raise ValueError("functional report is too large")
        try:
            with open(temporary, "x", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            self.secure_path(temporary)
            os.replace(temporary, target)
            self.secure_path(target)
        finally:
            temporary.unlink(missing_ok=True)

    def _unknown(self, kind: Kind, reason: Reason) -> dict[str, str | None]:
        return {"service": SERVICES[kind], "state": "not_checked", "reason": reason,
                "checked_at": None,
                "expires_at": None, "fingerprint": None}

    def read(self, kind: Kind) -> dict[str, str | None]:
        self._path(kind)
        if kind == "knowledge":
            return self._unknown(kind, "acceptance_kb_readonly_probe_unavailable")
        current = self.fingerprint(kind)
        if current is None:
            return self._unknown(kind, "configuration_unavailable")
        record = self._stored(kind)
        if record is None:
            return self._unknown(kind, "never_checked")
        if record.fingerprint != current:
            return self._unknown(kind, "configuration_changed")
        if record.expires_at <= self.now():
            return self._unknown(kind, "check_expired")
        if record.state == "running" and kind not in self.tasks:
            return self._unknown(kind, "interrupted")
        return record.public()

    async def start(self, kind: Kind, request_id: UUID) -> dict[str, str | None]:
        self._path(kind)
        async with self.lock:
            current = self.fingerprint(kind)
            if current is None:
                raise ValueError("configuration_unavailable")
            previous = self._stored(kind)
            if previous is not None and previous.request_id == request_id:
                return self.read(kind)
            task = self.tasks.get(kind)
            if task is not None and not task.done():
                raise ValueError("check_in_progress")
            if kind not in self.probes:
                raise ValueError("check_unavailable")
            return self._launch(kind, request_id, current)

    def _launch(self, kind: Kind, request_id: UUID, fingerprint: str) -> dict[str, str]:
        checked_at = self.now()
        record = FunctionalRecord(
            kind=kind, service=SERVICES[kind], state="running", reason="check_running",
            request_id=request_id, fingerprint=fingerprint,
            checked_at=checked_at, expires_at=checked_at + self.ttl,
        )
        self._write(record)
        self.tasks[kind] = asyncio.create_task(self._run(record), name=f"check-{kind}")
        return record.public()

    async def ensure(self, kind: Kind, *, force: bool = False) -> dict[str, str | None]:
        """Start one bounded probe only when current evidence cannot be reused."""
        self._path(kind)
        async with self.lock:
            current = self.fingerprint(kind)
            if current is None:
                return self.read(kind)
            task = self.tasks.get(kind)
            if task is not None and not task.done():
                return self.read(kind)
            record = self._stored(kind)
            if (not force and record is not None and record.fingerprint == current
                    and record.expires_at > self.now()
                    and record.state in {"available", "unavailable"}):
                return record.public()
            if kind not in self.probes:
                return self.read(kind)
            return self._launch(kind, uuid4(), current)

    async def ensure_all(self, *, force: bool = False) -> dict[str, object]:
        for kind in ("model", "asr", "tts"):
            await self.ensure(kind, force=force)
        return self.read_all()

    async def _run(self, record: FunctionalRecord) -> None:
        state: State = "available"
        reason: Reason = "check_succeeded"
        try:
            limit = (self.timeout[record.kind] if isinstance(self.timeout, Mapping)
                     else self.timeout)
            await asyncio.wait_for(self.probes[record.kind](record.kind), limit)
        except asyncio.CancelledError:
            state, reason = "not_checked", "cancelled"
        except TimeoutError:
            state, reason = "unavailable", "provider_timeout"
        except ProbeFailure as error:
            state, reason = "unavailable", error.reason
        except Exception:
            state, reason = "unavailable", "provider_unavailable"
        finally:
            finished = self.now()
            try:
                self._write(record.model_copy(update={
                    "state": state, "reason": reason, "checked_at": finished,
                    "expires_at": finished + self.ttl,
                }))
            finally:
                self.tasks.pop(record.kind, None)

    async def wait(self, kind: Kind) -> None:
        task = self.tasks.get(kind)
        if task is not None:
            await asyncio.shield(task)

    async def cancel(self, kind: Kind, request_id: UUID) -> dict[str, str | None]:
        task = self.tasks.get(kind)
        record = self._stored(kind)
        if record is None or record.request_id != request_id:
            raise ValueError("check_not_found")
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                if not task.cancelled():
                    raise
                finished = self.now()
                self._write(record.model_copy(update={
                    "state": "not_checked", "reason": "cancelled",
                    "checked_at": finished, "expires_at": finished + self.ttl,
                }))
                self.tasks.pop(kind, None)
        return self.read(kind)

    def read_all(self) -> dict[str, object]:
        return {"checks": {kind: self.read(kind) for kind in KINDS}}

    async def close(self) -> None:
        tasks = tuple(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
