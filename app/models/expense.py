"""Shared expenses, their shares and the archived settlement periods.

Every amount is an integer number of cents — never a float.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Entity, UtcDateTime

if TYPE_CHECKING:
    from app.models.household import Household
    from app.models.user import User


class Expense(Entity):
    __tablename__ = "expenses"

    household_id: Mapped[int] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    paid_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    spent_at: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    #: ``NULL`` means the expense belongs to the open (running) period.
    period_id: Mapped[int | None] = mapped_column(
        ForeignKey("settlement_periods.id", ondelete="SET NULL"), default=None, index=True
    )

    household: Mapped[Household] = relationship(back_populates="expenses")
    paid_by: Mapped[User] = relationship()
    period: Mapped[SettlementPeriod | None] = relationship(back_populates="expenses")
    #: Ordered by member id so a response always lists the split the same way.
    shares: Mapped[list[ExpenseShare]] = relationship(
        back_populates="expense",
        cascade="all, delete-orphan",
        order_by="ExpenseShare.user_id",
    )


class ExpenseShare(Entity):
    """The part of an expense a single person carries."""

    __tablename__ = "expense_shares"
    __table_args__ = (UniqueConstraint("expense_id", "user_id"),)

    expense_id: Mapped[int] = mapped_column(
        ForeignKey("expenses.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    share_cents: Mapped[int] = mapped_column(Integer, nullable=False)

    expense: Mapped[Expense] = relationship(back_populates="shares")
    user: Mapped[User] = relationship()


class SettlementPeriod(Entity):
    """A closed accounting period; archived data is immutable."""

    __tablename__ = "settlement_periods"

    household_id: Mapped[int] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), index=True, nullable=False
    )
    closed_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    closed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

    household: Mapped[Household] = relationship(back_populates="settlement_periods")
    closed_by: Mapped[User | None] = relationship()
    expenses: Mapped[list[Expense]] = relationship(back_populates="period")
    payments: Mapped[list[SettlementPayment]] = relationship(
        back_populates="period", cascade="all, delete-orphan"
    )


class SettlementPayment(Entity):
    """Frozen result of the settlement calculation: who pays whom how much."""

    __tablename__ = "settlement_payments"

    period_id: Mapped[int] = mapped_column(
        ForeignKey("settlement_periods.id", ondelete="CASCADE"), index=True, nullable=False
    )
    from_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    to_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)

    period: Mapped[SettlementPeriod] = relationship(back_populates="payments")
    from_user: Mapped[User] = relationship(foreign_keys=[from_user_id])
    to_user: Mapped[User] = relationship(foreign_keys=[to_user_id])
