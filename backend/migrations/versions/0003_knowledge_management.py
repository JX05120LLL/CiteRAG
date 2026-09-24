"""Persist knowledge creation idempotency without claiming or changing old data."""

import sqlalchemy as sa
from alembic import op

revision = "0003_knowledge_management"
down_revision = "0002_local_single_user"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("knowledge_bases", sa.Column("create_request_id", sa.Uuid(), nullable=True))
    op.add_column(
        "knowledge_bases", sa.Column("create_request_name", sa.String(120), nullable=True)
    )
    op.create_unique_constraint(
        "uq_knowledge_bases_create_request", "knowledge_bases",
        ["local_owner_id", "create_request_id"],
    )
    op.create_check_constraint(
        "ck_knowledge_bases_create_request", "knowledge_bases",
        "(create_request_id IS NULL AND create_request_name IS NULL) OR "
        "(create_request_id IS NOT NULL AND create_request_name IS NOT NULL "
        "AND local_owner_id IS NOT NULL)",
    )


def downgrade():
    # Removing accepted request keys could duplicate data when a client retries.
    op.execute(sa.text("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM knowledge_bases WHERE create_request_id IS NOT NULL)
            THEN
                RAISE EXCEPTION 'Cannot discard knowledge creation idempotency';
            END IF;
        END $$;
    """))
    op.drop_constraint("ck_knowledge_bases_create_request", "knowledge_bases", type_="check")
    op.drop_constraint("uq_knowledge_bases_create_request", "knowledge_bases", type_="unique")
    op.drop_column("knowledge_bases", "create_request_name")
    op.drop_column("knowledge_bases", "create_request_id")
