"""Allow a saved, explicitly unverified tail after interrupted streaming."""

import sqlalchemy as sa
from alembic import op

revision = "0007_partial_answers"
down_revision = "0006_m1_lifecycle"
branch_labels = None
depends_on = None

_OLD = ("status IN ('running','answered','insufficient_evidence',"
        "'needs_clarification','conflicting_evidence','failed','interrupted')")
_NEW = ("status IN ('running','answered','insufficient_evidence',"
        "'needs_clarification','conflicting_evidence','failed','interrupted','partial')")


def upgrade():
    op.drop_constraint("ck_answer_attempt_status", "answer_attempts", type_="check")
    op.create_check_constraint("ck_answer_attempt_status", "answer_attempts", _NEW)


def downgrade():
    op.execute(sa.text("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM answer_attempts WHERE status = 'partial')
            THEN RAISE EXCEPTION 'Cannot discard partial answer history'; END IF;
        END $$;
    """))
    op.drop_constraint("ck_answer_attempt_status", "answer_attempts", type_="check")
    op.create_check_constraint("ck_answer_attempt_status", "answer_attempts", _OLD)
