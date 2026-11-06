"""The pictures that the admin uploaded for hot deals in place of the generated ones

Revision ID: 0044
Revises: 0043
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0044"
down_revision: str | None = "0043"


def upgrade() -> None:
    op.create_table(
        "deal_art",
        sa.Column("game", sa.String(), nullable=False),
        sa.Column("offer", sa.String(), nullable=False),
        sa.Column("big", sa.Boolean(), nullable=False),
        sa.Column("image", sa.LargeBinary(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("game", "offer", "big"),
    )


def downgrade() -> None:
    op.drop_table("deal_art")
