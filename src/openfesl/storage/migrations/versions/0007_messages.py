"""Messages

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "message",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column(
            "sender_id",
            sa.BigInteger(),
            sa.ForeignKey("persona.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("purge", sa.String(), nullable=False),
        sa.Column("attachments", postgresql.JSONB(), nullable=False),
        sa.Column(
            "sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outbox", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_message_sender_id", "message", ["sender_id"])
    op.create_index("ix_message_expires_at", "message", ["expires_at"])
    op.create_table(
        "message_recipient",
        sa.Column(
            "message_id",
            sa.Integer(),
            sa.ForeignKey("message.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "persona_id",
            sa.BigInteger(),
            sa.ForeignKey("persona.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("inbox", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_message_recipient_persona_id", "message_recipient", ["persona_id"])


def downgrade() -> None:
    op.drop_table("message_recipient")
    op.drop_table("message")
