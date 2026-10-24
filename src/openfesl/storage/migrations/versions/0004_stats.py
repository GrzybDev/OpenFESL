"""Stats and leaderboards

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stat",
        sa.Column("partition", sa.String(), primary_key=True),
        sa.Column("owner_type", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.BigInteger(), primary_key=True),
        sa.Column("period", sa.SmallInteger(), primary_key=True),
        sa.Column("period_start", sa.Date(), primary_key=True),
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", sa.Double(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
    )
    op.create_index(
        "stat_leaderboard",
        "stat",
        ["partition", "owner_type", "key", "period", "period_start", "value"],
    )


def downgrade() -> None:
    op.drop_table("stat")
