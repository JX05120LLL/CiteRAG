"""Append managed files and durable jobs; never adopt or delete legacy data."""
import sqlalchemy as sa
from alembic import op

revision = "0004_managed_ingestion"
down_revision = "0003_knowledge_management"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("knowledge_bases", sa.Column("revision", sa.Integer(),
                                             nullable=False, server_default="0"))
    op.add_column("knowledge_bases", sa.Column("hide_history_before_revision", sa.Integer(),
                                             nullable=False, server_default="0"))
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kb_id", sa.Uuid(), sa.ForeignKey("knowledge_bases.id"), nullable=False),
        sa.Column("filename", sa.String(240), nullable=False),
        sa.Column("storage_key", sa.String(100), nullable=False, unique=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(60)),
        sa.Column("source_key", sa.String(100), nullable=False, unique=True),
        sa.Column("engine_doc_id", sa.String(100), nullable=False, unique=True),
        sa.Column("parsed_text", sa.Text()),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("indexed_once", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("kb_id", "sha256", name="uq_documents_content"),
        sa.CheckConstraint("status IN ('pending','parsing','parsed','indexing','ready','failed')",
                           name="ck_documents_status"),
    )
    op.create_index("ix_documents_kb_id", "documents", ["kb_id"])
    op.create_table(
        "parsed_blocks",
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"), primary_key=True),
        sa.Column("ordinal", sa.Integer(), primary_key=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("locator", sa.JSON(), nullable=False),
        sa.Column("start", sa.Integer(), nullable=False),
        sa.Column("end", sa.Integer(), nullable=False),
    )
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kb_id", sa.Uuid(), sa.ForeignKey("knowledge_bases.id"), nullable=False),
        sa.Column("client_request_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("document_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("engine_mutated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("target_workspace", sa.String(100)),
        sa.Column("retired_workspace", sa.String(100)),
        sa.Column("cleanup_pending", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("error_code", sa.String(60)),
        sa.Column("message", sa.String(300)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("kb_id", "client_request_id", name="uq_jobs_request"),
        sa.CheckConstraint("operation IN ('upload','rebuild')", name="ck_jobs_operation"),
        sa.CheckConstraint("status IN ('queued','running','succeeded','failed','interrupted')",
                           name="ck_jobs_status"),
        sa.CheckConstraint(
            "stage IN ('accepted','parsing','parsed','indexing','verifying','complete')",
            name="ck_jobs_stage",
        ),
    )
    op.create_index("ix_ingestion_jobs_kb_id", "ingestion_jobs", ["kb_id"])
    op.create_index("ix_jobs_status_created", "ingestion_jobs", ["status", "created_at"])
    op.create_index("uq_jobs_active_kb", "ingestion_jobs", ["kb_id"], unique=True,
                    postgresql_where=sa.text("status IN ('queued','running')"))


def downgrade():
    op.execute(sa.text("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM documents) OR EXISTS (SELECT 1 FROM ingestion_jobs)
               OR EXISTS (SELECT 1 FROM knowledge_bases WHERE revision <> 0
                          OR hide_history_before_revision <> 0)
            THEN RAISE EXCEPTION 'Cannot discard managed ingestion history'; END IF;
        END $$;
    """))
    op.drop_table("ingestion_jobs")
    op.drop_table("parsed_blocks")
    op.drop_table("documents")
    op.drop_column("knowledge_bases", "hide_history_before_revision")
    op.drop_column("knowledge_bases", "revision")
