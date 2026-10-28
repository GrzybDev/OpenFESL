"""Bundles of the games' stores, which hot deals sell, numbered with the products

Revision ID: 0032
Revises: 0031
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "store_bundle",
        sa.Column(
            "id",
            sa.BigInteger(),
            server_default=sa.text("nextval('store_id')"),
            primary_key=True,
        ),
        sa.Column("game", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("price", sa.BigInteger(), nullable=False),
        sa.Column("original_price", sa.BigInteger(), nullable=True),
        sa.Column("faction", sa.String(), nullable=True),
        sa.Column("hero_class", sa.String(), nullable=True),
        sa.Column("game_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "store_bundle_member",
        sa.Column(
            "bundle_id",
            sa.BigInteger(),
            sa.ForeignKey("store_bundle.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "product_id",
            sa.BigInteger(),
            sa.ForeignKey("store_product.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("ix_store_bundle_member_product_id", "store_bundle_member", ["product_id"])


def downgrade() -> None:
    op.drop_table("store_bundle_member")
    op.drop_table("store_bundle")
