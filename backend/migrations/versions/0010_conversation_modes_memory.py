"""Permit ordinary conversations and store source-linked knowledge-base memory."""

import sqlalchemy as sa
from alembic import op

revision = "0010_conversation_modes_memory"
down_revision = "0009_conversation_archive"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("conversations", "kb_id", existing_type=sa.UUID(), nullable=True)
    op.create_table(
        "knowledge_memories",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("owner_id", sa.UUID(), sa.ForeignKey(
            "local_profiles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kb_id", sa.UUID(), sa.ForeignKey(
            "knowledge_bases.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_conversation_id", sa.UUID(), sa.ForeignKey(
            "conversations.id"), nullable=False),
        sa.Column("source_message_id", sa.UUID(), sa.ForeignKey(
            "conversation_messages.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("content", sa.String(300), nullable=False),
        sa.Column("kb_revision", sa.Integer(), nullable=False),
        sa.Column("workspace", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('preference','background')", name="ck_knowledge_memory_kind"),
    )
    op.create_index("ix_knowledge_memories_scope", "knowledge_memories",
                    ["kb_id", "created_at", "id"])


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM knowledge_memories LIMIT 1")).first() or bind.execute(
        sa.text("SELECT 1 FROM conversations WHERE kb_id IS NULL LIMIT 1")
    ).first():
        raise RuntimeError("Delete or export ordinary chats and shared memories before downgrade")
    op.drop_index("ix_knowledge_memories_scope", table_name="knowledge_memories")
    op.drop_table("knowledge_memories")
    op.alter_column("conversations", "kb_id", existing_type=sa.UUID(), nullable=False)
