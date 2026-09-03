"""In-app notifications.

Stored as i18n keys plus parameters, never as finished text: everybody reads their
notifications in their own language, and that language may change after the notification
was written. Rendering happens on the client, at display time.

The interface is deliberately channel agnostic, so a future version can add web push as
a second delivery channel without touching a single caller.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session as DbSession

from app.errors import AppError, ErrorCode
from app.models import Notification, User
from app.models.base import utcnow

#: Notifications per page of the panel.
PAGE_SIZE = 20


def _keys(notification_type: str) -> tuple[str, str]:
    """Title and body key of a type. Stored with the row, so a later rename of the
    convention cannot silently change what old notifications say."""
    return f"notification.{notification_type}.title", f"notification.{notification_type}.body"


def notify(
    db: DbSession,
    user: User,
    notification_type: str,
    *,
    params: dict[str, Any] | None = None,
    reference_type: str | None = None,
    reference_id: int | None = None,
) -> Notification:
    """Announce something to one person. **Does not commit** — the caller owns the
    transaction, exactly like the feed events it usually accompanies."""
    title_key, body_key = _keys(str(notification_type))
    notification = Notification(
        user_id=user.id,
        type=str(notification_type),
        title_key=title_key,
        body_key=body_key,
        params=params or {},
        reference_type=reference_type,
        reference_id=reference_id,
    )
    db.add(notification)
    db.flush()
    return notification


def unread_count(db: DbSession, user: User) -> int:
    count = db.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == user.id, Notification.read_at.is_(None)
        )
    )
    return int(count or 0)


def list_notifications(
    db: DbSession, user: User, *, cursor: int | None = None, limit: int = PAGE_SIZE
) -> tuple[list[Notification], int | None]:
    """Own notifications, newest first. The cursor is the id of the last one delivered."""
    query = (
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.id.desc())
        .limit(limit + 1)
    )
    if cursor is not None:
        query = query.where(Notification.id < cursor)

    rows = list(db.scalars(query))
    next_cursor = rows[limit - 1].id if len(rows) > limit else None
    return rows[:limit], next_cursor


def get_notification(db: DbSession, user: User, notification_id: int) -> Notification:
    """Own notification; somebody else's does not exist for the caller."""
    notification = db.get(Notification, notification_id)
    if notification is None or notification.user_id != user.id:
        raise AppError(
            404,
            ErrorCode.NOT_FOUND,
            "Notification not found.",
            message_key="error.notification.not_found",
        )
    return notification


def mark_read(db: DbSession, notification: Notification) -> Notification:
    """Idempotent: reading twice keeps the moment it was first read."""
    if notification.read_at is None:
        notification.read_at = utcnow()
        db.commit()
        db.refresh(notification)
    return notification


def mark_all_read(db: DbSession, user: User) -> int:
    """Clear the badge in one go and report how many were still unread."""
    unread = unread_count(db, user)
    if unread:
        db.execute(
            update(Notification)
            .where(Notification.user_id == user.id, Notification.read_at.is_(None))
            .values(read_at=utcnow())
        )
        db.commit()
    return unread
