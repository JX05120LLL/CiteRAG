"""Daily same-host snapshots, serialized behind the API and ingestion gate."""

import asyncio
from contextlib import suppress
from datetime import datetime

from sqlalchemy.engine import URL

from app.config import LOCAL_RUNTIME_ROOT
from app.maintenance.backup import backup_bundle, verify_bundle


class DailyBackupRunner:
    def __init__(self, settings, runtime, owner_assertion, gate):
        self.settings = settings
        self.runtime = runtime
        self.owner_assertion = owner_assertion
        self.gate = gate
        self.state = "unavailable"
        self.error_code: str | None = None
        self.last_success_at: datetime | None = None
        self._task: asyncio.Task | None = None

    async def start(self):
        self.owner_assertion()
        self._task = asyncio.create_task(self._run(), name="daily-local-backup")

    async def _run(self):
        try:
            while True:
                try:
                    await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Supervisory boundary: keep retrying without logging secrets.
                    self.state = "unavailable"
                    self.error_code = "backup_failed"
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            raise

    async def run_once(self):
        if self.runtime.database_settings is None:
            self.state = "unavailable"
            self.error_code = "engine_configuration_unavailable"
            return
        today = datetime.now().date().isoformat()
        destination = self.settings.backup_root / today
        if destination.exists() and await asyncio.to_thread(verify_bundle, destination):
            self.state = "available"
            self.error_code = None
            return
        if destination.exists():
            self.state = "unavailable"
            self.error_code = "backup_verification_failed"
            return
        engine = self.runtime.database_settings
        engine_url = URL.create(
            "postgresql+asyncpg", username=engine.username,
            password=engine.password.get_secret_value(), host=engine.host,
            port=engine.port, database=engine.database,
        ).render_as_string(hide_password=False)
        self.state = "running"
        self.error_code = None
        try:
            async with self.gate.quiesce():
                self.owner_assertion()
                await backup_bundle(
                    self.settings.database_url.get_secret_value(), engine_url,
                    LOCAL_RUNTIME_ROOT, self.settings.backup_root,
                    self.settings.backup_pg_bin, day=today,
                    owner_assertion=self.owner_assertion,
                )
            self.last_success_at = datetime.now().astimezone()
            self.state = "available"
            self.error_code = None
        except asyncio.CancelledError:
            self.state = "unavailable"
            self.error_code = "backup_interrupted"
            raise
        except Exception:
            # Backup errors may contain connection material; status is category-only.
            self.state = "unavailable"
            self.error_code = "backup_failed"

    async def close(self):
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
