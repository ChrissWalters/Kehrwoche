"""FastAPI dependencies shared by all routers."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_db
from app.errors import AppError, ErrorCode
from app.models import User
from app.security import SESSION_COOKIE_NAME, RateLimiters
from app.services.auth import resolve_session

DbSession = Annotated[Session, Depends(get_db)]


def get_settings_from_app(request: Request) -> Settings:
    """The settings the app was created with — tests can swap them per app instance."""
    return request.app.state.settings


SettingsDep = Annotated[Settings, Depends(get_settings_from_app)]


def get_rate_limiters(request: Request) -> RateLimiters:
    return request.app.state.rate_limiters


RateLimitersDep = Annotated[RateLimiters, Depends(get_rate_limiters)]


def client_ip(request: Request) -> str:
    """The peer address.

    Forwarded headers are deliberately ignored: they are trivial to spoof, and behind a
    reverse proxy uvicorn already rewrites the peer when started with ``--proxy-headers``.
    """
    return request.client.host if request.client else "unknown"


def get_current_user(request: Request, db: DbSession, settings: SettingsDep) -> User:
    """The signed-in user, or 401 including removal of the stale session cookie."""
    return resolve_session(db, request.cookies.get(SESSION_COOKIE_NAME), settings)


#: Signed in, but possibly still forced to change the password. Only the account
#: endpoints (profile, password change, sessions, sign-out) accept this.
AuthenticatedUser = Annotated[User, Depends(get_current_user)]


def require_usable_account(user: AuthenticatedUser) -> User:
    """Block everything but the account endpoints until a forced password change is done."""
    if user.must_change_password:
        raise AppError(
            403,
            ErrorCode.PASSWORD_CHANGE_REQUIRED,
            "The password must be changed before this account can be used.",
        )
    return user


#: The dependency every business route uses.
CurrentUser = Annotated[User, Depends(require_usable_account)]


def require_member(user: CurrentUser) -> User:
    """Somebody who belongs to a household.

    Without one there is nothing to show — the client switches to the create-or-join
    view. A 404 keeps that indistinguishable from a household that no longer exists.
    """
    if user.household_id is None:
        raise AppError(
            404,
            ErrorCode.NOT_FOUND,
            "You are not part of a household.",
            message_key="error.household.none",
        )
    return user


MemberUser = Annotated[User, Depends(require_member)]


def require_admin(user: MemberUser) -> User:
    if not user.is_admin:
        raise AppError(
            403,
            ErrorCode.FORBIDDEN,
            "This action is reserved for admins.",
            message_key="error.admin_only",
        )
    return user


AdminUser = Annotated[User, Depends(require_admin)]
