"""Public labels of product keys and the game a key added by the admin is for

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"


def upgrade() -> None:
    op.add_column(
        "product_key",
        sa.Column("labels", postgresql.JSONB(), server_default="{}", nullable=False),
    )
    op.add_column("product_key", sa.Column("game", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("product_key", "game")
    op.drop_column("product_key", "labels")
