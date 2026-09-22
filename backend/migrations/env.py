import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.models import Base

config = context.config
target_metadata = Base.metadata


def migrate_connection(connection):
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def migrate_online():
    settings = Settings.from_env()
    if settings.database_url is None:
        raise RuntimeError("CITERAG_DATABASE_URL must be configured before running migrations")
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        poolclass=pool.NullPool,
        hide_parameters=True,
        echo=False,
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(migrate_connection)
    finally:
        await engine.dispose()


if config.attributes.get("connection") is not None:
    migrate_connection(config.attributes["connection"])
elif context.is_offline_mode():
    context.configure(
        dialect_name="postgresql", target_metadata=target_metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(migrate_online())
