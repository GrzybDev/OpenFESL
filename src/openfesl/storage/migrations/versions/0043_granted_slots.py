"""The hero slots that the admin granted each account on top of a game's free and bought ones

Revision ID: 0043
Revises: 0042
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "hero_account",
        sa.Column("granted_slots", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("hero_account", "granted_slots")
