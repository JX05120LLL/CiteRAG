from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.config import Settings

EXPECTED_SCHEMA_REVISION = "0005_answer_attempts"


class Database:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def verify_schema(self) -> None:
        """Verify the migrated schema and owner; never create or adopt application data."""
        message = "Business database schema is missing or incompatible; run explicit migrations"
        try:
            async with self.engine.connect() as connection:
                revisions = list(
                    await connection.scalars(text("SELECT version_num FROM alembic_version"))
                )
                if revisions != [EXPECTED_SCHEMA_REVISION]:
                    raise RuntimeError(message)
                profiles = await connection.scalar(text("SELECT count(*) FROM local_profiles"))
        except (SQLAlchemyError, OSError, TimeoutError):
            raise RuntimeError(message) from None
        if profiles != 1:
            raise RuntimeError("Business database local profile is missing or incompatible")

    @classmethod
    def from_settings(cls, settings: Settings) -> "Database | None":
        if settings.database_url is None:
            return None
        return cls(
            create_async_engine(
                settings.database_url.get_secret_value(),
                echo=False,
                hide_parameters=True,
                pool_pre_ping=True,
                connect_args={"timeout": 5, "command_timeout": 15},
            )
        )
