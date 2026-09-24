"""Single-process quiescence for a consistent two-database file snapshot."""

import asyncio
from contextlib import asynccontextmanager

from app.services.errors import ServiceError


class BackupGate:
    def __init__(self):
        self._condition = asyncio.Condition()
        self._pause_lock = asyncio.Lock()
        self._active = 0
        self._paused = False

    async def enter(self):
        async with self._condition:
            if self._paused:
                raise ServiceError(503, "backup_in_progress", "本地备份中，请稍后重试")
            self._active += 1

    async def wait_and_enter(self):
        async with self._condition:
            await self._condition.wait_for(lambda: not self._paused)
            self._active += 1

    async def leave(self):
        async with self._condition:
            if self._active <= 0:
                raise RuntimeError("Backup gate leave without admission")
            self._active -= 1
            self._condition.notify_all()

    @asynccontextmanager
    async def quiesce(self, *, timeout_seconds: float = 600):
        async with self._pause_lock:
            async with self._condition:
                self._paused = True
            try:
                async with self._condition:
                    await asyncio.wait_for(
                        self._condition.wait_for(lambda: self._active == 0), timeout_seconds,
                    )
                yield
            finally:
                async with self._condition:
                    self._paused = False
                    self._condition.notify_all()
