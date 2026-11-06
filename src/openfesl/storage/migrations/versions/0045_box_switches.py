"""The switch that takes a box off sale, or stops keys from opening a chest

Revision ID: 0045
Revises: 0044
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"


def upgrade() -> None:
    op.add_column(
        "store_box", sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False)
    )


def downgrade() -> None:
    op.drop_column("store_box", "enabled")
