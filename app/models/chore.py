"""Chores with their rotation state and the completion log."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Entity, UtcDateTime

if TYPE_CHECKING:
    from app.models.household import Household
    from app.models.user import User

#: ``rotation_seconds`` value for chores that are done "when needed" instead of on a clock.
ON_DEMAND = -1


class Chore(Entity):
    __tablename__ = "chores"
    __table_args__ = (CheckConstraint("points >= 0", name="points_non_negative"),)

    household_id: Mapped[int] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    #: Interval in seconds; :data:`ON_DEMAND` means "no time based rotation".
    rotation_seconds: Mapped[int] = mapped_column(Integer, default=ON_DEMAND, nullable=False)
    #: ``True`` keeps the due date grid stable, ``False`` counts from the actual completion.
    fixed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    #: Ordered rotation list of user ids.
    member_order: Mapped[list[int]] = mapped_column(
        MutableList.as_mutable(JSON), default=list, nullable=False
    )
    current_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None, index=True
    )
    due_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None, index=True)
    last_done_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)

    household: Mapped[Household] = relationship(back_populates="chores")
    current_user: Mapped[User | None] = relationship()
    completions: Mapped[list[ChoreCompletion]] = relationship(
        back_populates="chore", cascade="all, delete-orphan", order_by="ChoreCompletion.done_at"
    )


class ChoreCompletion(Entity):
    """One booked completion — the basis of history and statistics."""

    __tablename__ = "chore_completions"

    chore_id: Mapped[int] = mapped_column(
        ForeignKey("chores.id", ondelete="CASCADE"), index=True, nullable=False
    )
    #: Who the completion is credited to — the points go here.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    #: Set only when somebody booked it on behalf of ``user_id``; keeps the log honest.
    booked_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    done_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, index=True)
    points_awarded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    #: State of the chore right before this completion, so undo can restore it exactly
    #: instead of guessing it back from the history.
    previous_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    previous_due_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)

    chore: Mapped[Chore] = relationship(back_populates="completions")
    # Two paths lead to users now, so the join has to be spelled out.
    user: Mapped[User] = relationship(foreign_keys=[user_id])
    booked_by: Mapped[User | None] = relationship(foreign_keys=[booked_by_id])
    previous_user: Mapped[User | None] = relationship(foreign_keys=[previous_user_id])
