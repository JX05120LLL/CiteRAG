"""Persist text questions and their answer attempts without adopting legacy chats."""

import sqlalchemy as sa
from alembic import op

revision = "0005_answer_attempts"
down_revision = "0004_managed_ingestion"
branch_labels = None
depends_on = None


def upgrade():
    for attribute in ("doc_code", "model_code", "edition"):
        op.add_column("documents", sa.Column(attribute, sa.String(80)))
    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("client_message_id", sa.Uuid(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False, server_default="semantic"),
        sa.Column("query_filter", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("conversation_id", "client_message_id",
                            name="uq_conversation_message_request"),
    )
    op.create_index("ix_conversation_messages_conversation_id", "conversation_messages",
                    ["conversation_id"])
    op.create_index("ix_conversation_messages_order", "conversation_messages",
                    ["conversation_id", "created_at", "id"])
    op.create_table(
        "answer_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column(
            "message_id", sa.Uuid(), sa.ForeignKey("conversation_messages.id"), nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("text", sa.Text()),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("kb_revision", sa.Integer(), nullable=False),
        sa.Column("workspace", sa.String(100), nullable=False),
        sa.Column("error_code", sa.String(60)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('running','answered','insufficient_evidence',"
                           "'needs_clarification','conflicting_evidence','failed','interrupted')",
                           name="ck_answer_attempt_status"),
    )
    op.create_index("ix_answer_attempts_conversation_id", "answer_attempts",
                    ["conversation_id"])
    op.create_index("ix_answer_attempts_message", "answer_attempts",
                    ["message_id", "created_at", "id"])
    op.create_index("uq_answer_attempt_active", "answer_attempts", ["conversation_id"],
                    unique=True, postgresql_where=sa.text("status = 'running'"))


def downgrade():
    op.execute(sa.text("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM conversation_messages)
               OR EXISTS (SELECT 1 FROM answer_attempts)
               OR EXISTS (SELECT 1 FROM documents WHERE doc_code IS NOT NULL
                          OR model_code IS NOT NULL OR edition IS NOT NULL)
            THEN RAISE EXCEPTION 'Cannot discard conversation message history'; END IF;
        END $$;
    """))
    op.drop_table("answer_attempts")
    op.drop_table("conversation_messages")
    for attribute in ("edition", "model_code", "doc_code"):
        op.drop_column("documents", attribute)
