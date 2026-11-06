"""Catalogues of games' items imported from players' copies, with their texts by language

Revision ID: 0026
Revises: 0025
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026"
down_revision: str | None = "0025"


def upgrade() -> None:
    op.create_table(
        "catalogue",
        sa.Column("partition", sa.String(), primary_key=True),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.Column("revision", sa.String(), nullable=False),
        sa.Column("icons", sa.Integer(), nullable=False),
        sa.Column(
            "imported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "catalogue_text",
        sa.Column(
            "partition",
            sa.String(),
            sa.ForeignKey("catalogue.partition", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("language", sa.String(), primary_key=True),
        sa.Column("texts", postgresql.JSONB(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("catalogue_text")
    op.drop_table("catalogue")
