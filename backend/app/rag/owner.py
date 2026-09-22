"""Dedicated PostgreSQL session lock: admission gate, never a failover mechanism."""

import asyncio
from collections.abc import Callable
from contextlib import suppress

import asyncpg

# Stable across processes, held inside the application's database only.
OWNER_LOCK_KEY = 0x43495445524147


class OwnerUnavailable(RuntimeError):
    pass


class OwnerLost(RuntimeError):
    pass


class ApiOwner:
    def __init__(self, dsn: str, on_lost: Callable[[], None] | None = None):
        self._dsn = dsn.replace('postgresql+asyncpg://', 'postgresql://', 1)
        self._on_lost = on_lost
        self._connection: asyncpg.Connection | None = None
        self._monitor: asyncio.Task | None = None
        self._owned = False
        self._closing = False
        self.backend_pid: int | None = None

    def assert_owned(self) -> None:
        if not self._owned or self._connection is None or self._connection.is_closed():
            raise OwnerLost('API owner is unavailable')

    def _lost(self, *_args) -> None:
        if not self._closing and self._owned:
            self._owned = False
            if self._on_lost:
                self._on_lost()

    async def __aenter__(self):
        if self._connection is not None:
            raise OwnerUnavailable('API owner cannot be entered twice')
        try:
            self._connection = await asyncpg.connect(
                self._dsn, timeout=5, command_timeout=3,
                server_settings={'application_name': 'citerag-api-owner'},
            )
            acquired = await self._connection.fetchval(
                'SELECT pg_try_advisory_lock($1)', OWNER_LOCK_KEY
            )
            if not acquired:
                raise OwnerUnavailable('Another API owns this application database')
            self.backend_pid = self._connection.get_server_pid()
            self._owned = True
            self._connection.add_termination_listener(self._lost)
            self._monitor = asyncio.create_task(self._watch(), name='api-owner')
            return self
        except (OSError, asyncpg.PostgresError, TimeoutError, OwnerUnavailable):
            if self._connection is not None:
                await self._connection.close()
            raise OwnerUnavailable(
                'Cannot acquire API ownership; database unavailable or occupied'
            ) from None

    async def _watch(self) -> None:
        try:
            while self._owned:
                await asyncio.sleep(1)
                await self._connection.fetchval('SELECT 1', timeout=3)
        except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError, TimeoutError):
            self._lost()

    async def __aexit__(self, *_args) -> None:
        self._closing = True
        self._owned = False
        if self._monitor is not None:
            self._monitor.cancel()
            with suppress(asyncio.CancelledError):
                await self._monitor
        if self._connection is not None:
            # Session close releases the lock even when transaction state is broken.
            await self._connection.close(timeout=3)
