"""Boxes of the games' stores, which give one of their drops at random

Revision ID: 0033
Revises: 0032
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "store_box",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("game", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("value", sa.BigInteger(), nullable=False),
        sa.Column("worth_rule", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("faction", sa.String(), nullable=True),
        sa.Column("hero_class", sa.String(), nullable=True),
        sa.Column(
            "fallback_id",
            sa.BigInteger(),
            sa.ForeignKey("store_product.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("game", "key"),
    )
    op.create_table(
        "store_drop",
        sa.Column(
            "box_id",
            sa.BigInteger(),
            sa.ForeignKey("store_box.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "product_id",
            sa.BigInteger(),
            sa.ForeignKey("store_product.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False),
    )
    op.create_index("ix_store_drop_product_id", "store_drop", ["product_id"])
    op.add_column(
        "store_bundle",
        sa.Column(
            "box_id",
            sa.BigInteger(),
            sa.ForeignKey("store_box.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("ix_store_bundle_box_id", "store_bundle", ["box_id"])


def downgrade() -> None:
    op.drop_column("store_bundle", "box_id")
    op.drop_table("store_drop")
    op.drop_table("store_box")
