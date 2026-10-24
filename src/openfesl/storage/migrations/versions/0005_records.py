"""Records

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "record_entry",
        sa.Column(
            "persona_id",
            sa.BigInteger(),
            sa.ForeignKey("persona.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("partition", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), primary_key=True),
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", sa.String(), nullable=False),
        sa.Column(
            "modified_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("record_entry")
