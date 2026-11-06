"""Account states

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"


def upgrade() -> None:
    op.add_column(
        "account", sa.Column("state", sa.String(), server_default="active", nullable=False)
    )
    op.add_column("account", sa.Column("state_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "account", sa.Column("state_changed_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("account", "state_changed_at")
    op.drop_column("account", "state_until")
    op.drop_column("account", "state")
