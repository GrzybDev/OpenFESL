"""The games' icons, which Discord presence shows

Revision ID: 0019
Revises: 0018
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "game_icon",
        sa.Column("game", sa.String(), primary_key=True),
        sa.Column("png", sa.LargeBinary(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("game_icon")
