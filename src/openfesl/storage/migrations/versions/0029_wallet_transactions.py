"""The id that a sender gives a credit, which the ledger keeps once

Revision ID: 0029
Revises: 0028
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"


def upgrade() -> None:
    op.add_column("wallet_entry", sa.Column("transaction", sa.String(), nullable=True))
    op.create_index(
        "wallet_entry_transaction_key",
        "wallet_entry",
        ["transaction"],
        unique=True,
        postgresql_where=sa.text('"transaction" IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index("wallet_entry_transaction_key", "wallet_entry")
    op.drop_column("wallet_entry", "transaction")
