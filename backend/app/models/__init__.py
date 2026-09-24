from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
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
        UniqueConstraint(
            "local_owner_id", "create_request_id", name="uq_knowledge_bases_create_request"
        ),
        CheckConstraint(
            "(create_request_id IS NULL AND create_request_name IS NULL) OR "
            "(create_request_id IS NOT NULL AND create_request_name IS NOT NULL "
            "AND local_owner_id IS NOT NULL)",
            name="ck_knowledge_bases_create_request",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120))
    owner_id: Mapped[UUID | None] = mapped_column(
        "local_owner_id", ForeignKey("local_profiles.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="empty", server_default="empty")
    active_workspace: Mapped[str] = mapped_column(String(100), unique=True)
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    hide_history_before_revision: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    create_request_id: Mapped[UUID | None] = mapped_column()
    create_request_name: Mapped[str | None] = mapped_column(String(120))
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


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("uq_documents_active_content", "kb_id", "sha256", unique=True,
              postgresql_where=text("status <> 'deleted'")),
        CheckConstraint(
            "status IN ('pending','parsing','parsed','indexing','ready','failed',"
            "'deleting','replacing','deleted')",
            name="ck_documents_status",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    kb_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    filename: Mapped[str] = mapped_column(String(240))
    storage_key: Mapped[str] = mapped_column(String(100), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error_code: Mapped[str | None] = mapped_column(String(60))
    source_key: Mapped[str] = mapped_column(String(100), unique=True)
    engine_doc_id: Mapped[str] = mapped_column(String(100), unique=True)
    parsed_text: Mapped[str | None] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    indexed_once: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    doc_code: Mapped[str | None] = mapped_column(String(80))
    model_code: Mapped[str | None] = mapped_column(String(80))
    edition: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ParsedBlockRecord(Base):
    __tablename__ = "parsed_blocks"
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    locator: Mapped[dict] = mapped_column(JSON)
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        UniqueConstraint("kb_id", "client_request_id", name="uq_jobs_request"),
        CheckConstraint("operation IN ('upload','rebuild','delete','replace')",
                        name="ck_jobs_operation"),
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','interrupted')",
            name="ck_jobs_status",
        ),
        CheckConstraint(
            "stage IN ('accepted','parsing','parsed','indexing','verifying','cleanup','complete')",
            name="ck_jobs_stage",
        ),
        Index("ix_jobs_status_created", "status", "created_at"),
        Index("uq_jobs_active_kb", "kb_id", unique=True,
              postgresql_where=text("status IN ('queued','running')")),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    kb_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    client_request_id: Mapped[UUID] = mapped_column()
    operation: Mapped[str] = mapped_column(String(16))
    fingerprint: Mapped[str] = mapped_column(String(64))
    document_ids: Mapped[list[str]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    stage: Mapped[str] = mapped_column(String(16), default="accepted")
    engine_mutated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    target_workspace: Mapped[str | None] = mapped_column(String(100))
    retired_workspace: Mapped[str | None] = mapped_column(String(100))
    cleanup_pending: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    error_code: Mapped[str | None] = mapped_column(String(60))
    message: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "client_message_id", name="uq_conversation_message_request"
        ),
        Index("ix_conversation_messages_order", "conversation_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"), index=True)
    client_message_id: Mapped[UUID] = mapped_column()
    content: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(16), default="semantic")
    query_filter: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnswerAttempt(Base):
    __tablename__ = "answer_attempts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running','answered','insufficient_evidence','needs_clarification',"
            "'conflicting_evidence','failed','interrupted')", name="ck_answer_attempt_status",
        ),
        Index("uq_answer_attempt_active", "conversation_id", unique=True,
              postgresql_where=text("status = 'running'")),
        Index("ix_answer_attempts_message", "message_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"), index=True)
    message_id: Mapped[UUID] = mapped_column(ForeignKey("conversation_messages.id"))
    status: Mapped[str] = mapped_column(String(32), default="running")
    text: Mapped[str | None] = mapped_column(Text)
    citations: Mapped[list[dict]] = mapped_column(JSON, default=list)
    kb_revision: Mapped[int] = mapped_column(Integer)
    workspace: Mapped[str] = mapped_column(String(100))
    error_code: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConversationSummary(Base):
    __tablename__ = "conversation_summaries"

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id"), primary_key=True,
    )
    kb_revision: Mapped[int] = mapped_column(Integer)
    workspace: Mapped[str] = mapped_column(String(100))
    through_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    through_message_id: Mapped[UUID] = mapped_column()
    content: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  server_default=func.now())
