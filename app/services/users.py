"""The profile: name, language, address, picture — and giving the account up."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.errors import AppError, ErrorCode
from app.images import delete_image, store_image
from app.models import Household, Session, User
from app.models.base import utcnow
from app.security import verify_password
from app.services.auth import get_user_by_email, normalise_email, validate_email
from app.services.expenses import balance_map, has_open_balance

#: Name of a deleted account: empty on purpose. Every view already falls back to its own
#: translated "former member" text, and a placeholder written here could only ever be in
#: one language.
DELETED_NAME = ""


def update_profile(
    db: DbSession,
    user: User,
    settings: Settings,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    locale: str | None = None,
    email: str | None = None,
) -> User:
    """Change what a person may change about themselves."""
    if first_name is not None:
        cleaned = first_name.strip()
        if not cleaned:
            raise AppError(
                400,
                ErrorCode.VALIDATION_ERROR,
                "The name cannot be empty.",
                "first_name",
                message_key="error.name.empty",
            )
        user.first_name = cleaned
    if last_name is not None:
        user.last_name = last_name.strip() or None
    if locale is not None:
        user.locale = locale.strip().lower()
    if email is not None:
        user.email = _resolved_email(db, user, settings, email)

    db.commit()
    db.refresh(user)
    return user


def _resolved_email(db: DbSession, user: User, settings: Settings, email: str) -> str | None:
    """An address that is free, or nothing at all when the field was cleared."""
    address = normalise_email(email)
    if address is None:
        return None
    validate_email(address, settings)
    existing = get_user_by_email(db, address)
    if existing is not None and existing.id != user.id:
        raise AppError(409, ErrorCode.EMAIL_TAKEN, "This email address is already in use.", "email")
    return address


def _image_still_in_use(db: DbSession, name: str, *, without_user: int | None = None) -> bool:
    """Pictures are stored by content, so two accounts can share one file."""
    users = select(User.id).where(User.avatar_file == name)
    if without_user is not None:
        users = users.where(User.id != without_user)
    if db.scalars(users).first() is not None:
        return True
    return db.scalars(select(Household.id).where(Household.image_file == name)).first() is not None


def set_avatar(db: DbSession, user: User, data: bytes, settings: Settings) -> User:
    previous = user.avatar_file
    user.avatar_file = store_image(data, settings.media_dir)
    db.commit()
    db.refresh(user)

    if previous and previous != user.avatar_file and not _image_still_in_use(db, previous):
        delete_image(previous, settings.media_dir)
    return user


def set_household_image(
    db: DbSession, household: Household, data: bytes, settings: Settings
) -> Household:
    previous = household.image_file
    household.image_file = store_image(data, settings.media_dir)
    db.commit()
    db.refresh(household)

    if previous and previous != household.image_file and not _image_still_in_use(db, previous):
        delete_image(previous, settings.media_dir)
    return household


def media_path(settings: Settings) -> Path:
    return settings.media_dir


def _erase_identity(db: DbSession, user: User) -> None:
    """Replace what identifies a person. Called at once, or once the books are settled."""
    user.first_name = DELETED_NAME
    user.last_name = None
    # The login name is personal too; it stays unique so the row keeps its shape.
    user.username = f"deleted-{user.id}"
    user.erasure_requested_at = None


def delete_account(db: DbSession, user: User, password: str, settings: Settings) -> None:
    """Give up the account: anonymise, do not remove.

    The specification is explicit about this — completions, expenses and feed entries of
    the household have to stay consistent, so the row survives as "former member" while
    everything personal about it goes away. Access ends immediately in every case:
    sessions, password, address and picture are gone before this returns.

    Name and login name are the one exception, and only while money is open: an unpaid
    share is a claim, and a claim has to stay attributable to somebody. That state is
    marked with ``erasure_requested_at`` and cleared by :func:`finish_pending_erasures`
    as soon as the period is settled.
    """
    if not verify_password(user.password_hash, password):
        raise AppError(
            403,
            ErrorCode.INVALID_CREDENTIALS,
            "The password is wrong.",
            "password",
            message_key="error.password.wrong",
        )

    # Leaving is part of deleting; it also keeps the rotations and the last-admin rule
    # intact instead of duplicating them here.
    if user.household_id is not None:
        from app.services.household import leave_household

        leave_household(db, user)

    avatar = user.avatar_file
    user.email = None
    user.avatar_file = None
    # Not a valid hash, so nothing can ever verify against it.
    user.password_hash = ""
    user.is_active = False
    user.must_change_password = False

    for session in db.scalars(select(Session).where(Session.user_id == user.id)):
        db.delete(session)

    if has_open_balance(db, user.id):
        user.erasure_requested_at = utcnow()
    else:
        _erase_identity(db, user)
    db.commit()

    if avatar and not _image_still_in_use(db, avatar):
        delete_image(avatar, settings.media_dir)


def erase_now(db: DbSession, user: User) -> None:
    """Finish a pending erasure regardless of the balance.

    The escape hatch for whoever runs the server: if a household never settles, the
    retention would otherwise last forever, and the person who asked to be deleted has
    nobody to turn to. Deliberately **not** reachable from the web interface — a
    household admin must not be able to erase a debtor's name.
    """
    _erase_identity(db, user)
    db.commit()


def pending_erasures(db: DbSession, *, household_id: int | None = None) -> list[User]:
    """Accounts whose erasure is waiting, optionally only those of one household.

    Scoped on purpose: an objection in one household is no reason to make people vanish
    from the books of another.
    """
    waiting = list(db.scalars(select(User).where(User.erasure_requested_at.is_not(None))))
    if household_id is None:
        return waiting

    household = db.get(Household, household_id)
    if household is None:
        return []
    involved = set(balance_map(db, household))
    return [user for user in waiting if user.id in involved]


def finish_pending_erasures(db: DbSession) -> int:
    """Complete the deletions that were waiting for a settlement. Returns how many.

    Runs after archiving and in the scheduler, so a balance that reaches zero any other
    way — a corrected expense, a deleted one — is picked up as well.
    """
    waiting = db.scalars(select(User).where(User.erasure_requested_at.is_not(None))).all()
    finished = 0
    for user in waiting:
        if has_open_balance(db, user.id):
            continue
        _erase_identity(db, user)
        finished += 1
    if finished:
        db.commit()
    return finished
