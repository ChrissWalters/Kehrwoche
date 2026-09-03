"""keep only real addresses in email

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-06

Migration 0004 copied the previous login name into both ``username`` and ``email`` so
that nothing was lost. Login names are rarely addresses though ("jayw", "alex@wg"), and
a field called ``email`` must not hold anything else — V2 will send mail to it. Whatever
does not look like an address is therefore cleared; the value survives as the username.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Same loose rule the application uses: something before and after an "@".
LOOKS_LIKE_AN_ADDRESS = "%_@_%"


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE users SET email = NULL WHERE email IS NOT NULL AND email NOT LIKE :pattern"
        ).bindparams(pattern=LOOKS_LIKE_AN_ADDRESS)
    )


def downgrade() -> None:
    """Nothing to restore: the cleared values live on as the login name."""
