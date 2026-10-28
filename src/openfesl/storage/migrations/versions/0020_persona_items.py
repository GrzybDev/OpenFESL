"""Items that personas own each, with their uses and end

Revision ID: 0020
Revises: 0019
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "entitlement",
        sa.Column(
            "persona_id",
            sa.BigInteger(),
            sa.ForeignKey("persona.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column("entitlement", sa.Column("use_count", sa.Integer(), nullable=True))
    op.add_column("entitlement", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint("entitlement_account_id_tag_key", "entitlement", type_="unique")
    op.create_index(
        "entitlement_tag_key",
        "entitlement",
        ["account_id", "tag"],
        unique=True,
        postgresql_where=sa.text("persona_id IS NULL"),
    )
    op.create_index(
        "entitlement_item_key",
        "entitlement",
        ["persona_id", sa.text("split_part(tag, '-', 1)")],
        unique=True,
        postgresql_where=sa.text("persona_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("entitlement_item_key", "entitlement")
    op.drop_index("entitlement_tag_key", "entitlement")
    # Personas' items would break the account's one row per tag.
    op.execute("DELETE FROM entitlement WHERE persona_id IS NOT NULL")
    op.create_unique_constraint(
        "entitlement_account_id_tag_key", "entitlement", ["account_id", "tag"]
    )
    op.drop_column("entitlement", "expires_at")
    op.drop_column("entitlement", "use_count")
    op.drop_column("entitlement", "persona_id")
