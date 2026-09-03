"""Shared column types and the technical base every entity inherits."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Enum, TypeDecorator
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def string_enum(enum_type: type[StrEnum], name: str, length: int = 16) -> Enum:
    """Store an enum as its lowercase *value* in a portable ``VARCHAR`` column.

    Without ``values_callable`` SQLAlchemy persists the member *name* (``WG``), and
    ``native_enum=False`` keeps the column a plain string with a check constraint —
    the same shape on SQLite and PostgreSQL.
    """
    return Enum(
        enum_type,
        native_enum=False,
        length=length,
        name=name,
        values_callable=lambda enum: [member.value for member in enum],
    )


def utcnow() -> datetime:
    """Current time, always timezone aware — the only clock the models use."""
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator[datetime]):
    """Timezone-aware timestamps on every dialect.

    SQLite drops the offset when storing a value, so it would come back naive and
    comparisons against ``datetime.now(UTC)`` would fail. This decorator normalises to
    UTC on the way in and re-attaches UTC on the way out.

    Nothing in Kehrwoche depends on sub-second resolution: orderings that could tie use
    the id as a tie breaker. That keeps the behaviour the same whatever a dialect
    stores.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetimes are not accepted; use timezone-aware UTC values.")
        return value.astimezone(UTC)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Entity(Base):
    """Integer primary key plus UTC bookkeeping timestamps.

    ``updated_at`` is maintained on every flush — it is the anchor for the offline
    reconciliation planned for V3.
    """

    __abstract__ = True

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
