"""One local profile, without adopting existing multi-user records."""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0002_local_single_user"
down_revision = "0001_m0_accounts"
branch_labels = None
depends_on = None


def upgrade():
    profiles = op.create_table(
        "local_profiles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("singleton", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("singleton IS TRUE", name="ck_local_profiles_singleton"),
        sa.UniqueConstraint("singleton", name="uq_local_profiles_singleton"),
    )
    op.bulk_insert(profiles, [{"id": uuid4(), "singleton": True}])
    for table in ("knowledge_bases", "conversations"):
        op.add_column(table, sa.Column("local_owner_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_local_owner", table, "local_profiles",
            ["local_owner_id"], ["id"], ondelete="RESTRICT",
        )
    op.create_index("ix_knowledge_bases_local_owner_id", "knowledge_bases", ["local_owner_id"])
    op.create_index(
        "ix_conversations_local_owner_created", "conversations",
        ["local_owner_id", "created_at", "id"],
    )
    # Legacy account IDs and rows remain unchanged. New local conversations use
    # only local_owner_id; legacy rows keep local_owner_id NULL and stay private.
    op.alter_column("conversations", "owner_id", existing_type=sa.Uuid(), nullable=True)


def downgrade():
    # Rollback must not erase local ownership or reinterpret local data as shared.
    # The PostgreSQL DO block also works in offline migration SQL.
    op.execute(sa.text("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM knowledge_bases WHERE local_owner_id IS NOT NULL)
               OR EXISTS (SELECT 1 FROM conversations WHERE local_owner_id IS NOT NULL)
            THEN
                RAISE EXCEPTION 'Cannot downgrade while local-owned data exists';
            END IF;
        END $$;
    """))
    op.alter_column("conversations", "owner_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_index("ix_conversations_local_owner_created", table_name="conversations")
    op.drop_index("ix_knowledge_bases_local_owner_id", table_name="knowledge_bases")
    for table in ("conversations", "knowledge_bases"):
        op.drop_constraint(f"fk_{table}_local_owner", table, type_="foreignkey")
        op.drop_column(table, "local_owner_id")
    op.drop_table("local_profiles")
