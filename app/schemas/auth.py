"""Request and response models of the authentication endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.user import UserRole

USERNAME_MAX_LENGTH = 255
EMAIL_MAX_LENGTH = 255
NAME_MAX_LENGTH = 80
LOCALE_MAX_LENGTH = 8
#: Argon2 handles long inputs fine; the cap only keeps absurd payloads out.
PASSWORD_MAX_LENGTH = 256


class RegisterRequest(BaseModel):
    #: Freely chosen login name; it is visible to the household.
    username: str = Field(min_length=1, max_length=USERNAME_MAX_LENGTH)
    #: Optional and private. Nothing is sent in V1 — it prepares the mail reset of V2.
    email: str | None = Field(default=None, max_length=EMAIL_MAX_LENGTH)
    #: Policy checks (length, common passwords) happen in ``app.security``.
    password: str = Field(max_length=PASSWORD_MAX_LENGTH)
    first_name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    last_name: str | None = Field(default=None, max_length=NAME_MAX_LENGTH)
    locale: str = Field(default="en", max_length=LOCALE_MAX_LENGTH)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=USERNAME_MAX_LENGTH)
    password: str = Field(max_length=PASSWORD_MAX_LENGTH)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(max_length=PASSWORD_MAX_LENGTH)
    new_password: str = Field(max_length=PASSWORD_MAX_LENGTH)


class SessionResponse(BaseModel):
    """One signed-in device, as shown in the settings."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    user_agent: str | None
    #: True for the session making this very request.
    current: bool = False


class UserResponse(BaseModel):
    """The signed-in person as returned by ``/auth/login`` and ``/me``."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    #: Only ever returned for the signed-in person themselves.
    email: str | None
    first_name: str
    last_name: str | None
    avatar_file: str | None
    locale: str
    household_id: int | None
    role: UserRole
    points: int
    must_change_password: bool
