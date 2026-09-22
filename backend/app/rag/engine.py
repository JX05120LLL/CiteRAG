"""Isolated, single-owner SDK lifecycle. No query or ingestion HTTP endpoints in M0."""

import asyncio
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol
from uuid import UUID


def workspace_for(kb_id: UUID, root: Path) -> tuple[str, Path]:
    if not isinstance(kb_id, UUID):
        raise TypeError('Knowledge base ID must be a server UUID')
    workspace = f'kb_{kb_id.hex}'
    root = root.resolve()
    directory = (root / workspace).resolve()
    if directory.parent != root:
        raise ValueError('Engine directory escapes the configured root')
    return workspace, directory


def assert_isolated_configuration(environment: Mapping[str, str], directory: Path) -> None:
    if environment.get('POSTGRES_WORKSPACE'):
        raise ValueError('POSTGRES_WORKSPACE must be unset; workspaces belong to knowledge bases')
    # Do not read a potentially credential-bearing legacy config. Use controlled env only.
    if (directory / 'config.ini').exists():
        raise ValueError('config.ini is not supported; use controlled server configuration')


class Engine(Protocol):
    async def initialize_storages(self) -> None: ...

    async def finalize_storages(self) -> None: ...


class EngineManager:
    def __init__(
        self,
        root: Path,
        factory: Callable[[str, Path], Engine],
        assert_owner: Callable[[], None],
    ):
        self._root = root
        self._factory = factory
        self._assert_owner = assert_owner
        self._engines: dict[UUID, Engine] = {}
        # SDK shared initialization must be serial, even between different workspaces.
        self._lock = asyncio.Lock()
        self._closed = False

    async def get(self, kb_id: UUID) -> Engine:
        async with self._lock:
            self._assert_owner()
            if self._closed:
                raise RuntimeError('Engine manager is closed')
            if kb_id not in self._engines:
                workspace, directory = workspace_for(kb_id, self._root)
                engine = self._factory(workspace, directory)
                try:
                    await engine.initialize_storages()
                    self._assert_owner()
                except BaseException:
                    # Cancellation also leaves partially initialized storage to clean up.
                    await engine.finalize_storages()
                    raise
                self._engines[kb_id] = engine
            return self._engines[kb_id]

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            errors = []
            for engine in self._engines.values():
                try:
                    await engine.finalize_storages()
                except Exception as error:
                    # Finish the other instances, then report all cleanup failures.
                    errors.append(error)
            self._engines.clear()
            if errors:
                raise ExceptionGroup('Engine shutdown failed', errors)
