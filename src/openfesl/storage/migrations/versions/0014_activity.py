"""Peaks of each game's players online by hour and by day

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "activity_hour",
        sa.Column("game", sa.String(), primary_key=True),
        sa.Column("hour", sa.DateTime(timezone=True), primary_key=True),
        *_counts(),
    )
    op.create_table(
        "activity_day",
        sa.Column("game", sa.String(), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        *_counts(),
    )


def _counts() -> list[sa.Column[Any]]:
    # The count with the most players online in the hour or day, and when it was taken.
    return [
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("online", sa.Integer(), nullable=False),
        sa.Column("in_matches", sa.Integer(), nullable=False),
        sa.Column("servers", sa.Integer(), nullable=False),
    ]


def downgrade() -> None:
    op.drop_table("activity_day")
    op.drop_table("activity_hour")
