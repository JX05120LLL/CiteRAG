"""M0 accounts, revocable sessions and private conversation bindings."""

import sqlalchemy as sa
from alembic import op

revision = "0001_m0_accounts"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("role IN ('admin', 'user')", name="ck_users_role"),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="empty"),
        sa.Column("active_workspace", sa.String(100), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('empty', 'ready', 'maintaining', 'blocked')",
            name="ck_knowledge_bases_status",
        ),
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "kb_id",
            sa.Uuid(),
            sa.ForeignKey("knowledge_bases.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_conversations_owner_created", "conversations", ["owner_id", "created_at", "id"]
    )


def downgrade():
    op.drop_table("conversations")
    op.drop_table("knowledge_bases")
    op.drop_table("auth_sessions")
    op.drop_table("users")
