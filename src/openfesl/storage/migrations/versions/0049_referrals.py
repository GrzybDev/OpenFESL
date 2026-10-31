"""Recruit a Friend: who recruited each account made through a personal link, and when its steps
were reached

Revision ID: 0049
Revises: 0048
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0049"
down_revision: str | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "referral",
        sa.Column(
            "recruit_id",
            sa.BigInteger(),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("game", sa.String(), nullable=False),
        sa.Column(
            "recruiter_id",
            sa.BigInteger(),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "reached", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
    )
    op.create_index("ix_referral_recruiter_id", "referral", ["recruiter_id"])


def downgrade() -> None:
    op.drop_index("ix_referral_recruiter_id", table_name="referral")
    op.drop_table("referral")
