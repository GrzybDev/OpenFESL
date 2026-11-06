"""Codes that give gifts: their end, their gift and batch, and what each redemption gave

Revision ID: 0048
Revises: 0047
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0048"
down_revision: str | None = "0047"


def upgrade() -> None:
    op.add_column("product_key", sa.Column("expires_at", sa.DateTime(timezone=True)))
    op.add_column("product_key", sa.Column("gift", postgresql.JSONB()))
    op.add_column("product_key", sa.Column("batch", sa.BigInteger()))
    op.create_index("ix_product_key_batch", "product_key", ["batch"])
    op.add_column(
        "product_key_use",
        sa.Column("persona_id", sa.BigInteger(), sa.ForeignKey("persona.id", ondelete="SET NULL")),
    )
    op.add_column("product_key_use", sa.Column("outcome", postgresql.JSONB()))


def downgrade() -> None:
    op.drop_column("product_key_use", "outcome")
    op.drop_column("product_key_use", "persona_id")
    op.drop_index("ix_product_key_batch", table_name="product_key")
    op.drop_column("product_key", "batch")
    op.drop_column("product_key", "gift")
    op.drop_column("product_key", "expires_at")
