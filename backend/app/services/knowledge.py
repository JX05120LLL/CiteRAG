"""Local knowledge metadata. Creating a library never initializes the RAG engine."""

from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KnowledgeBase, LocalProfile
from app.services.errors import ServiceError

MAX_KNOWLEDGE_BASES = 5


class KnowledgeService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, owner_id: UUID, name: str, request_id: UUID) -> KnowledgeBase:
        # Serialize deduplication and capacity checks in PostgreSQL, including
        # requests from separate browser tabs. The lock ends at commit/rollback.
        owner = await self.session.scalar(
            select(LocalProfile.id).where(LocalProfile.id == owner_id).with_for_update()
        )
        if owner is None:
            raise ServiceError(503, "local_profile_unavailable", "本地资料归属缺失")
        existing = await self.session.scalar(
            select(KnowledgeBase).where(
                KnowledgeBase.owner_id == owner_id,
                KnowledgeBase.create_request_id == request_id,
            )
        )
        if existing is not None:
            if existing.create_request_name != name:
                raise ServiceError(409, "idempotency_conflict", "同一建库请求不能更换名称")
            # Keep comparing the original name after a rename; replay returns
            # the current view and does not restore stale metadata.
            await self.session.commit()
            return existing
        count = await self.session.scalar(
            select(func.count())
            .select_from(KnowledgeBase)
            .where(KnowledgeBase.owner_id == owner_id)
        )
        if count >= MAX_KNOWLEDGE_BASES:
            raise ServiceError(409, "capacity_exceeded", "最多创建 5 个本地知识库")
        kb_id = uuid4()
        knowledge_base = KnowledgeBase(
            id=kb_id, owner_id=owner_id, name=name, status="empty",
            active_workspace="kb_" + kb_id.hex,
            create_request_id=request_id, create_request_name=name,
        )
        self.session.add(knowledge_base)
        await self.session.commit()
        return knowledge_base

    async def rename(self, owner_id: UUID, kb_id: UUID, name: str) -> KnowledgeBase:
        knowledge_base = await self.session.scalar(
            select(KnowledgeBase)
            .where(KnowledgeBase.id == kb_id, KnowledgeBase.owner_id == owner_id)
            .with_for_update()
        )
        if knowledge_base is None:
            raise ServiceError(404, "kb_not_found", "知识库不存在或不可访问")
        # A display-name edit does not alter indexed content or library state.
        knowledge_base.name = name
        await self.session.commit()
        return knowledge_base
