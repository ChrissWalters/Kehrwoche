"""Chores: rotation, completion and the undo window.

The six steps of a completion are prescribed by the specification and all happen in one
transaction: log the completion, award the points, hand the chore on, work out the next
due date, write the feed event, tell the next person.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.errors import AppError, ErrorCode
from app.i18n import FALLBACK_LOCALE, load_catalogue
from app.models import (
    Chore,
    ChoreCompletion,
    FeedEvent,
    FeedEventType,
    Household,
    NotificationType,
    User,
)
from app.models.base import utcnow
from app.models.chore import ON_DEMAND
from app.services.feed import emit_event
from app.services.notifications import notify

#: How long a completion can be taken back — long enough for a mistap, short enough that
#: the household can rely on what it sees.
UNDO_WINDOW = timedelta(minutes=5)

#: Key of the template list inside a language catalogue.
TEMPLATES_KEY = "chore_templates"
#: Window of the "recently" column in the statistics.
RECENT_DAYS = 30
#: Page size of the history.
HISTORY_PAGE_SIZE = 20


def next_due_at(
    *,
    fixed: bool,
    rotation_seconds: int,
    previous_due_at: datetime | None,
    done_at: datetime,
) -> datetime | None:
    """When the chore is due after a completion.

    ``fixed`` keeps the grid stable — Monday stays Monday even when the work was done on
    Tuesday. After a longer gap the date skips whole intervals until it lies ahead of us:
    the bin cannot be put out three times for one collection, so missed rounds lapse
    instead of piling up as overdue work. Without ``fixed`` the interval simply starts at
    the moment the work was actually done.
    """
    if rotation_seconds == ON_DEMAND:
        return None
    interval = timedelta(seconds=rotation_seconds)
    if not fixed or previous_due_at is None:
        return done_at + interval

    next_due = previous_due_at + interval
    if next_due <= done_at:
        missed = (done_at - previous_due_at) // interval
        next_due = previous_due_at + (missed + 1) * interval
    return next_due


def next_member(member_order: list[int], current_user_id: int | None) -> int | None:
    """The next person in the rotation, wrapping around at the end."""
    if not member_order:
        return None
    if current_user_id is None or current_user_id not in member_order:
        return member_order[0]
    position = member_order.index(current_user_id)
    return member_order[(position + 1) % len(member_order)]


def get_chore(db: DbSession, household: Household, chore_id: int) -> Chore:
    """A chore of this household; anything else does not exist for the caller."""
    chore = db.get(Chore, chore_id)
    if chore is None or chore.household_id != household.id:
        raise AppError(
            404,
            ErrorCode.NOT_FOUND,
            "Chore not found.",
            message_key="error.chore.not_found",
        )
    return chore


def list_chores(db: DbSession, household: Household) -> list[Chore]:
    """Sorted the way the list is read: what is due first, on-demand chores last."""
    chores = db.scalars(select(Chore).where(Chore.household_id == household.id)).all()
    return sorted(chores, key=lambda chore: (chore.due_at is None, chore.due_at, chore.id))


def _validate_member_order(db: DbSession, household: Household, member_order: list[int]) -> None:
    if not member_order:
        raise AppError(
            400,
            ErrorCode.VALIDATION_ERROR,
            "The rotation needs at least one person.",
            "member_order",
            message_key="error.rotation.empty",
        )
    if len(set(member_order)) != len(member_order):
        raise AppError(
            400,
            ErrorCode.VALIDATION_ERROR,
            "A person can only appear once.",
            "member_order",
            message_key="error.duplicate_person",
        )
    known = set(db.scalars(select(User.id).where(User.household_id == household.id)))
    if not set(member_order) <= known:
        raise AppError(
            400,
            ErrorCode.VALIDATION_ERROR,
            "The rotation may only contain members of this household.",
            "member_order",
            message_key="error.rotation.not_a_member",
        )


def household_member_ids(db: DbSession, household: Household) -> list[int]:
    return list(
        db.scalars(select(User.id).where(User.household_id == household.id).order_by(User.id))
    )


def _complete_member_order(db: DbSession, household: Household, order: list[int]) -> list[int]:
    """The order as sent, plus every member it does not mention, appended at the end.

    A client that has not seen the newest member yet would otherwise write a rotation
    without them, and the person would never get a turn. Somebody joining a second later
    is appended by :func:`add_member_to_rotations` — whether they joined shortly before
    or shortly after a chore was created must not decide whether they are in the plan.
    """
    missing = [
        member_id for member_id in household_member_ids(db, household) if member_id not in order
    ]
    return [*order, *missing]


def create_chore(
    db: DbSession,
    household: Household,
    actor: User,
    *,
    title: str,
    description: str | None = None,
    points: int = 0,
    rotation_seconds: int = ON_DEMAND,
    fixed: bool = False,
    member_order: list[int] | None = None,
    due_at: datetime | None = None,
) -> Chore:
    order = member_order if member_order is not None else household_member_ids(db, household)
    _validate_member_order(db, household, order)
    order = _complete_member_order(db, household, order)

    if rotation_seconds == ON_DEMAND:
        due = None
    else:
        due = due_at or utcnow() + timedelta(seconds=rotation_seconds)

    chore = Chore(
        household_id=household.id,
        title=title.strip(),
        description=description,
        points=points,
        rotation_seconds=rotation_seconds,
        fixed=fixed,
        member_order=list(order),
        current_user_id=order[0],
        due_at=due,
    )
    db.add(chore)
    db.flush()
    emit_event(
        db,
        household,
        FeedEventType.CHORE_CREATED,
        actor=actor,
        reference_type="chore",
        reference_id=chore.id,
        body=chore.title,
    )
    db.commit()
    db.refresh(chore)
    return chore


#: What an edit can touch, in the order the entry reads them out. The values are the
#: second half of an i18n key (``chores.field.title`` and so on), so the pinboard names
#: the fields in the language of whoever is reading.
TRACKED_FIELDS = (
    "title",
    "description",
    "points",
    "rotation_seconds",
    "member_order",
    "current_user_id",
    "due_at",
)


def _snapshot(chore: Chore) -> dict[str, object]:
    """The values an edit is compared against — a copy, not a view of the live object."""
    return {
        name: list(value) if isinstance(value := getattr(chore, name), list) else value
        for name in TRACKED_FIELDS
    }


def update_chore(
    db: DbSession,
    household: Household,
    chore: Chore,
    *,
    actor: User | None = None,
    title: str | None = None,
    description: str | None = None,
    points: int | None = None,
    rotation_seconds: int | None = None,
    fixed: bool | None = None,
    member_order: list[int] | None = None,
    current_user_id: int | None = None,
    due_at: datetime | None = None,
) -> Chore:
    before = _snapshot(chore)

    if title is not None:
        chore.title = title.strip()
    if description is not None:
        chore.description = description or None
    if points is not None:
        chore.points = points
    if fixed is not None:
        chore.fixed = fixed
    if member_order is not None:
        _validate_member_order(db, household, member_order)
    # Also when the request does not mention the rotation at all: a chore that was
    # written from a stale member list heals as soon as anybody edits it, whatever they
    # came to change.
    chore.member_order = _complete_member_order(db, household, member_order or chore.member_order)
    if chore.current_user_id not in chore.member_order:
        chore.current_user_id = chore.member_order[0]
    if current_user_id is not None:
        if current_user_id not in chore.member_order:
            raise AppError(
                400,
                ErrorCode.VALIDATION_ERROR,
                "That person is not part of this rotation.",
                "current_user_id",
                message_key="error.rotation.person_missing",
            )
        chore.current_user_id = current_user_id
    if rotation_seconds is not None:
        chore.rotation_seconds = rotation_seconds
        if rotation_seconds == ON_DEMAND:
            chore.due_at = None
        elif chore.due_at is None:
            chore.due_at = utcnow() + timedelta(seconds=rotation_seconds)
    if due_at is not None:
        if chore.rotation_seconds == ON_DEMAND:
            raise AppError(
                400,
                ErrorCode.VALIDATION_ERROR,
                "A chore done when needed has no due date.",
                "due_at",
                message_key="error.chore.on_demand_has_no_due_date",
            )
        chore.due_at = due_at

    # The pinboard is the audit log, so an edit belongs in it — but only a real one:
    # opening the form and saving it unchanged is not something that happened. The
    # comparison is against the stored values, not against what the request mentioned,
    # so the rotation repair above shows up too. That is on purpose — the order really
    # did change, and an audit log that hides changes is not one.
    changed = [name for name in TRACKED_FIELDS if before[name] != _snapshot(chore)[name]]
    if changed and actor is not None:
        emit_event(
            db,
            household,
            FeedEventType.CHORE_UPDATED,
            actor=actor,
            reference_type="chore",
            reference_id=chore.id,
            body=chore.title,
            params={"fields": changed},
        )

    db.commit()
    db.refresh(chore)
    return chore


def delete_chore(db: DbSession, chore: Chore, actor: User | None = None) -> None:
    """The chore goes; the entry saying it went stays.

    ``reference_id`` keeps pointing at an id that no longer exists — deliberately. The
    feed events carry no foreign key precisely so the record of a deletion survives the
    thing it describes.
    """
    if actor is not None:
        emit_event(
            db,
            chore.household,
            FeedEventType.CHORE_DELETED,
            actor=actor,
            reference_type="chore",
            reference_id=chore.id,
            body=chore.title,
        )
    db.delete(chore)
    db.commit()


def resolve_credited(db: DbSession, chore: Chore, actor: User, for_user_id: int | None) -> User:
    """Who the completion is credited to — the actor unless somebody books for another."""
    if for_user_id is None or for_user_id == actor.id:
        return actor
    credited = db.get(User, for_user_id)
    if credited is None or credited.household_id != chore.household_id:
        raise AppError(
            400,
            ErrorCode.VALIDATION_ERROR,
            "That person is not a member of this household.",
            "for_user_id",
            message_key="error.member.not_in_household",
        )
    return credited


def complete_chore(
    db: DbSession, chore: Chore, actor: User, *, for_user_id: int | None = None
) -> ChoreCompletion:
    """The six prescribed steps.

    Anybody may do the work. By default the points go to whoever books it; booking on
    behalf of somebody else credits them instead and records who did the booking.
    """
    credited = resolve_credited(db, chore, actor, for_user_id)
    done_at = utcnow()

    completion = ChoreCompletion(
        chore_id=chore.id,
        user_id=credited.id,
        booked_by_id=None if credited.id == actor.id else actor.id,
        done_at=done_at,
        points_awarded=chore.points,
        previous_user_id=chore.current_user_id,
        previous_due_at=chore.due_at,
    )
    db.add(completion)

    credited.points += chore.points

    # Doing somebody else's turn normally hands the turn on to whoever did it — in a
    # household of two that is the same person again, which is the opposite of fair.
    # With the switch on, the turn stays where it was; everything else is unaffected.
    stands_in = chore.current_user_id is not None and credited.id != chore.current_user_id
    keeps_turn = stands_in and chore.household.takeover_keeps_turn

    successor_id = (
        chore.current_user_id
        if keeps_turn
        else next_member(chore.member_order, chore.current_user_id)
    )
    chore.current_user_id = successor_id
    chore.due_at = next_due_at(
        fixed=chore.fixed,
        rotation_seconds=chore.rotation_seconds,
        previous_due_at=completion.previous_due_at,
        done_at=done_at,
    )
    chore.last_done_at = done_at

    # The feed names who the work is credited to; who tapped is kept on the completion
    # and shown in the history.
    emit_event(
        db,
        chore.household,
        FeedEventType.CHORE_DONE,
        actor=credited,
        reference_type="chore",
        reference_id=chore.id,
        body=chore.title,
    )

    # Nobody is told "you are up" when the turn did not actually move — they knew.
    successor = db.get(User, successor_id) if successor_id and not keeps_turn else None
    if successor is not None and successor.id != actor.id:
        notify(
            db,
            successor,
            NotificationType.CHORE_ASSIGNED,
            params={"chore": chore.title},
            reference_type="chore",
            reference_id=chore.id,
        )

    db.commit()
    db.refresh(completion)
    return completion


def undo_completion(db: DbSession, chore: Chore, actor: User) -> None:
    """Take back the most recent completion — same six steps, backwards."""
    completion = db.scalars(
        select(ChoreCompletion)
        .where(ChoreCompletion.chore_id == chore.id)
        .order_by(ChoreCompletion.done_at.desc(), ChoreCompletion.id.desc())
    ).first()

    if completion is None or utcnow() - completion.done_at > UNDO_WINDOW:
        raise AppError(
            409, ErrorCode.UNDO_WINDOW_EXPIRED, "There is nothing left to take back here."
        )
    # The undo window corrects a mistap, so it belongs to whoever tapped — not to the
    # person the completion was credited to, who would not even learn about it in time.
    booked_by = completion.booked_by_id or completion.user_id
    if actor.id != booked_by:
        raise AppError(
            403,
            ErrorCode.FORBIDDEN,
            "Only the person who booked it can undo it.",
            message_key="error.chore.undo_not_yours",
        )

    booker = db.get(User, completion.user_id)
    if booker is not None:
        # Never below zero, even if the statistics were reset in between.
        booker.points = max(0, booker.points - completion.points_awarded)

    chore.current_user_id = completion.previous_user_id
    chore.due_at = completion.previous_due_at

    previous = db.scalars(
        select(ChoreCompletion)
        .where(ChoreCompletion.chore_id == chore.id, ChoreCompletion.id != completion.id)
        .order_by(ChoreCompletion.done_at.desc(), ChoreCompletion.id.desc())
    ).first()
    chore.last_done_at = previous.done_at if previous else None

    # The feed is an audit log: remove the entry the completion wrote, nothing else.
    event = db.scalars(
        select(FeedEvent)
        .where(
            FeedEvent.household_id == chore.household_id,
            FeedEvent.type == FeedEventType.CHORE_DONE,
            FeedEvent.reference_type == "chore",
            FeedEvent.reference_id == chore.id,
            FeedEvent.actor_id == completion.user_id,
        )
        .order_by(FeedEvent.id.desc())
    ).first()
    if event is not None:
        db.delete(event)

    db.delete(completion)
    db.commit()


def remind(db: DbSession, chore: Chore, actor: User) -> User:
    """Nudge whoever is on duty.

    The throttling lives in the router: once a day per chore and per reminding person.
    """
    if chore.current_user_id is None:
        raise AppError(
            409,
            ErrorCode.CONFLICT,
            "Nobody is on duty for this chore.",
            message_key="error.chore.nobody_on_duty",
        )
    responsible = db.get(User, chore.current_user_id)
    if responsible is None:
        raise AppError(
            409,
            ErrorCode.CONFLICT,
            "Nobody is on duty for this chore.",
            message_key="error.chore.nobody_on_duty",
        )

    notify(
        db,
        responsible,
        NotificationType.CHORE_REMINDER,
        params={"chore": chore.title, "by": actor.first_name},
        reference_type="chore",
        reference_id=chore.id,
    )
    db.commit()
    return responsible


def add_member_to_rotations(db: DbSession, household_id: int, user_id: int) -> None:
    """Put a new member at the end of every rotation of their household.

    Anything else would leave them out of the plan for good: the list is only extended
    when a chore is created, so somebody joining later would never get a turn. The end
    is the fair spot — the current cycle finishes first.
    """
    for chore in db.scalars(select(Chore).where(Chore.household_id == household_id)):
        if user_id in chore.member_order:
            continue
        chore.member_order = [*chore.member_order, user_id]
        # A rotation that had nobody left gets its first person back.
        if chore.current_user_id is None:
            chore.current_user_id = user_id


def remove_member_from_rotations(db: DbSession, household_id: int, user_id: int) -> None:
    """Take a departed member out of every rotation of their household.

    Whoever was on duty hands over to the next person in line; a rotation that would be
    left empty keeps nobody on duty until somebody edits the chore.
    """
    chores = db.scalars(select(Chore).where(Chore.household_id == household_id)).all()
    for chore in chores:
        if user_id in chore.member_order:
            successor_id = next_member(chore.member_order, user_id)
            chore.member_order = [member for member in chore.member_order if member != user_id]
            if chore.current_user_id == user_id:
                chore.current_user_id = successor_id if successor_id != user_id else None
        elif chore.current_user_id == user_id:
            chore.current_user_id = chore.member_order[0] if chore.member_order else None


def list_templates(
    user: User, settings: Settings, locale: str | None = None
) -> list[dict[str, object]]:
    """Suggestions for setting up, in the language currently on screen.

    ``locale`` comes from the client and wins over the profile language; a mounted
    language file may contain anything at all, so entries that do not carry a usable
    title are skipped instead of breaking the view.
    """
    catalogue = load_catalogue(locale or user.locale, settings) or {}
    entries = catalogue.get(TEMPLATES_KEY)
    if not isinstance(entries, list) or not entries:
        fallback = load_catalogue(FALLBACK_LOCALE, settings) or {}
        entries = fallback.get(TEMPLATES_KEY, [])

    templates: list[dict[str, object]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        title = entry.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        templates.append(
            {
                "title": title,
                "rotation_seconds": int(entry.get("rotation_seconds", ON_DEMAND)),
                "points": max(0, int(entry.get("points", 0))),
                "fixed": bool(entry.get("fixed", False)),
            }
        )
    return templates


def history(
    db: DbSession,
    household: Household,
    *,
    cursor: int | None = None,
    limit: int = HISTORY_PAGE_SIZE,
) -> tuple[list[tuple[ChoreCompletion, str]], int | None]:
    """Completions of the household, newest first, one page at a time.

    The cursor is the id of the last entry that was already delivered — ids grow with
    every completion, so paging cannot skip or repeat an entry.
    """
    query = (
        select(ChoreCompletion, Chore.title)
        .join(Chore, Chore.id == ChoreCompletion.chore_id)
        .where(Chore.household_id == household.id)
        .order_by(ChoreCompletion.id.desc())
        .limit(limit + 1)
    )
    if cursor is not None:
        query = query.where(ChoreCompletion.id < cursor)

    rows = [(completion, title) for completion, title in db.execute(query).all()]
    next_cursor = rows[limit - 1][0].id if len(rows) > limit else None
    return rows[:limit], next_cursor


def statistics(db: DbSession, household: Household) -> list[dict[str, object]]:
    """Points and completion counts per member, the leader board of the household."""
    since = utcnow() - timedelta(days=RECENT_DAYS)
    members = db.scalars(
        select(User).where(User.household_id == household.id).order_by(User.id)
    ).all()

    counted = db.execute(
        select(ChoreCompletion.user_id, ChoreCompletion.done_at)
        .join(Chore, Chore.id == ChoreCompletion.chore_id)
        .where(Chore.household_id == household.id)
    ).all()

    totals: dict[int, int] = {}
    recent: dict[int, int] = {}
    for user_id, done_at in counted:
        totals[user_id] = totals.get(user_id, 0) + 1
        if done_at >= since:
            recent[user_id] = recent.get(user_id, 0) + 1

    rows = [
        {
            "user_id": member.id,
            "first_name": member.first_name,
            "points": member.points,
            "completions": totals.get(member.id, 0),
            "completions_recent": recent.get(member.id, 0),
        }
        for member in members
    ]
    # Leader board: most points first, ties in a stable order.
    rows.sort(key=lambda row: (-int(row["points"]), str(row["first_name"]), int(row["user_id"])))
    return rows


def reset_statistics(db: DbSession, household: Household, actor: User) -> None:
    """Start the points over. The history stays — only the score is zeroed."""
    for member in db.scalars(select(User).where(User.household_id == household.id)):
        member.points = 0
    emit_event(db, household, FeedEventType.CHORE_STATISTICS_RESET, actor=actor)
    db.commit()
