"""Persist scoped tool decisions and sanitized outcomes."""

import sqlalchemy as sa
from alembic import op

revision = "0011_tool_gateway"
down_revision = "0010_conversation_modes_memory"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("owner_id", sa.UUID(), sa.ForeignKey("local_profiles.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("conversation_id", sa.UUID(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("request_id", sa.UUID(), nullable=False),
        sa.Column("kb_id", sa.UUID(), sa.ForeignKey("knowledge_bases.id")),
        sa.Column("kb_revision", sa.Integer(), nullable=False),
        sa.Column("workspace", sa.String(100), nullable=False),
        sa.Column("tool_id", sa.String(80), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("impact", sa.String(300), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("result", sa.JSON()),
        sa.Column("error_code", sa.String(60)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("conversation_id", "request_id", name="uq_tool_calls_request"),
        sa.CheckConstraint("status IN ('pending_approval','running','succeeded','failed',"
                           "'rejected','interrupted')", name="ck_tool_calls_status"),
    )
    op.create_index("ix_tool_calls_conversation_created", "tool_calls",
                    ["conversation_id", "created_at", "id"])
    op.create_index("uq_tool_calls_active_conversation", "tool_calls", ["conversation_id"],
                    unique=True, postgresql_where=sa.text(
                        "status IN ('pending_approval','running')"))


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM tool_calls LIMIT 1")).first():
        raise RuntimeError("Export or remove tool call records before downgrade")
    op.drop_index("uq_tool_calls_active_conversation", table_name="tool_calls")
    op.drop_index("ix_tool_calls_conversation_created", table_name="tool_calls")
    op.drop_table("tool_calls")
