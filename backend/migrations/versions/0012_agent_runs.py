"""Durable Agent runs; no provider calls and no automatic checkpoint setup."""

import sqlalchemy as sa
from alembic import op

revision = "0012_agent_runs"
down_revision = "0011_tool_gateway"
branch_labels = None
depends_on = None

ACTIVE = "status IN ('running','waiting_input','waiting_approval')"
ANSWER = (
    "status IN ('running','answered','insufficient_evidence','needs_clarification',"
    "'conflicting_evidence','failed','interrupted','partial','waiting_input','waiting_approval')"
)


def upgrade():
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("owner_id", sa.UUID(), sa.ForeignKey("local_profiles.id"), nullable=False),
        sa.Column(
            "conversation_id",
            sa.UUID(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            sa.UUID(),
            sa.ForeignKey("conversation_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "attempt_id",
            sa.UUID(),
            sa.ForeignKey("answer_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_id", sa.UUID(), nullable=False),
        sa.Column("kb_id", sa.UUID(), sa.ForeignKey("knowledge_bases.id")),
        sa.Column("kb_revision", sa.Integer(), nullable=False),
        sa.Column("workspace", sa.String(100), nullable=False),
        sa.Column("graph_version", sa.String(40), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        *[
            sa.Column(
                name,
                sa.Integer(),
                nullable=False,
                server_default="1" if name == "generation" else "0",
            )
            for name in ("generation", "model_rounds", "tool_attempts", "active_ms", "event_seq")
        ],
        sa.Column("prepared", sa.JSON()),
        sa.Column("waiting", sa.JSON()),
        sa.Column("resume_id", sa.UUID()),
        sa.Column("resume_payload", sa.JSON()),
        sa.Column("runner_id", sa.UUID()),
        *[
            sa.Column(name, sa.DateTime(timezone=True))
            for name in ("lease_until", "wait_until", "finished_at")
        ],
        sa.Column("voice_session_id", sa.UUID()),
        sa.Column("voice_generation", sa.Integer()),
        sa.Column("error_code", sa.String(60)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("conversation_id", "request_id", name="uq_agent_request"),
        sa.UniqueConstraint("attempt_id", name="uq_agent_attempt"),
        sa.CheckConstraint(
            "status IN ('running','waiting_input','waiting_approval','completed',"
            "'failed','interrupted','cancelled','expired')",
            name="ck_agent_status",
        ),
    )
    op.create_index(
        "uq_agent_active_chat",
        "agent_runs",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE),
    )
    op.create_table(
        "agent_steps",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "run_id", sa.UUID(), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("data", sa.JSON()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("run_id", "key", name="uq_agent_step"),
    )
    op.create_table(
        "agent_events",
        sa.Column(
            "run_id",
            sa.UUID(),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("seq", sa.Integer(), primary_key=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    for name, table in (("run_id", "agent_runs"), ("step_id", "agent_steps")):
        op.add_column(
            "tool_calls",
            sa.Column(name, sa.UUID(), sa.ForeignKey(f"{table}.id", ondelete="SET NULL")),
        )
    for name, length in (("tool_version", 80), ("arguments_hash", 64), ("effect", 16)):
        op.add_column("tool_calls", sa.Column(name, sa.String(length)))
    op.add_column("tool_calls", sa.Column("decision_id", sa.UUID()))
    op.drop_constraint("ck_tool_calls_status", "tool_calls")
    op.create_check_constraint(
        "ck_tool_calls_status",
        "tool_calls",
        "status IN ('pending_approval','running','succeeded','failed','rejected',"
        "'interrupted','unknown')",
    )
    op.drop_constraint("ck_answer_attempt_status", "answer_attempts")
    op.create_check_constraint("ck_answer_attempt_status", "answer_attempts", ANSWER)
    op.drop_index("uq_answer_attempt_active", table_name="answer_attempts")
    op.create_index(
        "uq_answer_attempt_active",
        "answer_attempts",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE),
    )
    # One independent checkpoint schema per business schema, including test isolation.
    op.execute("""DO $$ DECLARE target text; BEGIN
      target := CASE WHEN current_schema()='public' THEN 'agent_checkpoints'
                ELSE 'agent_cp_' || md5(current_schema()) END;
      EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I', target);
    END $$""")


def downgrade():
    connection = op.get_bind()
    if (
        connection.execute(sa.text("SELECT 1 FROM agent_runs LIMIT 1")).first()
        or connection.execute(
            sa.text(
                "SELECT 1 FROM tool_calls WHERE run_id IS NOT NULL OR status='unknown' "
                "OR tool_version IS NOT NULL OR arguments_hash IS NOT NULL "
                "OR effect IS NOT NULL OR decision_id IS NOT NULL LIMIT 1"
            )
        ).first()
        or connection.execute(
            sa.text(
                "SELECT 1 FROM answer_attempts WHERE "
                "status IN ('waiting_input','waiting_approval') LIMIT 1"
            )
        ).first()
    ):
        raise RuntimeError("Export Agent records before downgrade; no destructive rollback")
    op.execute("""DO $$ DECLARE target text; item text; has_data boolean; BEGIN
      target := CASE WHEN current_schema()='public' THEN 'agent_checkpoints'
                ELSE 'agent_cp_' || md5(current_schema()) END;
      FOR item IN SELECT tablename FROM pg_tables WHERE schemaname=target
                  AND tablename<>'checkpoint_migrations' LOOP
        EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I.%I)', target,item) INTO has_data;
        IF has_data THEN RAISE EXCEPTION 'Export Agent checkpoints before downgrade'; END IF;
      END LOOP;
      EXECUTE format('DROP SCHEMA %I CASCADE', target);
    END $$""")
    op.drop_index("uq_answer_attempt_active", table_name="answer_attempts")
    op.create_index(
        "uq_answer_attempt_active",
        "answer_attempts",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("status='running'"),
    )
    op.drop_constraint("ck_answer_attempt_status", "answer_attempts")
    op.create_check_constraint(
        "ck_answer_attempt_status",
        "answer_attempts",
        ANSWER.replace(",'waiting_input','waiting_approval'", ""),
    )
    op.drop_constraint("ck_tool_calls_status", "tool_calls")
    op.create_check_constraint(
        "ck_tool_calls_status",
        "tool_calls",
        "status IN ('pending_approval','running','succeeded','failed','rejected','interrupted')",
    )
    for name in ("run_id", "step_id", "tool_version", "arguments_hash", "effect", "decision_id"):
        op.drop_column("tool_calls", name)
    op.drop_table("agent_events")
    op.drop_table("agent_steps")
    op.drop_index("uq_agent_active_chat", table_name="agent_runs")
    op.drop_table("agent_runs")
