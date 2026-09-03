"""Instance administration — the jobs that have no place in the web interface.

Locking an account, resetting a password, deleting a whole household: none of this
belongs to a household admin, it belongs to whoever runs the server. The logic lives
here, `app/cli.py` is only the thin layer that prints it.
"""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.db import is_sqlite, sqlite_file
from app.models import Household, Session, User
from app.security import hash_password

#: Characters that survive being read out over the phone: no 0/O, no 1/I/l.
ONE_TIME_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"
#: Four groups of four — long enough to be safe, short enough to dictate.
ONE_TIME_GROUPS = 4
ONE_TIME_GROUP_SIZE = 4


class AdminError(Exception):
    """Something the operator has to fix — printed, never a stack trace."""


@dataclass(frozen=True)
class UserRow:
    """One line of ``user list``."""

    id: int
    username: str
    name: str
    household: str
    role: str
    active: bool
    must_change_password: bool


@dataclass(frozen=True)
class HouseholdRow:
    id: int
    name: str
    type: str
    members: int


def generate_one_time_password() -> str:
    """A password to hand over once — grouped, unambiguous, and long enough."""
    groups = [
        "".join(secrets.choice(ONE_TIME_ALPHABET) for _ in range(ONE_TIME_GROUP_SIZE))
        for _ in range(ONE_TIME_GROUPS)
    ]
    return "-".join(groups)


def list_users(db: DbSession) -> list[UserRow]:
    rows = []
    for user in db.scalars(select(User).order_by(User.id)):
        household = db.get(Household, user.household_id) if user.household_id else None
        rows.append(
            UserRow(
                id=user.id,
                username=user.username,
                name=" ".join(part for part in (user.first_name, user.last_name) if part),
                household=household.name if household else "-",
                role=str(user.role),
                active=user.is_active,
                must_change_password=user.must_change_password,
            )
        )
    return rows


def find_user(db: DbSession, username: str) -> User:
    user = db.scalar(select(User).where(User.username == username.strip().lower()))
    if user is None:
        raise AdminError(f"No account with the login name {username!r}.")
    return user


def revoke_sessions(db: DbSession, user: User) -> int:
    """Sign the account out everywhere. Returns how many devices were affected."""
    sessions = list(db.scalars(select(Session).where(Session.user_id == user.id)))
    for session in sessions:
        db.delete(session)
    return len(sessions)


def reset_password(db: DbSession, username: str) -> str:
    """Hand out a one-time password and force a change at the next sign-in.

    Every device is signed out at the same time: whoever asked for a reset has lost
    control of the account, so the old sessions have to go with the old password.
    """
    user = find_user(db, username)
    password = generate_one_time_password()
    user.password_hash = hash_password(password)
    user.must_change_password = True
    revoke_sessions(db, user)
    db.commit()
    return password


def set_active(db: DbSession, username: str, active: bool) -> User:
    """Lock or unlock an account. Locking also ends every running session."""
    user = find_user(db, username)
    user.is_active = active
    if not active:
        revoke_sessions(db, user)
    db.commit()
    db.refresh(user)
    return user


def list_households(db: DbSession) -> list[HouseholdRow]:
    rows = []
    for household in db.scalars(select(Household).order_by(Household.id)):
        members = db.scalars(select(User.id).where(User.household_id == household.id)).all()
        rows.append(
            HouseholdRow(
                id=household.id,
                name=household.name,
                type=str(household.type),
                members=len(members),
            )
        )
    return rows


def delete_household(db: DbSession, household_id: int) -> str:
    """Remove a household with everything in it. The accounts themselves survive."""
    household = db.get(Household, household_id)
    if household is None:
        raise AdminError(f"No household with the id {household_id}.")
    name = household.name
    db.delete(household)
    db.commit()
    return name


def backup_database(settings: Settings, target: Path) -> Path:
    """Copy a running SQLite database consistently, without stopping the server.

    Uses SQLite's own backup interface, which takes care of pages still sitting in the
    write-ahead log — a plain file copy can miss exactly those. For PostgreSQL there is
    nothing sensible to do from here, so it says which tool to use instead of writing a
    file that only looks like a backup.
    """
    if not is_sqlite(settings.database_url):
        raise AdminError(
            "This instance does not use SQLite. Please back the database up with the "
            "tool of your database — pg_dump for PostgreSQL."
        )

    source = sqlite_file(settings.database_url)
    if source is None or not source.exists():
        raise AdminError("The database file does not exist (yet).")

    target = target.expanduser()
    if target.is_dir():
        target = target / f"{source.stem}-backup.db"
    target.parent.mkdir(parents=True, exist_ok=True)

    with (
        sqlite3.connect(f"file:{source}?mode=ro", uri=True) as origin,
        sqlite3.connect(target) as copy,
    ):
        origin.backup(copy)
    return target
