"""The rewards that accounts claimed on the games' sites

Revision ID: 0035
Revises: 0034
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reward_claim",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("game", sa.String(), nullable=False),
        sa.Column(
            "account_id",
            sa.BigInteger(),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("faction", sa.String(), nullable=True),
        sa.Column(
            "persona_id",
            sa.BigInteger(),
            sa.ForeignKey("persona.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("outcome", JSONB(), nullable=False),
        sa.Column(
            "claimed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "game", "account_id", "key", "faction", postgresql_nulls_not_distinct=True
        ),
    )
    op.create_index("ix_reward_claim_account_id", "reward_claim", ["account_id"])
    op.create_index("ix_reward_claim_persona_id", "reward_claim", ["persona_id"])


def downgrade() -> None:
    op.drop_table("reward_claim")
