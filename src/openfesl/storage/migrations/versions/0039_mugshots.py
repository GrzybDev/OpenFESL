"""The pictures that games render of their heroes, one for each hero

Revision ID: 0039
Revises: 0038
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"


def upgrade() -> None:
    op.create_table(
        "mugshot",
        sa.Column(
            "persona_id",
            sa.BigInteger(),
            sa.ForeignKey("persona.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("image", sa.LargeBinary(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("mugshot")
