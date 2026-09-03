"""The household kitty: shared expenses and how they are divided.

Amounts are integer cents throughout. Splitting is deterministic — the same expense
always produces the same shares, no matter who enters it or in which order the client
sends the participants.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import selectinload

from app.errors import AppError, ErrorCode
from app.models import (
    Expense,
    ExpenseShare,
    FeedEventType,
    Household,
    NotificationType,
    SettlementPayment,
    SettlementPeriod,
    User,
)
from app.models.base import utcnow
from app.schemas.expenses import ExpenseShareRequest
from app.services.feed import emit_event
from app.services.notifications import notify

#: Expenses per page of the list.
PAGE_SIZE = 20
#: Separator of the list cursor: date of the expense and its id.
CURSOR_SEPARATOR = ":"


def clean_title(title: str) -> str:
    """A title has to survive trimming — "   " is not a purpose."""
    cleaned = title.strip()
    if not cleaned:
        raise AppError(
            400,
            ErrorCode.VALIDATION_ERROR,
            "The expense needs a title.",
            "title",
            message_key="error.expense.title_required",
        )
    return cleaned


def split_evenly(amount_cents: int, user_ids: Sequence[int]) -> list[tuple[int, int]]:
    """Divide an amount evenly; the leftover cents go to the first participants.

    10 € on three people is 3,34 € / 3,33 € / 3,33 € — the cents have to land somewhere,
    and they always land in the same place: participants are ordered by their id, so the
    result does not depend on the order the client happened to send.
    """
    ordered = sorted(set(user_ids))
    base, remainder = divmod(amount_cents, len(ordered))
    return [
        (user_id, base + (1 if index < remainder else 0)) for index, user_id in enumerate(ordered)
    ]


def _member_ids(db: DbSession, household: Household) -> list[int]:
    return list(
        db.scalars(select(User.id).where(User.household_id == household.id).order_by(User.id))
    )


def _require_members(db: DbSession, household: Household, user_ids: Sequence[int]) -> None:
    """Everybody involved has to belong to this household.

    A foreign id is answered with 404 like any other foreign object: whether that account
    exists elsewhere is none of this household's business.
    """
    known = set(_member_ids(db, household))
    if not set(user_ids) <= known:
        raise AppError(
            404,
            ErrorCode.NOT_FOUND,
            "Member not found.",
            message_key="error.member.not_found",
        )


def resolve_shares(
    db: DbSession,
    household: Household,
    *,
    amount_cents: int,
    participants: Sequence[int] | None,
    shares: Sequence[ExpenseShareRequest] | None,
) -> list[tuple[int, int]]:
    """The final split, either evenly over a selection or exactly as sent.

    Without any hint the whole household carries the expense evenly — that is the case
    people enter at the till, and it saves them the selection.
    """
    if participants is not None and shares is not None:
        raise AppError(
            400,
            ErrorCode.VALIDATION_ERROR,
            "Send either an even split over participants or manual shares, not both.",
            "shares",
            message_key="error.expense.split_ambiguous",
        )

    if shares is not None:
        user_ids = [share.user_id for share in shares]
        if not user_ids:
            raise AppError(
                400,
                ErrorCode.VALIDATION_ERROR,
                "Nobody shares this expense.",
                "shares",
                message_key="error.expense.no_participants",
            )
        if len(set(user_ids)) != len(user_ids):
            raise AppError(
                400,
                ErrorCode.VALIDATION_ERROR,
                "A person can only appear once.",
                "shares",
                message_key="error.duplicate_person",
            )
        _require_members(db, household, user_ids)
        total = sum(share.share_cents for share in shares)
        if total != amount_cents:
            raise AppError(
                400,
                ErrorCode.SHARES_MISMATCH,
                f"The shares add up to {total} cents instead of {amount_cents}.",
                "shares",
            )
        return [(share.user_id, share.share_cents) for share in shares]

    chosen = list(participants) if participants is not None else _member_ids(db, household)
    if not chosen:
        raise AppError(
            400,
            ErrorCode.VALIDATION_ERROR,
            "Nobody shares this expense.",
            "participants",
            message_key="error.expense.no_participants",
        )
    _require_members(db, household, chosen)
    return split_evenly(amount_cents, chosen)


def _apply_shares(db: DbSession, expense: Expense, shares: Sequence[tuple[int, int]]) -> None:
    """Replace the split of an expense — shares are never patched line by line."""
    expense.shares = []
    # The old rows have to be gone before the new ones arrive, otherwise a person who
    # stays in the split collides with their own previous row.
    db.flush()
    for user_id, share_cents in shares:
        db.add(ExpenseShare(expense_id=expense.id, user_id=user_id, share_cents=share_cents))
    db.flush()


def get_expense(db: DbSession, household: Household, expense_id: int) -> Expense:
    """An expense of this household; anything else does not exist for the caller."""
    expense = db.get(Expense, expense_id)
    if expense is None or expense.household_id != household.id:
        raise AppError(
            404,
            ErrorCode.NOT_FOUND,
            "Expense not found.",
            message_key="error.expense.not_found",
        )
    return expense


def require_open_period(expense: Expense) -> Expense:
    """Archived periods are the settled past — they cannot be rewritten."""
    if expense.period_id is not None:
        raise AppError(
            409,
            ErrorCode.CONFLICT,
            "This expense belongs to an archived settlement period.",
            message_key="error.expense.archived",
        )
    return expense


def create_expense(
    db: DbSession,
    household: Household,
    actor: User,
    *,
    title: str,
    amount_cents: int,
    paid_by_id: int | None = None,
    spent_at: date | None = None,
    participants: Sequence[int] | None = None,
    shares: Sequence[ExpenseShareRequest] | None = None,
) -> Expense:
    payer_id = paid_by_id if paid_by_id is not None else actor.id
    _require_members(db, household, [payer_id])
    split = resolve_shares(
        db, household, amount_cents=amount_cents, participants=participants, shares=shares
    )

    expense = Expense(
        household_id=household.id,
        title=clean_title(title),
        amount_cents=amount_cents,
        paid_by_id=payer_id,
        spent_at=spent_at or utcnow().date(),
    )
    db.add(expense)
    db.flush()
    _apply_shares(db, expense, split)

    emit_event(
        db,
        household,
        FeedEventType.EXPENSE_ADDED,
        actor=actor,
        reference_type="expense",
        reference_id=expense.id,
        body=expense.title,
    )
    db.commit()
    db.refresh(expense)
    return expense


def update_expense(
    db: DbSession,
    household: Household,
    expense: Expense,
    *,
    title: str | None = None,
    amount_cents: int | None = None,
    paid_by_id: int | None = None,
    spent_at: date | None = None,
    participants: Sequence[int] | None = None,
    shares: Sequence[ExpenseShareRequest] | None = None,
) -> Expense:
    """Change an expense of the open period.

    A new amount without a new split is refused instead of silently redistributing: a
    household that agreed on 70/30 must not end up at 50/50 because somebody corrected
    the total.
    """
    require_open_period(expense)

    amount = amount_cents if amount_cents is not None else expense.amount_cents
    if amount_cents is not None and participants is None and shares is None:
        raise AppError(
            400,
            ErrorCode.SHARES_MISMATCH,
            "A new amount needs the split that goes with it.",
            "shares",
            message_key="error.expense.amount_needs_shares",
        )

    if title is not None:
        expense.title = clean_title(title)
    if paid_by_id is not None:
        _require_members(db, household, [paid_by_id])
        expense.paid_by_id = paid_by_id
    if spent_at is not None:
        expense.spent_at = spent_at
    expense.amount_cents = amount

    if participants is not None or shares is not None:
        split = resolve_shares(
            db, household, amount_cents=amount, participants=participants, shares=shares
        )
        _apply_shares(db, expense, split)
        # A changed split alone leaves the row itself untouched, and the change marker
        # of the module would not move. Touching it keeps open views in sync.
        expense.updated_at = utcnow()

    db.commit()
    db.refresh(expense)
    return expense


def delete_expense(db: DbSession, expense: Expense) -> None:
    require_open_period(expense)
    db.delete(expense)
    db.commit()


def _open_expenses(db: DbSession, household: Household) -> list[Expense]:
    """Everything that has not been settled yet, oldest first."""
    return list(
        db.scalars(
            select(Expense)
            .where(Expense.household_id == household.id, Expense.period_id.is_(None))
            .options(selectinload(Expense.shares))
            .order_by(Expense.id)
        )
    )


def _totals(db: DbSession, household: Household) -> tuple[dict[int, int], dict[int, int]]:
    """What each person paid and what each person carries, over the open period."""
    paid: dict[int, int] = {}
    owed: dict[int, int] = {}
    for expense in _open_expenses(db, household):
        paid[expense.paid_by_id] = paid.get(expense.paid_by_id, 0) + expense.amount_cents
        for share in expense.shares:
            owed[share.user_id] = owed.get(share.user_id, 0) + share.share_cents
    return paid, owed


def balances(db: DbSession, household: Household) -> list[dict[str, object]]:
    """Paid minus carried, per person, over the open period.

    Everybody who is involved appears — the current members plus anyone who paid or
    carries a share and has meanwhile moved out. Leaving the household does not settle
    a debt, and dropping those rows would break the promise that the balances add up to
    zero.
    """
    paid, owed = _totals(db, household)
    involved = set(_member_ids(db, household)) | set(paid) | set(owed)
    people = db.scalars(select(User).where(User.id.in_(involved)).order_by(User.id)).all()
    # Empty where the rule says "no name": the client then shows its own translated
    # "former member" text instead of a name nobody is entitled to see any more.
    nameable = nameable_people(db, household)

    return [
        {
            "user_id": person.id,
            "first_name": nameable.get(person.id, ""),
            "paid_cents": paid.get(person.id, 0),
            "owed_cents": owed.get(person.id, 0),
            "balance_cents": paid.get(person.id, 0) - owed.get(person.id, 0),
        }
        for person in people
    ]


def nameable_people(db: DbSession, household: Household) -> dict[int, str]:
    """Who may be shown by name in the kitty — and nobody else.

    Current members always; people who have left only while their balance in the open
    period is not zero. A name is visible exactly as long as it is needed to say who
    still owes what; once the period is archived, the placeholder takes over on its own.
    """
    names = {
        user.id: user.first_name
        for user in db.scalars(select(User).where(User.household_id == household.id))
    }
    outstanding = {
        user_id
        for user_id, cents in balance_map(db, household).items()
        if cents != 0 and user_id not in names
    }
    if outstanding:
        for user in db.scalars(select(User).where(User.id.in_(outstanding))):
            names[user.id] = user.first_name
    return names


def has_open_balance(db: DbSession, user_id: int) -> bool:
    """Does this person still owe or lend something in any unsettled period?

    Asked without a household on purpose: it is used after somebody has already left,
    when their membership no longer says where to look.
    """
    involved = select(Expense).where(
        Expense.period_id.is_(None),
        or_(
            Expense.paid_by_id == user_id,
            Expense.id.in_(select(ExpenseShare.expense_id).where(ExpenseShare.user_id == user_id)),
        ),
    )
    paid = 0
    owed = 0
    for expense in db.scalars(involved.options(selectinload(Expense.shares))):
        if expense.paid_by_id == user_id:
            paid += expense.amount_cents
        owed += sum(share.share_cents for share in expense.shares if share.user_id == user_id)
    return paid != owed


def balance_map(db: DbSession, household: Household) -> dict[int, int]:
    """Only the numbers the settlement needs: member id → balance in cents."""
    paid, owed = _totals(db, household)
    return {
        user_id: paid.get(user_id, 0) - owed.get(user_id, 0) for user_id in set(paid) | set(owed)
    }


def settlement_payments(balances_by_user: dict[int, int]) -> list[tuple[int, int, int]]:
    """The payments that bring every balance to zero: who pays whom how much.

    Greedy, as the specification prescribes: the largest debtor pays the largest
    creditor as much as possible. Every payment settles at least one of the two, so
    there are never more than n−1 payments for n people. Ties are broken by the member
    id, which makes the proposal reproducible — the same books always yield the same
    list, and nobody has to wonder why it changed between two taps.
    """
    debtors = sorted(
        ((user_id, -amount) for user_id, amount in balances_by_user.items() if amount < 0),
        key=lambda entry: (-entry[1], entry[0]),
    )
    creditors = sorted(
        ((user_id, amount) for user_id, amount in balances_by_user.items() if amount > 0),
        key=lambda entry: (-entry[1], entry[0]),
    )

    payments: list[tuple[int, int, int]] = []
    while debtors and creditors:
        debtor, debt = debtors[0]
        creditor, credit = creditors[0]
        amount = min(debt, credit)
        payments.append((debtor, creditor, amount))

        debtors = _reduced(debtors, debt - amount)
        creditors = _reduced(creditors, credit - amount)
    return payments


def _reduced(entries: list[tuple[int, int]], remainder: int) -> list[tuple[int, int]]:
    """Drop the settled head or re-insert it with what is left, keeping the order."""
    rest = entries[1:]
    if remainder == 0:
        return rest
    head = (entries[0][0], remainder)
    return sorted([head, *rest], key=lambda entry: (-entry[1], entry[0]))


def settlement(db: DbSession, household: Household) -> list[tuple[int, int, int]]:
    """The current payment proposal, calculated live — nothing is stored."""
    return settlement_payments(balance_map(db, household))


def archive_period(db: DbSession, household: Household, admin: User) -> SettlementPeriod:
    """Close the open period: freeze the expenses and the payments that settle them.

    One transaction, because a half-archived period would leave the household with books
    nobody can trust: the expenses have to move, the payments have to be frozen, and the
    balances have to start at zero — together or not at all.
    """
    expenses = _open_expenses(db, household)
    if not expenses:
        raise AppError(
            409,
            ErrorCode.CONFLICT,
            "There is nothing to settle.",
            message_key="error.settlement.nothing_to_settle",
        )

    payments = settlement(db, household)

    period = SettlementPeriod(household_id=household.id, closed_at=utcnow(), closed_by_id=admin.id)
    db.add(period)
    db.flush()

    for expense in expenses:
        expense.period_id = period.id
    for debtor_id, creditor_id, amount in payments:
        db.add(
            SettlementPayment(
                period_id=period.id,
                from_user_id=debtor_id,
                to_user_id=creditor_id,
                amount_cents=amount,
            )
        )

    emit_event(
        db,
        household,
        FeedEventType.SETTLEMENT_ARCHIVED,
        actor=admin,
        reference_type="settlement_period",
        reference_id=period.id,
        body=str(len(expenses)),
    )

    # Everybody who owes something learns about it — once, with the total. One
    # settlement is one piece of news, even when it is paid to two people.
    owed_by: dict[int, list[int]] = {}
    for debtor_id, _, amount in payments:
        owed_by.setdefault(debtor_id, []).append(amount)
    for debtor_id, amounts in owed_by.items():
        debtor = db.get(User, debtor_id)
        if debtor is not None:
            notify(
                db,
                debtor,
                NotificationType.SETTLEMENT_DUE,
                params={"amount_cents": sum(amounts), "payments": len(amounts)},
                reference_type="settlement_period",
                reference_id=period.id,
            )

    db.commit()
    db.refresh(period)
    return period


def _period_summary(period: SettlementPeriod) -> dict[str, object]:
    return {
        "id": period.id,
        "closed_at": period.closed_at,
        "closed_by_id": period.closed_by_id,
        "expense_count": len(period.expenses),
        "total_cents": sum(expense.amount_cents for expense in period.expenses),
    }


def list_periods(db: DbSession, household: Household) -> list[dict[str, object]]:
    """The archive, newest period first."""
    periods = db.scalars(
        select(SettlementPeriod)
        .where(SettlementPeriod.household_id == household.id)
        .options(selectinload(SettlementPeriod.expenses))
        .order_by(SettlementPeriod.id.desc())
    ).all()
    return [_period_summary(period) for period in periods]


def get_period(db: DbSession, household: Household, period_id: int) -> SettlementPeriod:
    """An archived period of this household; anything else does not exist for the caller."""
    period = db.get(SettlementPeriod, period_id)
    if period is None or period.household_id != household.id:
        raise AppError(
            404,
            ErrorCode.NOT_FOUND,
            "Settlement period not found.",
            message_key="error.period.not_found",
        )
    return period


def period_detail(period: SettlementPeriod) -> dict[str, object]:
    """Summary plus everything the period froze."""
    return {
        **_period_summary(period),
        "expenses": sorted(period.expenses, key=lambda e: (e.spent_at, e.id), reverse=True),
        "payments": sorted(
            period.payments, key=lambda p: (-p.amount_cents, p.from_user_id, p.to_user_id)
        ),
    }


def _cursor_condition(cursor: str) -> ColumnElement[bool]:
    """Everything that comes after the last delivered entry.

    The list is ordered by date and, within a day, by id — so the cursor carries both.
    Written as a plain OR instead of a row value comparison, which not every supported
    database spells the same way.
    """
    stamp, _, raw_id = cursor.partition(CURSOR_SEPARATOR)
    try:
        last_date = date.fromisoformat(stamp)
        last_id = int(raw_id)
    except ValueError as error:
        raise AppError(400, ErrorCode.VALIDATION_ERROR, "Invalid cursor.", "cursor") from error
    return or_(
        Expense.spent_at < last_date,
        (Expense.spent_at == last_date) & (Expense.id < last_id),
    )


def list_expenses(
    db: DbSession,
    household: Household,
    user: User,
    *,
    mine: bool = False,
    cursor: str | None = None,
    limit: int = PAGE_SIZE,
) -> tuple[list[Expense], str | None]:
    """The open period, newest expense first, one page at a time.

    ``mine`` narrows the list to what concerns the requesting person: expenses they paid
    or carry a share of. Archived periods have their own endpoint (AP20) and never show
    up here.
    """
    query = (
        select(Expense)
        .where(Expense.household_id == household.id, Expense.period_id.is_(None))
        .options(selectinload(Expense.shares))
        .order_by(Expense.spent_at.desc(), Expense.id.desc())
        .limit(limit + 1)
    )
    if mine:
        query = query.where(
            or_(
                Expense.paid_by_id == user.id,
                Expense.id.in_(
                    select(ExpenseShare.expense_id).where(ExpenseShare.user_id == user.id)
                ),
            )
        )
    if cursor is not None:
        query = query.where(_cursor_condition(cursor))

    rows = list(db.scalars(query))
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = f"{last.spent_at.isoformat()}{CURSOR_SEPARATOR}{last.id}"
    return rows[:limit], next_cursor
