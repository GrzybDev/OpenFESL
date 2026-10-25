"""E-mail verification of new accounts

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("account", sa.Column("verification_hash", sa.LargeBinary(), nullable=True))
    op.add_column(
        "account", sa.Column("verification_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_unique_constraint("account_verification_hash_key", "account", ["verification_hash"])


def downgrade() -> None:
    op.drop_constraint("account_verification_hash_key", "account")
    op.drop_column("account", "verification_expires_at")
    op.drop_column("account", "verification_hash")
