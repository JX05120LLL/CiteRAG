"""Store private M3 images and their message bindings without changing old chats."""

import sqlalchemy as sa
from alembic import op

revision = "0008_image_attachments"
down_revision = "0007_partial_answers"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "image_attachments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Uuid(),
            sa.ForeignKey("local_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("storage_key", sa.String(100), nullable=False, unique=True),
        sa.Column("filename", sa.String(240), nullable=False),
        sa.Column("mime_type", sa.String(30), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("observation", sa.Text()),
        sa.Column("observation_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("needs_confirmation", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("confirmed_identifier", sa.String(80)),
        sa.Column("observation_model", sa.String(80)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("size > 0 AND size <= 10485760", name="ck_image_attachments_size"),
        sa.CheckConstraint(
            "width > 0 AND height > 0 AND width * height <= 16000000",
            name="ck_image_attachments_pixels",
        ),
        sa.CheckConstraint(
            "mime_type IN ('image/png','image/jpeg')", name="ck_image_attachments_mime"
        ),
        sa.CheckConstraint(
            "observation_status IN ('pending','ready','failed')",
            name="ck_image_attachments_observation",
        ),
    )
    op.create_index("ix_image_attachments_expiry", "image_attachments", ["expires_at"])
    op.create_index("ix_image_attachments_conversation", "image_attachments", ["conversation_id"])
    op.create_table(
        "message_images",
        sa.Column(
            "message_id",
            sa.Uuid(),
            sa.ForeignKey("conversation_messages.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "attachment_id",
            sa.Uuid(),
            sa.ForeignKey("image_attachments.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("ix_message_images_attachment", "message_images", ["attachment_id"])


def downgrade():
    op.execute(
        sa.text("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM image_attachments) THEN
            RAISE EXCEPTION 'Cannot discard private image history';
        END IF;
    END $$;""")
    )
    op.drop_index("ix_message_images_attachment", table_name="message_images")
    op.drop_table("message_images")
    op.drop_index("ix_image_attachments_conversation", table_name="image_attachments")
    op.drop_index("ix_image_attachments_expiry", table_name="image_attachments")
    op.drop_table("image_attachments")
