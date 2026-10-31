"""The admin's own offers: their texts in other languages and the discount they take

Revision ID: 0046
Revises: 0045
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "store_bundle",
        sa.Column("texts", postgresql.JSONB(), server_default="{}", nullable=False),
    )
    op.add_column("store_bundle", sa.Column("discount", sa.SmallInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("store_bundle", "discount")
    op.drop_column("store_bundle", "texts")
