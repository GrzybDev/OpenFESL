"""The products of the games' stores, numbered with their bundles

Revision ID: 0030
Revises: 0029
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"


def upgrade() -> None:
    op.execute(sa.schema.CreateSequence(sa.Sequence("store_id")))
    op.create_table(
        "store_product",
        sa.Column(
            "id",
            sa.BigInteger(),
            server_default=sa.text("nextval('store_id')"),
            primary_key=True,
        ),
        sa.Column("game", sa.String(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("days", sa.Integer(), nullable=True),
        sa.Column("uses", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("price", sa.BigInteger(), nullable=False),
        sa.Column("original_price", sa.BigInteger(), nullable=True),
        sa.Column("account", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("published", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("sort", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "game",
            "item_id",
            "type",
            "days",
            "uses",
            "currency",
            postgresql_nulls_not_distinct=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("store_product")
    op.execute(sa.schema.DropSequence(sa.Sequence("store_id")))
