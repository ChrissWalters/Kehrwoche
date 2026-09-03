"""The household kitty: entering, changing and listing shared expenses."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from app.deps import AdminUser, DbSession, MemberUser
from app.models import Household, SettlementPeriod
from app.schemas.expenses import (
    BalanceResponse,
    ExpenseCreateRequest,
    ExpensePageResponse,
    ExpenseResponse,
    ExpenseUpdateRequest,
    PaymentResponse,
    PeriodDetailResponse,
    PeriodResponse,
    PersonNameResponse,
    SettlementResponse,
)
from app.services import expenses as expense_service
from app.services import household as household_service
from app.services import users as user_service

router = APIRouter(prefix="/expenses", tags=["expenses"])


def _mentioned(period: SettlementPeriod) -> set[int]:
    """Everybody a frozen period names: payers, receivers and whoever paid an expense."""
    people = {expense.paid_by_id for expense in period.expenses}
    for payment in period.payments:
        people.update({payment.from_user_id, payment.to_user_id})
    return people


def _payments(rows: list[tuple[int, int, int]]) -> list[PaymentResponse]:
    return [
        PaymentResponse(from_user_id=debtor, to_user_id=creditor, amount_cents=amount)
        for debtor, creditor, amount in rows
    ]


def _names(db: DbSession, household: Household, people: set[int]) -> list[PersonNameResponse]:
    """Names for people the client cannot look up itself.

    Current members are left out on purpose: the client already has them from the
    household, and repeating them here would hand out more than the question asked for.
    """
    allowed = expense_service.nameable_people(db, household)
    members = {member.id for member in household_service.list_members(db, household)}
    return [
        PersonNameResponse(user_id=user_id, first_name=allowed[user_id])
        for user_id in sorted(people)
        if user_id in allowed and user_id not in members
    ]


@router.get("", response_model=ExpensePageResponse, summary="Expenses of the open period")
def list_expenses(
    current_user: MemberUser,
    db: DbSession,
    mine: bool = False,
    cursor: Annotated[str | None, Query(max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = expense_service.PAGE_SIZE,
) -> ExpensePageResponse:
    """``mine`` keeps only what concerns the requester: paid by them or shared by them."""
    household = household_service.get_household(db, current_user)
    rows, next_cursor = expense_service.list_expenses(
        db, household, current_user, mine=mine, cursor=cursor, limit=limit
    )
    return ExpensePageResponse(
        items=[ExpenseResponse.model_validate(expense) for expense in rows],
        next_cursor=next_cursor,
    )


@router.post(
    "",
    response_model=ExpenseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record an expense",
)
def create_expense(
    payload: ExpenseCreateRequest, current_user: MemberUser, db: DbSession
) -> ExpenseResponse:
    household = household_service.get_household(db, current_user)
    expense = expense_service.create_expense(
        db,
        household,
        current_user,
        title=payload.title,
        amount_cents=payload.amount_cents,
        paid_by_id=payload.paid_by_id,
        spent_at=payload.spent_at,
        participants=payload.participants,
        shares=payload.shares,
    )
    return ExpenseResponse.model_validate(expense)


@router.get("/balances", response_model=list[BalanceResponse], summary="Credit and debt")
def read_balances(current_user: MemberUser, db: DbSession) -> list[BalanceResponse]:
    """Calculated live from the open period — nothing is cached, nothing can drift."""
    household = household_service.get_household(db, current_user)
    return [BalanceResponse.model_validate(row) for row in expense_service.balances(db, household)]


@router.get("/settlement", response_model=SettlementResponse, summary="Who pays whom")
def read_settlement(current_user: MemberUser, db: DbSession) -> SettlementResponse:
    household = household_service.get_household(db, current_user)
    payments = expense_service.settlement(db, household)
    mentioned = {person for payment in payments for person in payment[:2]}
    return SettlementResponse(payments=_payments(payments), names=_names(db, household, mentioned))


@router.post(
    "/archive",
    response_model=PeriodDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Close the open period",
)
def archive_period(current_user: AdminUser, db: DbSession) -> PeriodDetailResponse:
    """Admins only, and irreversible: the archive is the settled past."""
    household = household_service.get_household(db, current_user)
    period = expense_service.archive_period(db, household, current_user)
    # Settling the period is what releases a deletion that was waiting for it.
    user_service.finish_pending_erasures(db)
    return PeriodDetailResponse.model_validate(
        expense_service.period_detail(period) | {"names": _names(db, household, _mentioned(period))}
    )


@router.get("/periods", response_model=list[PeriodResponse], summary="Archived periods")
def list_periods(current_user: MemberUser, db: DbSession) -> list[PeriodResponse]:
    household = household_service.get_household(db, current_user)
    return [
        PeriodResponse.model_validate(row) for row in expense_service.list_periods(db, household)
    ]


@router.get(
    "/periods/{period_id}",
    response_model=PeriodDetailResponse,
    summary="One archived period",
)
def read_period(period_id: int, current_user: MemberUser, db: DbSession) -> PeriodDetailResponse:
    household = household_service.get_household(db, current_user)
    period = expense_service.get_period(db, household, period_id)
    return PeriodDetailResponse.model_validate(
        expense_service.period_detail(period) | {"names": _names(db, household, _mentioned(period))}
    )


@router.patch("/{expense_id}", response_model=ExpenseResponse, summary="Change an expense")
def update_expense(
    expense_id: int,
    payload: ExpenseUpdateRequest,
    current_user: MemberUser,
    db: DbSession,
) -> ExpenseResponse:
    household = household_service.get_household(db, current_user)
    expense = expense_service.get_expense(db, household, expense_id)
    expense = expense_service.update_expense(
        db,
        household,
        expense,
        title=payload.title,
        amount_cents=payload.amount_cents,
        paid_by_id=payload.paid_by_id,
        spent_at=payload.spent_at,
        participants=payload.participants,
        shares=payload.shares,
    )
    return ExpenseResponse.model_validate(expense)


@router.delete(
    "/{expense_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an expense",
)
def delete_expense(expense_id: int, current_user: MemberUser, db: DbSession) -> None:
    household = household_service.get_household(db, current_user)
    expense_service.delete_expense(db, expense_service.get_expense(db, household, expense_id))
