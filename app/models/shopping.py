"""The shared shopping list."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Entity, UtcDateTime

if TYPE_CHECKING:
    from app.models.household import Household
    from app.models.user import User


class ShoppingItem(Entity):
    __tablename__ = "shopping_items"

    household_id: Mapped[int] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), default=None)
    #: "Important" — sorts the item to the top of the open section.
    priority: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    bought: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    inserter_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    buyer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    bought_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)

    household: Mapped[Household] = relationship(back_populates="shopping_items")
    inserter: Mapped[User] = relationship(foreign_keys=[inserter_id])
    buyer: Mapped[User | None] = relationship(foreign_keys=[buyer_id])
