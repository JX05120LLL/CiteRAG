"""Add an optional archive marker without changing existing conversations."""

import sqlalchemy as sa
from alembic import op

revision = "0009_conversation_archive"
down_revision = "0008_image_attachments"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("conversations", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.create_index("ix_conversations_owner_archive_created", "conversations",
                    ["local_owner_id", "archived_at", "created_at", "id"])


def downgrade():
    op.drop_index("ix_conversations_owner_archive_created", table_name="conversations")
    op.drop_column("conversations", "archived_at")
