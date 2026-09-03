"""fair takeover and edited chores

Two columns, both added with a value for the rows that already exist: the switch is off,
so every household keeps behaving exactly as before, and existing feed entries get an
empty parameter set rather than NULL.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-11 09:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("households", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "takeover_keeps_turn",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    with op.batch_alter_table("feed_events", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("params", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )


def downgrade() -> None:
    with op.batch_alter_table("feed_events", schema=None) as batch_op:
        batch_op.drop_column("params")

    with op.batch_alter_table("households", schema=None) as batch_op:
        batch_op.drop_column("takeover_keeps_turn")
