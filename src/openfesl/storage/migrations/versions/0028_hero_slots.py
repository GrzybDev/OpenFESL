"""The hero slots each account bought on top of a game's free ones

Revision ID: 0028
Revises: 0027
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "hero_account",
        sa.Column("bought_slots", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("hero_account", "bought_slots")
