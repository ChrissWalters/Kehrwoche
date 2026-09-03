"""In-app notifications, stored as i18n keys so everyone reads them in their language."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, ForeignKey, Integer, String
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Entity, UtcDateTime

if TYPE_CHECKING:
    from app.models.user import User


class NotificationType(StrEnum):
    """Known notification types; stored as text for the same reason as feed events."""

    CHORE_DUE = "chore_due"
    CHORE_ASSIGNED = "chore_assigned"
    CHORE_REMINDER = "chore_reminder"
    FEED_COMMENT = "feed_comment"
    FEED_LIKE = "feed_like"
    SETTLEMENT_DUE = "settlement_due"


class Notification(Entity):
    __tablename__ = "notifications"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    type: Mapped[str] = mapped_column(String(40), nullable=False)

    #: i18n keys plus their parameters — never a rendered text.
    title_key: Mapped[str] = mapped_column(String(80), nullable=False)
    body_key: Mapped[str | None] = mapped_column(String(80), default=None)
    params: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict, nullable=False
    )

    reference_type: Mapped[str | None] = mapped_column(String(40), default=None)
    reference_id: Mapped[int | None] = mapped_column(Integer, default=None)
    read_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None, index=True)

    user: Mapped[User] = relationship(back_populates="notifications")
