"""The pinboard: reading the feed, writing posts, likes and comments."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from app.deps import DbSession, MemberUser
from app.schemas.feed import (
    CommentRequest,
    CommentResponse,
    FeedEventResponse,
    FeedPageResponse,
    LikeResponse,
    PostRequest,
)
from app.services import feed as feed_service
from app.services import household as household_service

router = APIRouter(prefix="/feed", tags=["feed"])


@router.get("", response_model=FeedPageResponse, summary="The pinboard")
def read_feed(
    current_user: MemberUser,
    db: DbSession,
    cursor: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = feed_service.PAGE_SIZE,
) -> FeedPageResponse:
    household = household_service.get_household(db, current_user)
    items, next_cursor = feed_service.list_feed(
        db, household, current_user, cursor=cursor, limit=limit
    )
    return FeedPageResponse(
        items=[FeedEventResponse.model_validate(item) for item in items],
        next_cursor=next_cursor,
    )


@router.post(
    "",
    response_model=FeedEventResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Write a post",
)
def create_post(payload: PostRequest, current_user: MemberUser, db: DbSession) -> FeedEventResponse:
    household = household_service.get_household(db, current_user)
    feed_service.create_post(db, household, current_user, payload.body)
    # Straight back through the reading path, so a fresh post carries the same counters
    # as every other card.
    items, _ = feed_service.list_feed(db, household, current_user, limit=1)
    return FeedEventResponse.model_validate(items[0])


@router.delete(
    "/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete your own post",
)
def delete_post(event_id: int, current_user: MemberUser, db: DbSession) -> None:
    household = household_service.get_household(db, current_user)
    event = feed_service.get_event(db, household, event_id)
    feed_service.delete_post(db, event, current_user)


@router.post("/{event_id}/like", response_model=LikeResponse, summary="Like or un-like")
def toggle_like(event_id: int, current_user: MemberUser, db: DbSession) -> LikeResponse:
    household = household_service.get_household(db, current_user)
    event = feed_service.get_event(db, household, event_id)
    liked, count = feed_service.toggle_like(db, event, current_user)
    return LikeResponse(liked=liked, like_count=count)


@router.get(
    "/{event_id}/comments",
    response_model=list[CommentResponse],
    summary="Comments of an entry",
)
def read_comments(event_id: int, current_user: MemberUser, db: DbSession) -> list[CommentResponse]:
    """Reading them clears the unread marker — for the author of the entry."""
    household = household_service.get_household(db, current_user)
    event = feed_service.get_event(db, household, event_id)
    return [
        CommentResponse.model_validate(comment)
        for comment in feed_service.list_comments(db, event, current_user)
    ]


@router.post(
    "/{event_id}/comments",
    response_model=CommentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Comment on an entry",
)
def add_comment(
    event_id: int, payload: CommentRequest, current_user: MemberUser, db: DbSession
) -> CommentResponse:
    household = household_service.get_household(db, current_user)
    event = feed_service.get_event(db, household, event_id)
    return CommentResponse.model_validate(
        feed_service.add_comment(db, event, current_user, payload.body)
    )
