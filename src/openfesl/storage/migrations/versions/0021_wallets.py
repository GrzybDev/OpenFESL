"""Wallets of players' currencies with the ledgers of their changes

Revision ID: 0021
Revises: 0020
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"


def upgrade() -> None:
    op.create_table(
        "wallet",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "account_id",
            sa.BigInteger(),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("game", sa.String(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column(
            "persona_id",
            sa.BigInteger(),
            sa.ForeignKey("persona.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("balance", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint(
            "account_id", "game", "currency", "persona_id", postgresql_nulls_not_distinct=True
        ),
    )
    op.create_index("ix_wallet_persona_id", "wallet", ["persona_id"])
    op.create_table(
        "wallet_entry",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "wallet_id",
            sa.BigInteger(),
            sa.ForeignKey("wallet.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("delta", sa.BigInteger(), nullable=False),
        sa.Column("balance", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_wallet_entry_wallet_id", "wallet_entry", ["wallet_id"])


def downgrade() -> None:
    op.drop_table("wallet_entry")
    op.drop_table("wallet")
