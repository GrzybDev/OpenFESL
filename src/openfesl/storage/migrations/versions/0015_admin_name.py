"""The display names of the admins in the log of their actions

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"


def upgrade() -> None:
    op.add_column("admin_action", sa.Column("admin_name", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("admin_action", "admin_name")
