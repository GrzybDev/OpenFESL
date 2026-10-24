"""Personas

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "persona",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "account_id",
            sa.BigInteger(),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_persona_account_id", "persona", ["account_id"])
    op.create_index(
        "persona_name_key", "persona", ["namespace", sa.text("lower(name)")], unique=True
    )


def downgrade() -> None:
    op.drop_table("persona")
