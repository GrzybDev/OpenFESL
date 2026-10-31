"""The Daily Claw: each grab of an account's hero, and the rare tiers of a box's drops

Revision ID: 0050
Revises: 0049
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050"
down_revision: str | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "store_drop", sa.Column("rare", sa.Boolean(), server_default="false", nullable=False)
    )
    op.create_table(
        "claw_grab",
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
            nullable=False,
        ),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("free", sa.Integer(), nullable=True),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("ownership", sa.String(), nullable=False),
        sa.Column("rare", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("game", "account_id", "day", "free"),
    )
    op.create_index("ix_claw_grab_persona_id", "claw_grab", ["persona_id"])
    op.create_index("ix_claw_grab_created_at", "claw_grab", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_claw_grab_created_at", table_name="claw_grab")
    op.drop_index("ix_claw_grab_persona_id", table_name="claw_grab")
    op.drop_table("claw_grab")
    op.drop_column("store_drop", "rare")
