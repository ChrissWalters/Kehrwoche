"""The shared shopping list: entering, ticking off, tidying up and suggestions."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.errors import AppError, ErrorCode
from app.i18n import FALLBACK_LOCALE, load_catalogue
from app.models import FeedEventType, Household, ShoppingItem, User
from app.models.base import utcnow
from app.services.feed import emit_event

#: Key of the suggestion list inside a language catalogue.
SUGGESTIONS_KEY = "shopping_suggestions"
#: How many suggestions a phone keyboard can show without hiding the list.
MAX_SUGGESTIONS = 8
#: How far back the household's own history is considered.
HISTORY_DEPTH = 200


def clean_name(name: str) -> str:
    """A name has to survive trimming — "   " is not an item."""
    cleaned = name.strip()
    if not cleaned:
        raise AppError(
            400,
            ErrorCode.VALIDATION_ERROR,
            "The item needs a name.",
            "name",
            message_key="error.item.name_required",
        )
    return cleaned


def get_item(db: DbSession, household: Household, item_id: int) -> ShoppingItem:
    """An item of this household; anything else does not exist for the caller."""
    item = db.get(ShoppingItem, item_id)
    if item is None or item.household_id != household.id:
        raise AppError(
            404,
            ErrorCode.NOT_FOUND,
            "Item not found.",
            message_key="error.item.not_found",
        )
    return item


def list_items(db: DbSession, household: Household) -> list[ShoppingItem]:
    """Open items first (important ones on top), everything bought at the end."""
    items = db.scalars(select(ShoppingItem).where(ShoppingItem.household_id == household.id)).all()
    return sorted(
        items,
        key=lambda item: (item.bought, not item.priority, item.id),
    )


def add_item(
    db: DbSession,
    household: Household,
    actor: User,
    *,
    name: str,
    note: str | None = None,
    priority: bool = False,
) -> ShoppingItem:
    item = ShoppingItem(
        household_id=household.id,
        name=clean_name(name),
        note=(note or "").strip() or None,
        priority=priority,
        inserter_id=actor.id,
    )
    db.add(item)
    db.flush()
    emit_event(
        db,
        household,
        FeedEventType.SHOPPING_ADDED,
        actor=actor,
        reference_type="shopping_item",
        reference_id=item.id,
        body=item.name,
    )
    db.commit()
    db.refresh(item)
    return item


def update_item(
    db: DbSession,
    item: ShoppingItem,
    *,
    name: str | None = None,
    note: str | None = None,
    priority: bool | None = None,
) -> ShoppingItem:
    if name is not None:
        item.name = clean_name(name)
    if note is not None:
        item.note = note.strip() or None
    if priority is not None:
        item.priority = priority
    db.commit()
    db.refresh(item)
    return item


def toggle_item(db: DbSession, item: ShoppingItem, actor: User) -> ShoppingItem:
    """Tick off or put back — the same tap does both, so a mistap costs nothing."""
    if item.bought:
        item.bought = False
        item.buyer_id = None
        item.bought_at = None
    else:
        item.bought = True
        item.buyer_id = actor.id
        item.bought_at = utcnow()
    db.commit()
    db.refresh(item)
    return item


def delete_item(db: DbSession, item: ShoppingItem) -> None:
    db.delete(item)
    db.commit()


def clear_bought(db: DbSession, household: Household, actor: User) -> int:
    """Remove everything that was bought and report it as one line in the feed.

    One event per item would bury the board under a shopping trip; the household wants
    to read "Alex bought 8 items", not eight entries.
    """
    bought = list(
        db.scalars(
            select(ShoppingItem).where(
                ShoppingItem.household_id == household.id,
                ShoppingItem.bought.is_(True),
            )
        )
    )
    if not bought:
        raise AppError(
            409,
            ErrorCode.CONFLICT,
            "Nothing is ticked off.",
            message_key="error.shopping.nothing_bought",
        )

    for item in bought:
        db.delete(item)

    emit_event(
        db,
        household,
        FeedEventType.SHOPPING_BOUGHT_BULK,
        actor=actor,
        reference_type="shopping_item",
        body=str(len(bought)),
    )
    db.commit()
    return len(bought)


def _catalogue_suggestions(user: User, settings: Settings, locale: str | None) -> list[str]:
    catalogue = load_catalogue(locale or user.locale, settings) or {}
    entries = catalogue.get(SUGGESTIONS_KEY)
    if not isinstance(entries, list) or not entries:
        fallback = load_catalogue(FALLBACK_LOCALE, settings) or {}
        entries = fallback.get(SUGGESTIONS_KEY, [])
    return [entry for entry in entries if isinstance(entry, str) and entry.strip()]


def suggestions(
    db: DbSession,
    household: Household,
    user: User,
    settings: Settings,
    query: str,
    locale: str | None = None,
) -> list[str]:
    """Names starting with ``query``.

    What the household buys itself comes first: "Hafermilch" from last week beats a
    generic "Haferflocken" from the word list. Matching is case insensitive and by
    prefix — on a phone people type two or three letters, not a substring.

    ``locale`` is the language currently on screen; it wins over the profile, because
    suggestions in a language nobody is reading are of no use.
    """
    prefix = query.strip().lower()
    if not prefix:
        return []

    own_history = db.scalars(
        select(ShoppingItem.name)
        .where(ShoppingItem.household_id == household.id)
        .order_by(ShoppingItem.id.desc())
        .limit(HISTORY_DEPTH)
    ).all()

    found: list[str] = []
    seen: set[str] = set()
    for name in [*own_history, *_catalogue_suggestions(user, settings, locale)]:
        key = name.strip().lower()
        if key in seen or not key.startswith(prefix):
            continue
        seen.add(key)
        found.append(name.strip())
        if len(found) == MAX_SUGGESTIONS:
            break
    return found
