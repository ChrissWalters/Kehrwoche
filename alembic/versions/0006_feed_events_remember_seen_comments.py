"""feed events remember seen comments

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-07 17:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("feed_events", schema=None) as batch_op:
        batch_op.add_column(sa.Column("comments_seen_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("feed_events", schema=None) as batch_op:
        batch_op.drop_column("comments_seen_id")
