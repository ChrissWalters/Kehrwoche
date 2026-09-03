"""Request and response models of the household endpoints."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.models.household import HouseholdType
from app.models.user import UserRole

NAME_MAX_LENGTH = 120
#: ISO 4217, three letters. Purely a display format — nothing is ever converted.
CURRENCY_PATTERN = r"^[A-Za-z]{3}$"


class HouseholdCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    type: HouseholdType = HouseholdType.WG
    currency: str = Field(default="EUR", pattern=CURRENCY_PATTERN)


class HouseholdUpdateRequest(BaseModel):
    """Every field is optional; only what is sent gets changed."""

    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX_LENGTH)
    type: HouseholdType | None = None
    currency: str | None = Field(default=None, pattern=CURRENCY_PATTERN)
    takeover_keeps_turn: bool | None = None


class JoinRequest(BaseModel):
    join_code: str = Field(min_length=1, max_length=32)


class MemberRoleRequest(BaseModel):
    role: UserRole


class MemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    #: Shown next to the name so people with the same name stay distinguishable.
    username: str
    first_name: str
    last_name: str | None
    avatar_file: str | None
    role: UserRole
    points: int


class HouseholdResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: HouseholdType
    image_file: str | None
    currency: str
    #: When set, doing somebody else's turn does not hand the turn on to whoever did it.
    takeover_keeps_turn: bool
    #: Shared secret of the household — every member may pass it on.
    join_code: str
    members: list[MemberResponse]


class JoinCodeResponse(BaseModel):
    join_code: str


class HouseholdStateResponse(BaseModel):
    """Change markers per module — the client polls this, not the data itself."""

    #: Membership and household settings; moves when somebody joins, leaves or is changed.
    household: str
    chores: str
    shopping: str
    expenses: str
    feed: str
    #: Unread notifications of the requesting person; drives the badge on the bell.
    notifications: int
