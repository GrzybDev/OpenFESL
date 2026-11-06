"""Persona states

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"


def upgrade() -> None:
    op.add_column(
        "persona", sa.Column("state", sa.String(), server_default="active", nullable=False)
    )
    op.add_column(
        "persona", sa.Column("state_changed_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("persona", "state_changed_at")
    op.drop_column("persona", "state")
