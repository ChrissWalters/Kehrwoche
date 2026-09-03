"""The pinboard — and the place every system event of the household is written.

The feed is not decoration: it is the audit log of the household. Events are therefore
created **synchronously in the transaction of the change they describe** (see
:func:`emit_event`), never afterwards and never in the background. A change that is
rolled back leaves no entry, and an entry never exists without its change.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.errors import AppError, ErrorCode
from app.models import (
    Comment,
    FeedEvent,
    FeedEventType,
    Household,
    Like,
    NotificationType,
    User,
)
from app.services.notifications import notify

#: Events per page of the feed.
PAGE_SIZE = 20


def emit_event(
    db: DbSession,
    household: Household,
    event_type: FeedEventType,
    *,
    actor: User | None = None,
    reference_type: str | None = None,
    reference_id: int | None = None,
    body: str | None = None,
    params: dict[str, Any] | None = None,
) -> FeedEvent:
    """Record what just happened. **Does not commit** — the caller owns the transaction.

    Every module writes its events through here, so the shape of an entry is decided in
    one place instead of in eight.
    """
    event = FeedEvent(
        household_id=household.id,
        type=event_type,
        actor_id=actor.id if actor is not None else None,
        reference_type=reference_type,
        reference_id=reference_id,
        body=body,
        params=params or {},
    )
    db.add(event)
    db.flush()
    return event


def get_event(db: DbSession, household: Household, event_id: int) -> FeedEvent:
    """An entry of this household; anything else does not exist for the caller."""
    event = db.get(FeedEvent, event_id)
    if event is None or event.household_id != household.id:
        raise AppError(
            404,
            ErrorCode.NOT_FOUND,
            "Entry not found.",
            message_key="error.post.not_found",
        )
    return event


def _counts(
    db: DbSession, model: type[Comment] | type[Like], event_ids: list[int]
) -> dict[int, int]:
    if not event_ids:
        return {}
    rows = db.execute(
        select(model.event_id, func.count(model.id))
        .where(model.event_id.in_(event_ids))
        .group_by(model.event_id)
    ).all()
    return dict(rows)  # type: ignore[arg-type]


def _unread_comments(db: DbSession, user: User, events: list[FeedEvent]) -> dict[int, int]:
    """Comments by other people that the author of the entry has not seen yet.

    Only entries of the requesting person are considered — the unread marker belongs to
    whoever wrote the post, nobody else.
    """
    own = {event.id: event.comments_seen_id or 0 for event in events if event.actor_id == user.id}
    if not own:
        return {}

    rows = db.execute(
        select(Comment.event_id, Comment.id).where(
            Comment.event_id.in_(list(own)), Comment.author_id != user.id
        )
    ).all()

    unread: dict[int, int] = {}
    for event_id, comment_id in rows:
        if comment_id > own[event_id]:
            unread[event_id] = unread.get(event_id, 0) + 1
    return unread


def list_feed(
    db: DbSession,
    household: Household,
    user: User,
    *,
    cursor: int | None = None,
    limit: int = PAGE_SIZE,
) -> tuple[list[dict[str, object]], int | None]:
    """One page of the feed, newest first, enriched with everything a card shows.

    The cursor is the id of the last entry already delivered — ids grow with every
    event, so scrolling can neither skip nor repeat one. Likes and comments are counted
    in one query each instead of per entry.
    """
    query = (
        select(FeedEvent)
        .where(FeedEvent.household_id == household.id)
        .order_by(FeedEvent.id.desc())
        .limit(limit + 1)
    )
    if cursor is not None:
        query = query.where(FeedEvent.id < cursor)

    events = list(db.scalars(query))
    next_cursor = events[limit - 1].id if len(events) > limit else None
    events = events[:limit]

    event_ids = [event.id for event in events]
    like_counts = _counts(db, Like, event_ids)
    comment_counts = _counts(db, Comment, event_ids)
    liked_by_me = set(
        db.scalars(
            select(Like.event_id).where(Like.event_id.in_(event_ids), Like.user_id == user.id)
        )
    )
    unread = _unread_comments(db, user, events)

    items = [
        {
            "id": event.id,
            "type": event.type,
            "actor_id": event.actor_id,
            "reference_type": event.reference_type,
            "reference_id": event.reference_id,
            "body": event.body,
            "params": dict(event.params),
            "created_at": event.created_at,
            "like_count": like_counts.get(event.id, 0),
            "liked_by_me": event.id in liked_by_me,
            "comment_count": comment_counts.get(event.id, 0),
            "comments_unread": unread.get(event.id, 0),
        }
        for event in events
    ]
    return items, next_cursor


def clean_body(body: str) -> str:
    """Text has to survive trimming — a post of spaces is not a post."""
    cleaned = body.strip()
    if not cleaned:
        raise AppError(
            400,
            ErrorCode.VALIDATION_ERROR,
            "The entry needs some text.",
            "body",
            message_key="error.post.body_required",
        )
    return cleaned


def create_post(db: DbSession, household: Household, actor: User, body: str) -> FeedEvent:
    event = emit_event(db, household, FeedEventType.USER_POST, actor=actor, body=clean_body(body))
    db.commit()
    db.refresh(event)
    return event


def delete_post(db: DbSession, event: FeedEvent, user: User) -> None:
    """Only your own post, and only a post.

    System events are the audit log of the household — nobody deletes those, not even
    admins, because a log that can be cleaned up proves nothing.
    """
    if event.type != FeedEventType.USER_POST:
        raise AppError(
            403,
            ErrorCode.FORBIDDEN,
            "System entries cannot be deleted.",
            message_key="error.post.system_entry",
        )
    if event.actor_id != user.id:
        raise AppError(
            403,
            ErrorCode.FORBIDDEN,
            "Only your own posts can be deleted.",
            message_key="error.post.not_yours",
        )
    db.delete(event)
    db.commit()


def toggle_like(db: DbSession, event: FeedEvent, user: User) -> tuple[bool, int]:
    """The same tap likes and un-likes, so a mistap costs nothing."""
    existing = db.scalar(select(Like).where(Like.event_id == event.id, Like.user_id == user.id))
    if existing is not None:
        db.delete(existing)
        liked = False
    else:
        db.add(Like(event_id=event.id, user_id=user.id))
        liked = True
        # Nobody needs a notification about their own tap.
        if event.actor_id is not None and event.actor_id != user.id:
            author = db.get(User, event.actor_id)
            if author is not None:
                notify(
                    db,
                    author,
                    NotificationType.FEED_LIKE,
                    params={"actor": user.first_name},
                    reference_type="feed_event",
                    reference_id=event.id,
                )
    db.commit()

    count = db.scalar(select(func.count(Like.id)).where(Like.event_id == event.id)) or 0
    return liked, int(count)


def list_comments(db: DbSession, event: FeedEvent, user: User) -> list[Comment]:
    """The comments of an entry, oldest first — and reading them clears the marker."""
    comments = list(
        db.scalars(select(Comment).where(Comment.event_id == event.id).order_by(Comment.id))
    )
    if event.actor_id == user.id and comments:
        newest = comments[-1].id
        if (event.comments_seen_id or 0) < newest:
            event.comments_seen_id = newest
            db.commit()
    return comments


def add_comment(db: DbSession, event: FeedEvent, author: User, body: str) -> Comment:
    comment = Comment(event_id=event.id, author_id=author.id, body=clean_body(body))
    db.add(comment)
    db.flush()

    if event.actor_id is not None and event.actor_id != author.id:
        recipient = db.get(User, event.actor_id)
        if recipient is not None:
            notify(
                db,
                recipient,
                NotificationType.FEED_COMMENT,
                params={"actor": author.first_name},
                reference_type="feed_event",
                reference_id=event.id,
            )
    db.commit()
    db.refresh(comment)
    return comment
