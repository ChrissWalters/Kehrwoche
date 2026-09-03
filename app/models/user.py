"""Accounts and their membership in a household."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Entity, UtcDateTime, string_enum

if TYPE_CHECKING:
    from app.models.household import Household
    from app.models.notification import Notification
    from app.models.session import Session


class UserRole(StrEnum):
    ADMIN = "admin"
    MEMBER = "member"


class User(Entity):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("points >= 0", name="points_non_negative"),)

    #: Login name, freely chosen and visible to the household — never an identifier
    #: that has to stay private.
    username: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    #: Optional and private: nothing is sent anywhere, it prepares the password
    #: reset by mail planned for a future version.
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, default=None)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name: Mapped[str] = mapped_column(String(80), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(80), default=None)
    avatar_file: Mapped[str | None] = mapped_column(String(128), default=None)
    locale: Mapped[str] = mapped_column(String(8), default="en", nullable=False)

    #: ``NULL`` is the "without household" state after registration or moving out.
    household_id: Mapped[int | None] = mapped_column(
        ForeignKey("households.id", ondelete="SET NULL"), default=None, index=True
    )
    role: Mapped[UserRole] = mapped_column(
        string_enum(UserRole, "user_role"),
        default=UserRole.MEMBER,
        nullable=False,
    )
    points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Set when the account was given up while money was still open. Everything personal
    #: is already gone at that point; only name and login name wait for the settlement,
    #: because an open claim has to stay attributable to somebody.
    erasure_requested_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)

    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    household: Mapped[Household | None] = relationship(back_populates="members")
    sessions: Mapped[list[Session]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    notifications: Mapped[list[Notification]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def is_admin(self) -> bool:
        return self.role is UserRole.ADMIN
