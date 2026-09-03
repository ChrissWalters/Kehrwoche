"""Request and response models of the household kitty.

Every amount is an integer number of cents. The API never accepts or returns a float —
rounding a shared bill is a decision, not an accident of binary arithmetic.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

TITLE_MAX_LENGTH = 120
#: Upper bound of a single expense: one million units of the household currency. Far
#: beyond any shared shopping trip, but it keeps a slipped decimal point out of the books.
MAX_AMOUNT_CENTS = 100_000_000


class ExpenseShareRequest(BaseModel):
    """One line of a manual split."""

    user_id: int
    share_cents: int = Field(ge=0, le=MAX_AMOUNT_CENTS)


class ExpenseCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX_LENGTH)
    amount_cents: int = Field(gt=0, le=MAX_AMOUNT_CENTS)
    #: Who paid; defaults to the person entering the expense.
    paid_by_id: int | None = None
    #: Date of the expense; defaults to today.
    spent_at: date | None = None
    #: Even split over these members. Omitted means the whole household.
    participants: list[int] | None = None
    #: Manual split. Mutually exclusive with ``participants``; the shares have to add up
    #: to ``amount_cents``.
    shares: list[ExpenseShareRequest] | None = None


class ExpenseUpdateRequest(BaseModel):
    """Everything optional — the client sends only what changed.

    A new ``amount_cents`` has to come with a split (``participants`` or ``shares``),
    because the server must not guess how a manually agreed division should follow the
    new total.
    """

    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX_LENGTH)
    amount_cents: int | None = Field(default=None, gt=0, le=MAX_AMOUNT_CENTS)
    paid_by_id: int | None = None
    spent_at: date | None = None
    participants: list[int] | None = None
    shares: list[ExpenseShareRequest] | None = None


class ExpenseShareResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    share_cents: int


class ExpenseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    amount_cents: int
    paid_by_id: int
    spent_at: date
    #: ``null`` while the expense belongs to the open period; set once it is archived.
    period_id: int | None
    created_at: datetime
    shares: list[ExpenseShareResponse]


class ExpensePageResponse(BaseModel):
    items: list[ExpenseResponse]
    #: Pass back as `cursor` to fetch the next page; null means the end.
    next_cursor: str | None


class BalanceResponse(BaseModel):
    """What one person paid, what they carry, and the difference.

    Positive means credit, negative means debt. Over a household the balances always
    add up to zero.
    """

    user_id: int
    first_name: str
    paid_cents: int
    owed_cents: int
    balance_cents: int


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    from_user_id: int
    to_user_id: int
    amount_cents: int


class PersonNameResponse(BaseModel):
    """A member id and the name the kitty is allowed to show for it."""

    user_id: int
    first_name: str


class SettlementResponse(BaseModel):
    """Who should pay whom, so that everybody ends up at zero."""

    payments: list[PaymentResponse]
    #: Names of people who are no longer members but still carry an open balance. The
    #: client resolves current members itself; these it could not know.
    names: list[PersonNameResponse] = []


class PeriodResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    closed_at: datetime
    #: Null when the account of the person who archived it has been deleted since.
    closed_by_id: int | None
    expense_count: int
    total_cents: int


class PeriodDetailResponse(PeriodResponse):
    """An archived period with everything it froze — read only, for good."""

    expenses: list[ExpenseResponse]
    payments: list[PaymentResponse]
    names: list[PersonNameResponse] = []
