"""Use limits of product keys

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("product_key", sa.Column("max_uses", sa.Integer(), nullable=True))
    op.add_column(
        "product_key",
        sa.Column("once_per_account", sa.Boolean(), server_default="false", nullable=False),
    )
    op.create_table(
        "product_key_use",
        sa.Column(
            "key_id",
            sa.BigInteger(),
            sa.ForeignKey("product_key.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "account_id",
            sa.BigInteger(),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            primary_key=True,
            index=True,
        ),
        sa.Column(
            "used_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("product_key_use")
    op.drop_column("product_key", "once_per_account")
    op.drop_column("product_key", "max_uses")
