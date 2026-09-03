"""Change markers, so open views can stay current without reloading everything.

A marker is a short string per module. The client polls it and only refetches the data
of the view somebody is actually looking at. Deliberately without extra bookkeeping in
the writing paths: count plus latest change is enough to notice that something happened.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.models import (
    Chore,
    ChoreCompletion,
    Comment,
    Expense,
    FeedEvent,
    Household,
    Like,
    Notification,
    ShoppingItem,
    User,
)
from app.models.base import Entity

#: Marker of a module nothing has ever been written to.
EMPTY = "0"


def _marker(db: DbSession, *conditions: tuple[type[Entity], object]) -> str:
    """Count and latest change of one or more tables, as one short string."""
    parts: list[str] = []
    for model, condition in conditions:
        count, latest = db.execute(
            select(func.count(model.id), func.max(model.updated_at)).where(condition)
        ).one()
        # Keep whatever resolution the database offers rather than rounding to a
        # lowest common denominator: both supported dialects store microseconds, and
        # throwing that away would make two changes in one second indistinguishable.
        stamp = f"{latest.timestamp():.6f}" if isinstance(latest, datetime) else "0"
        parts.append(f"{count}.{stamp}")
    return "-".join(parts) or EMPTY


def household_state(db: DbSession, household: Household, user: User) -> dict[str, str | int]:
    """One marker per module plus the badge counter of the bell."""
    # Membership and household settings. Every form builds on the member list — who is
    # on duty, who shares an expense — so a client that missed a new member would write
    # them out of the plan.
    household_marker = _marker(
        db,
        (User, User.household_id == household.id),
        (Household, Household.id == household.id),
    )
    chores = _marker(
        db,
        (Chore, Chore.household_id == household.id),
        (
            ChoreCompletion,
            ChoreCompletion.chore_id.in_(
                select(Chore.id).where(Chore.household_id == household.id)
            ),
        ),
    )
    shopping = _marker(db, (ShoppingItem, ShoppingItem.household_id == household.id))
    expenses = _marker(db, (Expense, Expense.household_id == household.id))
    household_events = select(FeedEvent.id).where(FeedEvent.household_id == household.id)
    feed = _marker(
        db,
        (FeedEvent, FeedEvent.household_id == household.id),
        (Comment, Comment.event_id.in_(household_events)),
        (Like, Like.event_id.in_(household_events)),
    )
    unread = db.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == user.id, Notification.read_at.is_(None)
        )
    )

    return {
        "household": household_marker,
        "chores": chores,
        "shopping": shopping,
        "expenses": expenses,
        "feed": feed,
        "notifications": int(unread or 0),
    }
