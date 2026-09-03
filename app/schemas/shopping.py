"""Request and response models of the shopping list."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

NAME_MAX_LENGTH = 120
NOTE_MAX_LENGTH = 255


class ShoppingItemCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    #: Quantity or brand — "2 Liter", "die günstige".
    note: str | None = Field(default=None, max_length=NOTE_MAX_LENGTH)
    priority: bool = False


class ShoppingItemUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX_LENGTH)
    note: str | None = Field(default=None, max_length=NOTE_MAX_LENGTH)
    priority: bool | None = None


class ShoppingItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    note: str | None
    priority: bool
    bought: bool
    inserter_id: int
    buyer_id: int | None
    bought_at: datetime | None


class ClearBoughtResponse(BaseModel):
    """How many items the tidy-up removed — the client shows it in the snackbar."""

    removed: int
