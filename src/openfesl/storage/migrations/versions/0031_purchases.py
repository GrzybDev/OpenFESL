"""The purchases made in the games' stores, with what each brought

Revision ID: 0031
Revises: 0030
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031"
down_revision: str | None = "0030"


def upgrade() -> None:
    op.create_table(
        "purchase",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("game", sa.String(), nullable=False),
        sa.Column(
            "account_id",
            sa.BigInteger(),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "persona_id",
            sa.BigInteger(),
            sa.ForeignKey("persona.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("product_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("price", sa.BigInteger(), nullable=False),
        sa.Column("outcome", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_purchase_account_id", "purchase", ["account_id"])
    op.create_index("ix_purchase_persona_id", "purchase", ["persona_id"])


def downgrade() -> None:
    op.drop_table("purchase")
