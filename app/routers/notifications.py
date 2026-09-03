"""In-app notifications: the bell and its panel."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.deps import CurrentUser, DbSession
from app.schemas.notifications import (
    NotificationPageResponse,
    NotificationResponse,
    ReadAllResponse,
)
from app.services import notifications as notification_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationPageResponse, summary="Own notifications")
def read_notifications(
    current_user: CurrentUser,
    db: DbSession,
    cursor: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = notification_service.PAGE_SIZE,
) -> NotificationPageResponse:
    """Notifications belong to a person, not to a household — no membership needed."""
    rows, next_cursor = notification_service.list_notifications(
        db, current_user, cursor=cursor, limit=limit
    )
    return NotificationPageResponse(
        items=[NotificationResponse.model_validate(row) for row in rows],
        next_cursor=next_cursor,
        unread=notification_service.unread_count(db, current_user),
    )


@router.post("/read-all", response_model=ReadAllResponse, summary="Clear the badge")
def read_all(current_user: CurrentUser, db: DbSession) -> ReadAllResponse:
    return ReadAllResponse(read=notification_service.mark_all_read(db, current_user))


@router.post(
    "/{notification_id}/read",
    response_model=NotificationResponse,
    summary="Mark one as read",
)
def read_one(
    notification_id: int, current_user: CurrentUser, db: DbSession
) -> NotificationResponse:
    notification = notification_service.get_notification(db, current_user, notification_id)
    return NotificationResponse.model_validate(notification_service.mark_read(db, notification))
