"""username and optional email

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-05

The login name becomes a field of its own; the email address turns optional and private.
Existing accounts keep working: their previous login name is copied into ``username``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("username", sa.String(length=255), nullable=True))

    # Whatever people signed in with so far stays their login name.
    op.execute(sa.text("UPDATE users SET username = email"))

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("username", existing_type=sa.String(length=255), nullable=False)
        batch_op.alter_column("email", existing_type=sa.String(length=255), nullable=True)
        batch_op.create_index(batch_op.f("ix_users_username"), ["username"], unique=True)


def downgrade() -> None:
    # An account without an email address cannot exist in the old schema.
    op.execute(sa.text("UPDATE users SET email = username WHERE email IS NULL"))

    with op.batch_alter_table("users", schema=None) as batch_op:
        # drop_index is safe here: no foreign key points at users.username. Without this
        # line SQLite's batch mode would rebuild the table and then try to recreate the
        # index for a column that is no longer there.
        batch_op.drop_index(batch_op.f("ix_users_username"))
        batch_op.alter_column("email", existing_type=sa.String(length=255), nullable=False)
        batch_op.drop_column("username")
