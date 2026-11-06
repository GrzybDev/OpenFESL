"""The mark of the dedicated servers that the service's administrators run

Revision ID: 0040
Revises: 0039
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"


def upgrade() -> None:
    op.add_column(
        "game_server",
        sa.Column("official", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("game_server", "official")
