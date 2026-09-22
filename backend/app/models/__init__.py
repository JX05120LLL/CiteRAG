from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class LegacyUser(Base):
    """Retained schema metadata only; no current account or login behavior."""
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('admin', 'user')", name="ck_users_role"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LegacyAuthSession(Base):
    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LocalProfile(Base):
    __tablename__ = "local_profiles"
    __table_args__ = (
        CheckConstraint("singleton IS TRUE", name="ck_local_profiles_singleton"),
        UniqueConstraint("singleton", name="uq_local_profiles_singleton"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    singleton: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        CheckConstraint(
            "status IN ('empty', 'ready', 'maintaining', 'blocked')",
            name="ck_knowledge_bases_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120))
    owner_id: Mapped[UUID | None] = mapped_column(
        "local_owner_id", ForeignKey("local_profiles.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="empty", server_default="empty")
    active_workspace: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_owner_created", "owner_id", "created_at", "id"),
        Index("ix_conversations_local_owner_created", "local_owner_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    legacy_owner_id: Mapped[UUID | None] = mapped_column(
        "owner_id", ForeignKey("users.id", ondelete="RESTRICT")
    )
    owner_id: Mapped[UUID | None] = mapped_column(
        "local_owner_id", ForeignKey("local_profiles.id", ondelete="RESTRICT")
    )
    kb_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(String(120), default="新聊天")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
