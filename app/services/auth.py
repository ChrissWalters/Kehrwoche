"""Registration, sign-in and server-side sessions."""

from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.errors import AppError, ErrorCode
from app.models import Session as AuthSession
from app.models import User
from app.models.base import utcnow
from app.security import (
    generate_session_token,
    hash_password,
    hash_session_token,
    needs_rehash,
    session_cookie_removal_header,
    validate_password,
    verify_password,
)

#: Length limit of the stored ``user_agent`` (see ``app/models/session.py``).
USER_AGENT_MAX_LENGTH = 255

#: Deliberately loose: "something@something" without spaces. The address is optional and
#: nothing is sent to it, so the check only runs when the operator asks for it via
#: ``EMAIL_VALIDATION``.
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+$")


def normalise_username(username: str) -> str:
    """Trimmed and lower case, so "John" and "john" cannot become two accounts."""
    return username.strip().lower()


def normalise_email(email: str | None) -> str | None:
    if email is None:
        return None
    cleaned = email.strip().lower()
    return cleaned or None


def validate_email(email: str, settings: Settings) -> None:
    if settings.email_validation and not EMAIL_PATTERN.match(email):
        raise AppError(
            400,
            ErrorCode.VALIDATION_ERROR,
            "This is not a valid email address.",
            "email",
            message_key="error.email.invalid",
        )


def get_user_by_username(db: DbSession, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == normalise_username(username)))


def get_user_by_email(db: DbSession, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == normalise_email(email)))


def register_user(
    db: DbSession,
    settings: Settings,
    *,
    username: str,
    password: str,
    first_name: str,
    last_name: str | None = None,
    locale: str = "en",
    email: str | None = None,
) -> User:
    username = normalise_username(username)
    email = normalise_email(email)
    if not username:
        raise AppError(
            400,
            ErrorCode.VALIDATION_ERROR,
            "A login name is required.",
            "username",
            message_key="error.username.required",
        )
    if email is not None:
        validate_email(email, settings)
    validate_password(password)

    if get_user_by_username(db, username) is not None:
        raise AppError(409, ErrorCode.USERNAME_TAKEN, "This login name is taken.", "username")
    if email is not None and get_user_by_email(db, email) is not None:
        raise AppError(409, ErrorCode.EMAIL_TAKEN, "This email address is already in use.", "email")

    user = User(
        username=username,
        email=email,
        password_hash=hash_password(password),
        first_name=first_name,
        last_name=last_name,
        locale=locale,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate(db: DbSession, username: str, password: str) -> User:
    """Return the user for valid credentials, otherwise raise a 401.

    The same error is used for an unknown login name and a wrong password so that the
    response never reveals which accounts exist.
    """
    user = get_user_by_username(db, username)
    invalid = AppError(401, ErrorCode.INVALID_CREDENTIALS, "Login name or password is wrong.")
    if user is None or not verify_password(user.password_hash, password):
        raise invalid
    if not user.is_active:
        raise AppError(403, ErrorCode.ACCOUNT_INACTIVE, "This account is locked.")

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        db.commit()
    return user


def session_lifetime(settings: Settings) -> timedelta:
    return timedelta(days=settings.session_max_age_days)


def start_session(
    db: DbSession, user: User, settings: Settings, user_agent: str | None = None
) -> str:
    """Create a session row and return the token that goes into the cookie."""
    token = generate_session_token()
    now = utcnow()
    db.add(
        AuthSession(
            token_hash=hash_session_token(token),
            user_id=user.id,
            last_seen_at=now,
            expires_at=now + session_lifetime(settings),
            user_agent=user_agent[:USER_AGENT_MAX_LENGTH] if user_agent else None,
        )
    )
    db.commit()
    return token


def get_session(db: DbSession, token: str) -> AuthSession | None:
    return db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_session_token(token)))


def resolve_session(db: DbSession, token: str | None, settings: Settings) -> User:
    """Return the signed-in user, refreshing the inactivity window.

    Any failure raises a 401 that also tells the browser to drop the stale cookie.
    """
    expired = AppError(
        401,
        ErrorCode.NOT_AUTHENTICATED,
        "Not signed in.",
        headers=session_cookie_removal_header(settings),
    )
    if not token:
        raise expired

    session = get_session(db, token)
    now = utcnow()
    if session is None or session.expires_at <= now:
        if session is not None:
            db.delete(session)
            db.commit()
        raise expired

    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise expired

    # Sessions expire after inactivity, so every request pushes the window forward.
    session.last_seen_at = now
    session.expires_at = now + session_lifetime(settings)
    db.commit()
    return user


def list_sessions(db: DbSession, user: User) -> list[AuthSession]:
    """Active sessions of this account, most recently used first."""
    return list(
        db.scalars(
            select(AuthSession)
            .where(AuthSession.user_id == user.id, AuthSession.expires_at > utcnow())
            .order_by(AuthSession.last_seen_at.desc())
        )
    )


def revoke_session(db: DbSession, user: User, session_id: int) -> None:
    """Sign out one device. Sessions of other accounts are reported as not found."""
    session = db.get(AuthSession, session_id)
    if session is None or session.user_id != user.id:
        raise AppError(
            404,
            ErrorCode.NOT_FOUND,
            "Session not found.",
            message_key="error.session.not_found",
        )
    db.delete(session)
    db.commit()


def change_password(
    db: DbSession, user: User, current_password: str, new_password: str, keep_token: str | None
) -> None:
    """Set a new password and sign out every other device."""
    if not verify_password(user.password_hash, current_password):
        raise AppError(
            401,
            ErrorCode.INVALID_CREDENTIALS,
            "The current password is wrong.",
            "current_password",
            message_key="error.password.wrong",
        )
    validate_password(new_password, "new_password")

    user.password_hash = hash_password(new_password)
    user.must_change_password = False

    keep_hash = hash_session_token(keep_token) if keep_token else None
    for session in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id)):
        if session.token_hash != keep_hash:
            db.delete(session)
    db.commit()


def end_session(db: DbSession, token: str | None) -> None:
    """Sign out; unknown or missing tokens are silently accepted."""
    if not token:
        return
    session = get_session(db, token)
    if session is not None:
        db.delete(session)
        db.commit()
