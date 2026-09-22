from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LocalProfile
from app.services.errors import ServiceError


async def database_session(request: Request) -> AsyncIterator[AsyncSession]:
    database = request.app.state.database
    if database is None:
        raise ServiceError(503, "database_not_configured", "业务数据库尚未配置，请先配置并迁移")
    try:
        async with database.sessions() as session:
            yield session
    except (OSError, TimeoutError):
        # asyncpg can raise raw transport errors before SQLAlchemy wraps them.
        raise ServiceError(
            503, "persistence_failed", "业务数据库不可用，操作未确认保存"
        ) from None


Session = Annotated[AsyncSession, Depends(database_session)]


async def local_owner(session: Session) -> UUID:
    owner = await session.scalar(select(LocalProfile.id))
    if owner is None:
        raise ServiceError(503, "local_profile_unavailable", "本地资料归属缺失，请检查数据库迁移")
    return owner


LocalOwner = Annotated[UUID, Depends(local_owner)]
