"""Links e-mailed to accounts' addresses that link a sign-in service's identity once opened

Revision ID: 0024
Revises: 0023
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"


def upgrade() -> None:
    op.create_table(
        "sign_in_proof",
        sa.Column("token_hash", sa.LargeBinary(), primary_key=True),
        sa.Column(
            "account_id",
            sa.BigInteger(),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("service", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sign_in_proof_account_id", "sign_in_proof", ["account_id"])


def downgrade() -> None:
    op.drop_table("sign_in_proof")
