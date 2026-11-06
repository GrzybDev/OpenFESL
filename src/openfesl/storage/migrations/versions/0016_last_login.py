"""When each account last logged in

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"


def upgrade() -> None:
    op.add_column("account", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("account", "last_login_at")
