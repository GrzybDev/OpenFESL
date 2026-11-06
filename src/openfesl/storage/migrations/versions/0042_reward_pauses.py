"""The days on which the rewards of the games' sites were paused

Revision ID: 0042
Revises: 0041
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"


def upgrade() -> None:
    op.create_table(
        "reward_pause",
        sa.Column("game", sa.String(), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("reward_pause")
