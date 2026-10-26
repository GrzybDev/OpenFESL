"""The roles of the admins in the log of their actions

Revision ID: 0018
Revises: 0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("admin_action", sa.Column("admin_role", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("admin_action", "admin_role")
