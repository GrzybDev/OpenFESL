"""The stock of admin offers and how many of each bundle were sold

Revision ID: 0047
Revises: 0046
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"


def upgrade() -> None:
    op.add_column("store_bundle", sa.Column("stock", sa.Integer(), nullable=True))
    op.add_column(
        "store_bundle", sa.Column("sold", sa.Integer(), server_default="0", nullable=False)
    )


def downgrade() -> None:
    op.drop_column("store_bundle", "sold")
    op.drop_column("store_bundle", "stock")
