"""Add revision-bound summaries and durable document retirement states."""

import sqlalchemy as sa
from alembic import op

revision = "0006_m1_lifecycle"
down_revision = "0005_answer_attempts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "conversation_summaries",
        sa.Column("conversation_id", sa.Uuid(),
                  sa.ForeignKey("conversations.id"), primary_key=True),
        sa.Column("kb_revision", sa.Integer(), nullable=False),
        sa.Column("workspace", sa.String(100), nullable=False),
        sa.Column("through_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("through_message_id", sa.Uuid(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.drop_constraint("uq_documents_content", "documents", type_="unique")
    op.create_index("uq_documents_active_content", "documents", ["kb_id", "sha256"],
                    unique=True, postgresql_where=sa.text("status <> 'deleted'"))
    op.drop_constraint("ck_documents_status", "documents", type_="check")
    op.create_check_constraint("ck_documents_status", "documents",
        "status IN ('pending','parsing','parsed','indexing','ready','failed',"
        "'deleting','replacing','deleted')")
    op.drop_constraint("ck_jobs_operation", "ingestion_jobs", type_="check")
    op.create_check_constraint("ck_jobs_operation", "ingestion_jobs",
        "operation IN ('upload','rebuild','delete','replace')")
    op.drop_constraint("ck_jobs_stage", "ingestion_jobs", type_="check")
    op.create_check_constraint("ck_jobs_stage", "ingestion_jobs",
        "stage IN ('accepted','parsing','parsed','indexing','verifying','cleanup','complete')")


def downgrade():
    op.execute(sa.text("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM conversation_summaries)
               OR EXISTS (SELECT 1 FROM documents WHERE status IN
                          ('deleting','replacing','deleted'))
               OR EXISTS (SELECT 1 FROM ingestion_jobs WHERE operation IN ('delete','replace')
                          OR stage = 'cleanup' OR cleanup_pending)
            THEN RAISE EXCEPTION 'Cannot discard M1 lifecycle history'; END IF;
        END $$;
    """))
    op.drop_constraint("ck_jobs_stage", "ingestion_jobs", type_="check")
    op.create_check_constraint("ck_jobs_stage", "ingestion_jobs",
        "stage IN ('accepted','parsing','parsed','indexing','verifying','complete')")
    op.drop_constraint("ck_jobs_operation", "ingestion_jobs", type_="check")
    op.create_check_constraint("ck_jobs_operation", "ingestion_jobs",
        "operation IN ('upload','rebuild')")
    op.drop_constraint("ck_documents_status", "documents", type_="check")
    op.create_check_constraint("ck_documents_status", "documents",
        "status IN ('pending','parsing','parsed','indexing','ready','failed')")
    op.drop_index("uq_documents_active_content", table_name="documents")
    op.create_unique_constraint("uq_documents_content", "documents", ["kb_id", "sha256"])
    op.drop_table("conversation_summaries")
