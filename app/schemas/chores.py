"""Request and response models of the chore endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.chore import ON_DEMAND

TITLE_MAX_LENGTH = 120
#: A week has 604800 seconds; a year is plenty of head room for a rhythm.
MAX_ROTATION_SECONDS = 366 * 24 * 3600
MAX_POINTS = 1000


class ChoreCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX_LENGTH)
    description: str | None = None
    points: int = Field(default=0, ge=0, le=MAX_POINTS)
    #: ``-1`` means "when needed" — no due date, no clock.
    rotation_seconds: int = Field(default=ON_DEMAND, ge=ON_DEMAND, le=MAX_ROTATION_SECONDS)
    fixed: bool = False
    #: Order of the rotation; empty means every member, ordered by joining.
    member_order: list[int] | None = None
    due_at: datetime | None = None


class ChoreUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX_LENGTH)
    description: str | None = None
    points: int | None = Field(default=None, ge=0, le=MAX_POINTS)
    rotation_seconds: int | None = Field(default=None, ge=ON_DEMAND, le=MAX_ROTATION_SECONDS)
    fixed: bool | None = None
    member_order: list[int] | None = None
    current_user_id: int | None = None
    due_at: datetime | None = None


class ChoreResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str | None
    points: int
    rotation_seconds: int
    fixed: bool
    member_order: list[int]
    current_user_id: int | None
    due_at: datetime | None
    last_done_at: datetime | None


class ChoreCompleteRequest(BaseModel):
    """Optional body of ``complete``.

    ``for_user_id`` books on behalf of somebody else — for the case where the person on
    duty did the work but has no device at hand.
    """

    for_user_id: int | None = None


class ChoreCompletionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    chore_id: int
    user_id: int
    booked_by_id: int | None
    done_at: datetime
    points_awarded: int


class RemindResponse(BaseModel):
    """Who was nudged — the client shows a confirmation with their name."""

    user_id: int
    first_name: str


class ChoreTemplateResponse(BaseModel):
    """A suggestion from the language file, ready to be turned into a chore."""

    title: str
    rotation_seconds: int
    points: int
    fixed: bool


class HistoryEntryResponse(BaseModel):
    id: int
    chore_id: int
    chore_title: str
    user_id: int
    #: Set when somebody else booked this completion on behalf of ``user_id``.
    booked_by_id: int | None
    done_at: datetime
    points_awarded: int


class HistoryPageResponse(BaseModel):
    items: list[HistoryEntryResponse]
    #: Pass back as `cursor` to fetch the next page; null means the end.
    next_cursor: int | None


class MemberStatisticsResponse(BaseModel):
    user_id: int
    first_name: str
    points: int
    completions: int
    #: Completions within the last 30 days.
    completions_recent: int
