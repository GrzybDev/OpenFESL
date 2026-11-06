"""The models and textures of heroes that an imported catalogue keeps

Revision ID: 0041
Revises: 0040
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"


def upgrade() -> None:
    for column in ("models", "textures"):
        op.add_column(
            "catalogue", sa.Column(column, sa.Integer(), server_default="0", nullable=False)
        )


def downgrade() -> None:
    for column in ("models", "textures"):
        op.drop_column("catalogue", column)
