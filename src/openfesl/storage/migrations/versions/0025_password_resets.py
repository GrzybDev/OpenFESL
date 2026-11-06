"""Links e-mailed to accounts' addresses that set a new game password once opened

Revision ID: 0025
Revises: 0024
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"


def upgrade() -> None:
    op.create_table(
        "password_reset",
        sa.Column("token_hash", sa.LargeBinary(), primary_key=True),
        sa.Column(
            "account_id",
            sa.BigInteger(),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("password_reset")
