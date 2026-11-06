"""Relationships between accounts, such as friends, one row for each side

Revision ID: 0037
Revises: 0036
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"


def upgrade() -> None:
    op.create_table(
        "relationship",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("game", sa.String(), nullable=False),
        sa.Column("tag", sa.String(), nullable=False),
        sa.Column(
            "owner_id",
            sa.BigInteger(),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "member_id",
            sa.BigInteger(),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_agrees", sa.Boolean(), nullable=False),
        sa.Column("member_agrees", sa.Boolean(), nullable=False),
        sa.Column("new", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "modified_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("game", "tag", "owner_id", "member_id"),
    )
    op.create_index("ix_relationship_member_id", "relationship", ["member_id"])


def downgrade() -> None:
    op.drop_table("relationship")
