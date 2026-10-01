"""Separate PostgreSQL checkpoint schema. Setup is an explicit maintenance command."""

import asyncio
import hashlib
import sys
import threading
from contextlib import asynccontextmanager

from sqlalchemy import text


def checkpoint_namespace(business_schema: str) -> str:
    if business_schema == "public":
        return "agent_checkpoints"
    return "agent_cp_" + hashlib.md5(business_schema.encode(), usedforsecurity=False).hexdigest()


@asynccontextmanager
async def checkpoint_store(database, *, setup: bool = False):
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    async with database.engine.connect() as connection:
        namespace = checkpoint_namespace(await connection.scalar(text("SELECT current_schema()")))
        if (
            await connection.scalar(text("SELECT to_regnamespace(:name)"), {"name": namespace})
            is None
        ):
            raise RuntimeError("Run explicit Agent schema migration before checkpoint setup")
    dsn = database.engine.url.set(drivername="postgresql").render_as_string(hide_password=False)

    async def open_store():
        pool = AsyncConnectionPool(
            dsn,
            min_size=1,
            max_size=3,
            open=False,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
                "options": f"-c search_path={namespace}",
            },
        )
        await pool.open()
        try:
            await pool.wait(timeout=5)
            saver = AsyncPostgresSaver(pool)
            if setup:
                await saver.setup()
            else:
                async with pool.connection() as connection:
                    cursor = await connection.execute("SELECT to_regclass('checkpoints') AS name")
                    if (await cursor.fetchone())["name"] is None:
                        raise RuntimeError("Run explicit Agent checkpoint setup before startup")
                    cursor = await connection.execute(
                        "SELECT max(v) AS version FROM checkpoint_migrations"
                    )
                    if (await cursor.fetchone())["version"] != len(saver.MIGRATIONS) - 1:
                        raise RuntimeError(
                            "Agent checkpoint version differs from locked dependencies"
                        )
        except BaseException:
            await pool.close()
            raise
        return pool, saver

    if sys.platform != "win32":
        pool, saver = await open_store()
        try:
            yield saver
        finally:
            await pool.close()
        return

    from app.agent.saver_bridge import ThreadedSaver

    loop = asyncio.SelectorEventLoop()

    def run_loop():
        asyncio.set_event_loop(loop)
        loop.run_forever()

    thread = threading.Thread(target=run_loop, name="agent-checkpoints", daemon=True)
    thread.start()
    pool = None
    try:
        pool, saver = await asyncio.wrap_future(
            asyncio.run_coroutine_threadsafe(open_store(), loop)
        )
        yield ThreadedSaver(saver, loop)
    finally:
        if pool is not None:
            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(pool.close(), loop))
        loop.call_soon_threadsafe(loop.stop)
        await asyncio.to_thread(thread.join)
        loop.close()


async def _setup():
    from app.config import Settings
    from app.database import Database

    # This command uses only explicitly supplied database configuration.
    database = Database.from_settings(Settings.from_env())
    if database is None:
        raise RuntimeError("An explicit isolated or separately authorized business DB is required")
    try:
        await database.verify_schema()
        async with checkpoint_store(database, setup=True):
            pass
    finally:
        await database.engine.dispose()


if __name__ == "__main__":
    asyncio.run(_setup())
