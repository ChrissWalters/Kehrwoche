"""Request and response models of the pinboard."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

#: Long enough for a note to the household, short enough to stay readable on a phone.
BODY_MAX_LENGTH = 2000


class PostRequest(BaseModel):
    """A post is plain text — no uploads."""

    body: str = Field(min_length=1, max_length=BODY_MAX_LENGTH)


class CommentRequest(BaseModel):
    body: str = Field(min_length=1, max_length=BODY_MAX_LENGTH)


class CommentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    author_id: int
    body: str
    created_at: datetime


class FeedEventResponse(BaseModel):
    id: int
    type: str
    #: Null once the account behind an event has been deleted.
    actor_id: int | None
    #: Where the event points to, so the client can link into the module.
    reference_type: str | None
    reference_id: int | None
    #: Text of a post, or a pre-formatted detail of a system event.
    body: str | None
    #: Placeholder values for the sentence — for an edit, the fields it touched.
    params: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    like_count: int
    liked_by_me: bool
    comment_count: int
    #: Comments the requesting person has not seen yet — always 0 unless the event is
    #: theirs, because only the author gets the unread marker.
    comments_unread: int


class FeedPageResponse(BaseModel):
    items: list[FeedEventResponse]
    #: Pass back as `cursor` to fetch the next page; null means the end.
    next_cursor: int | None


class LikeResponse(BaseModel):
    """State after the toggle — the client does not have to guess."""

    liked: bool
    like_count: int
