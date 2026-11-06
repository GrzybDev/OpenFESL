"""Accounts' web sessions, public display names and the game each was registered in

Revision ID: 0022
Revises: 0021
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"


def upgrade() -> None:
    op.add_column(
        "account",
        sa.Column("session_epoch", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("account", sa.Column("display_name", sa.String(), nullable=True))
    op.add_column("account", sa.Column("made_in", sa.String(), nullable=True))
    # Until now only Bad Company 2's client registered players: Battlefield Heroes registers none
    # in the game, and the site registered no accounts yet.
    op.execute("UPDATE account SET made_in = 'bfbc2' WHERE NOT server")
    op.create_index(
        "account_display_name_key",
        "account",
        [sa.text("lower(display_name)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("account_display_name_key", "account")
    op.drop_column("account", "made_in")
    op.drop_column("account", "display_name")
    op.drop_column("account", "session_epoch")
