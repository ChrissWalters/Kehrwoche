"""Request models of the profile."""

from __future__ import annotations

from pydantic import BaseModel, Field

NAME_MAX_LENGTH = 80
EMAIL_MAX_LENGTH = 254
LOCALE_MAX_LENGTH = 8


class ProfileUpdateRequest(BaseModel):
    """Everything optional — the form sends only what changed.

    ``email`` is the one field that can be *cleared*: sending an empty string removes the
    address, which V1 only ever used as an optional contact anyway.
    """

    first_name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX_LENGTH)
    last_name: str | None = Field(default=None, max_length=NAME_MAX_LENGTH)
    locale: str | None = Field(default=None, max_length=LOCALE_MAX_LENGTH)
    email: str | None = Field(default=None, max_length=EMAIL_MAX_LENGTH)


class DeleteAccountRequest(BaseModel):
    """Deleting an account is irreversible, so it asks for the password."""

    password: str


class ImageResponse(BaseModel):
    """The stored file name — the client builds ``/media/<file>`` from it."""

    file: str
