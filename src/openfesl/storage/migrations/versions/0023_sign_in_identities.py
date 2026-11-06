"""Accounts' identities at the sign-in services linked to them

Revision ID: 0023
Revises: 0022
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"


def upgrade() -> None:
    op.create_table(
        "sign_in_identity",
        sa.Column("service", sa.String(), primary_key=True),
        sa.Column("subject", sa.String(), primary_key=True),
        sa.Column(
            "account_id",
            sa.BigInteger(),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "linked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("account_id", "service"),
    )


def downgrade() -> None:
    op.drop_table("sign_in_identity")
