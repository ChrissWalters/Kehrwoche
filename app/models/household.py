"""The household — the tenant every other entity is bound to."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Entity, string_enum

if TYPE_CHECKING:
    from app.models.chore import Chore
    from app.models.expense import Expense, SettlementPeriod
    from app.models.feed import FeedEvent
    from app.models.shopping import ShoppingItem
    from app.models.user import User

#: Length of a join code (see ``app/services/household.py`` from AP09).
JOIN_CODE_LENGTH = 12


class HouseholdType(StrEnum):
    """Presentation only — no functional difference between the types."""

    WG = "wg"
    COUPLE = "couple"
    FAMILY = "family"


class Household(Entity):
    __tablename__ = "households"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    type: Mapped[HouseholdType] = mapped_column(
        string_enum(HouseholdType, "household_type"),
        default=HouseholdType.WG,
        nullable=False,
    )
    image_file: Mapped[str | None] = mapped_column(String(128), default=None)
    join_code: Mapped[str] = mapped_column(
        String(JOIN_CODE_LENGTH), unique=True, index=True, nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), default="EUR", nullable=False)
    #: Doing a chore *for* whoever is on duty normally hands the turn on to the person
    #: who did it. In a household of two that means the same person twice in a row, so
    #: this switch keeps the turn where it was — the due date, points and history are
    #: unaffected either way.
    takeover_keeps_turn: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    members: Mapped[list[User]] = relationship(back_populates="household")
    chores: Mapped[list[Chore]] = relationship(
        back_populates="household", cascade="all, delete-orphan"
    )
    shopping_items: Mapped[list[ShoppingItem]] = relationship(
        back_populates="household", cascade="all, delete-orphan"
    )
    expenses: Mapped[list[Expense]] = relationship(
        back_populates="household", cascade="all, delete-orphan"
    )
    settlement_periods: Mapped[list[SettlementPeriod]] = relationship(
        back_populates="household", cascade="all, delete-orphan"
    )
    feed_events: Mapped[list[FeedEvent]] = relationship(
        back_populates="household", cascade="all, delete-orphan"
    )
