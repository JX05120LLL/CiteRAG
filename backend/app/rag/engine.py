"""Isolated, single-owner SDK lifecycle. No query or ingestion HTTP endpoints in M0."""

import asyncio
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol
from uuid import UUID


def workspace_for(
    kb_id: UUID, root: Path, workspace: str | None = None
) -> tuple[str, Path]:
    if not isinstance(kb_id, UUID):
        raise TypeError('Knowledge base ID must be a server UUID')
    base = f'kb_{kb_id.hex}'
    if workspace is None:
        workspace = base
    if not isinstance(workspace, str) or not re.fullmatch(
        re.escape(base) + r'(?:_r_[0-9a-f]{32})?', workspace
    ):
        raise ValueError('Engine workspace does not belong to this knowledge base')
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
        self._engines: dict[tuple[UUID, str], Engine] = {}
        self._initializing: dict[tuple[UUID, str], Engine] = {}
        # SDK shared initialization must be serial, even between different workspaces.
        self._lock = asyncio.Lock()
        self._closed = False

    async def get(self, kb_id: UUID, workspace: str | None = None) -> Engine:
        workspace, directory = workspace_for(kb_id, self._root, workspace)
        key = (kb_id, workspace)
        async with self._lock:
            self._assert_owner()
            if self._closed:
                raise RuntimeError('Engine manager is closed')
            if key not in self._engines:
                engine = self._factory(workspace, directory)
                self._initializing[key] = engine
                try:
                    await engine.initialize_storages()
                    self._assert_owner()
                except BaseException as initialization_error:
                    try:
                        await _finalize_protected(engine)
                    except BaseException as cleanup_error:
                        _raise_cleanup_group(
                            'Engine initialization and cleanup failed',
                            [initialization_error, cleanup_error],
                        )
                    raise
                finally:
                    self._initializing.pop(key, None)
                self._engines[key] = engine
            return self._engines[key]

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            engines = tuple(self._engines.values())
            self._engines.clear()
            errors: list[BaseException] = []
            for engine in engines:
                try:
                    await _finalize_protected(engine)
                except BaseException as error:
                    # Finish the other instances, then report all cleanup failures.
                    errors.append(error)
            if errors:
                _raise_cleanup_group('Engine shutdown failed', errors)


async def _finalize_protected(engine: Engine) -> None:
    """Finish one cleanup even if the caller receives another cancellation."""

    task = asyncio.create_task(engine.finalize_storages())
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    await task
    if cancelled:
        raise asyncio.CancelledError


def _raise_cleanup_group(message: str, errors: list[BaseException]) -> None:
    if all(isinstance(error, Exception) for error in errors):
        raise ExceptionGroup(message, errors)
    raise BaseExceptionGroup(message, errors)
