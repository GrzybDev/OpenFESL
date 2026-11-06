"""Association lists

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"


def upgrade() -> None:
    op.create_table(
        "association",
        sa.Column(
            "owner_id",
            sa.BigInteger(),
            sa.ForeignKey("persona.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("type", sa.String(), primary_key=True),
        sa.Column("partition", sa.String(), primary_key=True),
        sa.Column(
            "member_id",
            sa.BigInteger(),
            sa.ForeignKey("persona.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "modified_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_association_member_id", "association", ["member_id"])


def downgrade() -> None:
    op.drop_table("association")
