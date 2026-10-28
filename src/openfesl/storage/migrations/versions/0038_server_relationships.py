"""Relationships of dedicated servers with accounts, such as their VIP lists

Revision ID: 0038
Revises: 0037
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("relationship", sa.Column("owner_server", sa.String(), nullable=True))
    op.add_column("relationship", sa.Column("member_server", sa.String(), nullable=True))
    op.alter_column("relationship", "owner_id", nullable=True)
    op.alter_column("relationship", "member_id", nullable=True)
    op.drop_constraint("relationship_game_tag_owner_id_member_id_key", "relationship")
    op.create_unique_constraint(
        "relationship_side_key",
        "relationship",
        ["game", "tag", "owner_id", "owner_server", "member_id", "member_server"],
        postgresql_nulls_not_distinct=True,
    )
    op.create_check_constraint(
        "relationship_one_owner", "relationship", "(owner_id IS NULL) <> (owner_server IS NULL)"
    )
    op.create_check_constraint(
        "relationship_one_member", "relationship", "(member_id IS NULL) <> (member_server IS NULL)"
    )


def downgrade() -> None:
    op.execute("DELETE FROM relationship WHERE owner_id IS NULL OR member_id IS NULL")
    op.drop_constraint("relationship_one_member", "relationship")
    op.drop_constraint("relationship_one_owner", "relationship")
    op.drop_constraint("relationship_side_key", "relationship")
    op.create_unique_constraint(
        "relationship_game_tag_owner_id_member_id_key",
        "relationship",
        ["game", "tag", "owner_id", "member_id"],
    )
    op.alter_column("relationship", "member_id", nullable=False)
    op.alter_column("relationship", "owner_id", nullable=False)
    op.drop_column("relationship", "member_server")
    op.drop_column("relationship", "owner_server")
