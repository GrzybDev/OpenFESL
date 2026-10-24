"""Entitlements and product keys

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "entitlement",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "account_id",
            sa.BigInteger(),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tag", sa.String(), nullable=False),
        sa.Column("group_name", sa.String(), nullable=True),
        sa.Column("product_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column(
            "granted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("account_id", "tag"),
    )
    op.create_table(
        "product_key",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("product_key_code_key", "product_key", [sa.text("upper(code)")], unique=True)
    op.create_table(
        "product_key_grant",
        sa.Column(
            "key_id",
            sa.BigInteger(),
            sa.ForeignKey("product_key.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("tag", sa.String(), primary_key=True),
        sa.Column("group_name", sa.String(), nullable=True),
        sa.Column("product_id", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("product_key_grant")
    op.drop_table("product_key")
    op.drop_table("entitlement")
