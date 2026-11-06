"""The heroes' stats when an event with goals began, which its goals count from

Revision ID: 0036
Revises: 0035
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0036"
down_revision: str | None = "0035"


def upgrade() -> None:
    op.create_table(
        "reward_baseline",
        sa.Column("game", sa.String(), primary_key=True),
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("stat", sa.String(), primary_key=True),
        sa.Column("values", JSONB(), nullable=False),
        sa.Column(
            "taken_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("reward_baseline")
