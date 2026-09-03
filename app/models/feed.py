"""The pinboard: system events (the household's audit log) plus user posts."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Entity

if TYPE_CHECKING:
    from app.models.household import Household
    from app.models.user import User


class FeedEventType(StrEnum):
    """Known event types.

    Stored as plain text rather than a database enum: modules keep adding types, and a
    text column spares every dialect an enum migration for each new one.
    """

    CHORE_CREATED = "chore_created"
    CHORE_UPDATED = "chore_updated"
    CHORE_DELETED = "chore_deleted"
    CHORE_DONE = "chore_done"
    CHORE_STATISTICS_RESET = "chore_statistics_reset"
    SHOPPING_ADDED = "shopping_added"
    SHOPPING_BOUGHT_BULK = "shopping_bought_bulk"
    EXPENSE_ADDED = "expense_added"
    SETTLEMENT_ARCHIVED = "settlement_archived"
    MEMBER_JOINED = "member_joined"
    MEMBER_LEFT = "member_left"
    USER_POST = "user_post"


class FeedEvent(Entity):
    __tablename__ = "feed_events"

    household_id: Mapped[int] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), index=True, nullable=False
    )
    type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    #: ``NULL`` for events without an actor (for example scheduled ones).
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None, index=True
    )

    #: Polymorphic pointer to the business object the event is about.
    reference_type: Mapped[str | None] = mapped_column(String(40), default=None)
    reference_id: Mapped[int | None] = mapped_column(Integer, default=None)
    #: Free text of a user post, or pre-formatted extra information.
    body: Mapped[str | None] = mapped_column(Text, default=None)
    #: Placeholder values for the sentence of a system event — the names of the fields
    #: an edit touched, for instance. Keys rather than finished text, for the same reason
    #: notifications store keys: everybody reads the entry in their own language.
    params: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict, nullable=False
    )

    #: Newest comment the author of this event has already seen; everything with a
    #: higher id is unread for them. Deliberately an id and not a timestamp: ids grow
    #: strictly, while two comments written in the same instant can carry the same
    #: timestamp. Plain integer, no foreign key — a deleted comment must not reset
    #: the mark.
    comments_seen_id: Mapped[int | None] = mapped_column(Integer, default=None)

    household: Mapped[Household] = relationship(back_populates="feed_events")
    actor: Mapped[User | None] = relationship()
    comments: Mapped[list[Comment]] = relationship(
        back_populates="event", cascade="all, delete-orphan", order_by="Comment.created_at"
    )
    likes: Mapped[list[Like]] = relationship(back_populates="event", cascade="all, delete-orphan")


class Comment(Entity):
    __tablename__ = "comments"

    event_id: Mapped[int] = mapped_column(
        ForeignKey("feed_events.id", ondelete="CASCADE"), index=True, nullable=False
    )
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    event: Mapped[FeedEvent] = relationship(back_populates="comments")
    author: Mapped[User] = relationship()


class Like(Entity):
    __tablename__ = "likes"
    __table_args__ = (UniqueConstraint("event_id", "user_id"),)

    event_id: Mapped[int] = mapped_column(
        ForeignKey("feed_events.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)

    event: Mapped[FeedEvent] = relationship(back_populates="likes")
    user: Mapped[User] = relationship()
