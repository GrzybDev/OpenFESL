"""The hot deals' sections and the rotation's picks

Revision ID: 0034
Revises: 0033
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("store_bundle", sa.Column("section", sa.String(), nullable=True))
    op.add_column("store_bundle", sa.Column("pool", sa.String(), nullable=True))
    op.add_column(
        "store_bundle", sa.Column("source", sa.String(), server_default="admin", nullable=False)
    )
    op.create_unique_constraint(
        "store_bundle_game_section_pool_starts_at_key",
        "store_bundle",
        ["game", "section", "pool", "starts_at"],
    )


def downgrade() -> None:
    op.drop_constraint("store_bundle_game_section_pool_starts_at_key", "store_bundle")
    for column in ("source", "pool", "section"):
        op.drop_column("store_bundle", column)
