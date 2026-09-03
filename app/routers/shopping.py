"""The shopping list: entering, ticking off, tidying up and suggestions."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from app.deps import DbSession, MemberUser, SettingsDep
from app.schemas.shopping import (
    ClearBoughtResponse,
    ShoppingItemCreateRequest,
    ShoppingItemResponse,
    ShoppingItemUpdateRequest,
)
from app.services import household as household_service
from app.services import shopping as shopping_service

router = APIRouter(prefix="/shopping", tags=["shopping"])


@router.get("", response_model=list[ShoppingItemResponse], summary="The shopping list")
def list_items(current_user: MemberUser, db: DbSession) -> list[ShoppingItemResponse]:
    household = household_service.get_household(db, current_user)
    return [
        ShoppingItemResponse.model_validate(item)
        for item in shopping_service.list_items(db, household)
    ]


@router.get(
    "/suggestions",
    response_model=list[str],
    summary="Completions for the input field",
)
def suggestions(
    current_user: MemberUser,
    db: DbSession,
    settings: SettingsDep,
    q: Annotated[str, Query(max_length=120)] = "",
    locale: Annotated[str | None, Query(max_length=8)] = None,
) -> list[str]:
    """What the household buys itself comes before the generic word list.

    ``locale`` is the language the client currently displays; without it the profile
    language decides.
    """
    household = household_service.get_household(db, current_user)
    return shopping_service.suggestions(db, household, current_user, settings, q, locale)


@router.post(
    "",
    response_model=ShoppingItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Put something on the list",
)
def add_item(
    payload: ShoppingItemCreateRequest, current_user: MemberUser, db: DbSession
) -> ShoppingItemResponse:
    household = household_service.get_household(db, current_user)
    item = shopping_service.add_item(
        db,
        household,
        current_user,
        name=payload.name,
        note=payload.note,
        priority=payload.priority,
    )
    return ShoppingItemResponse.model_validate(item)


@router.post(
    "/clear-bought",
    response_model=ClearBoughtResponse,
    summary="Remove everything that was bought",
)
def clear_bought(current_user: MemberUser, db: DbSession) -> ClearBoughtResponse:
    """Writes a single feed entry for the whole shopping trip."""
    household = household_service.get_household(db, current_user)
    removed = shopping_service.clear_bought(db, household, current_user)
    return ClearBoughtResponse(removed=removed)


@router.patch(
    "/{item_id}",
    response_model=ShoppingItemResponse,
    summary="Change an item",
)
def update_item(
    item_id: int,
    payload: ShoppingItemUpdateRequest,
    current_user: MemberUser,
    db: DbSession,
) -> ShoppingItemResponse:
    household = household_service.get_household(db, current_user)
    item = shopping_service.get_item(db, household, item_id)
    item = shopping_service.update_item(
        db, item, name=payload.name, note=payload.note, priority=payload.priority
    )
    return ShoppingItemResponse.model_validate(item)


@router.post(
    "/{item_id}/toggle",
    response_model=ShoppingItemResponse,
    summary="Tick off or put back",
)
def toggle_item(item_id: int, current_user: MemberUser, db: DbSession) -> ShoppingItemResponse:
    household = household_service.get_household(db, current_user)
    item = shopping_service.get_item(db, household, item_id)
    return ShoppingItemResponse.model_validate(shopping_service.toggle_item(db, item, current_user))


@router.delete(
    "/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an item",
)
def delete_item(item_id: int, current_user: MemberUser, db: DbSession) -> None:
    household = household_service.get_household(db, current_user)
    shopping_service.delete_item(db, shopping_service.get_item(db, household, item_id))
