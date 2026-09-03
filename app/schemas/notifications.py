"""Request and response models of the in-app notifications."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class NotificationResponse(BaseModel):
    """A notification travels as keys plus parameters, never as finished text.

    Everybody reads it in their own language, and the language can change after the
    notification was written — so the text is rendered on the client, at display time.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    title_key: str
    body_key: str | None
    params: dict[str, Any]
    reference_type: str | None
    reference_id: int | None
    created_at: datetime
    read_at: datetime | None


class NotificationPageResponse(BaseModel):
    items: list[NotificationResponse]
    #: Pass back as `cursor` to fetch the next page; null means the end.
    next_cursor: int | None
    #: Badge counter, so the panel and the bell can never disagree.
    unread: int


class ReadAllResponse(BaseModel):
    """How many were still unread — the client shows it in the snackbar."""

    read: int
