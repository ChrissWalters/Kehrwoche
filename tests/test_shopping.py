"""The shopping list: entering, ticking off, tidying up and suggestions."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import ErrorCode
from app.main import API_PREFIX
from app.models import FeedEvent, FeedEventType, Household, ShoppingItem, User
from app.models.base import utcnow
from app.services.shopping import MAX_SUGGESTIONS, suggestions
from app.services.sync import household_state
from tests.conftest import CsrfAwareClient
from tests.test_household import HOUSEHOLD, sign_up


@pytest.fixture
async def founder(client: AsyncClient) -> AsyncClient:
    await sign_up(client)
    await client.post(f"{API_PREFIX}/household", json=HOUSEHOLD)
    return client


@pytest.fixture
async def join_code(founder: AsyncClient) -> str:
    return (await founder.get(f"{API_PREFIX}/household")).json()["join_code"]


@pytest.fixture
async def second_client(app: FastAPI, founder: AsyncClient) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with CsrfAwareClient(transport=transport, base_url="https://testserver") as client:
        await client.get(f"{API_PREFIX}/meta")
        await sign_up(client, "bea")
        yield client


async def add(client: AsyncClient, name: str, **extra: object) -> dict:
    response = await client.post(f"{API_PREFIX}/shopping", json={"name": name, **extra})
    return response.json()


# --- Entering ---------------------------------------------------------------------


async def test_adding_an_item_records_who_wrote_it_down(
    founder: AsyncClient, db_session: Session
) -> None:
    response = await founder.post(
        f"{API_PREFIX}/shopping", json={"name": "  Milch  ", "note": " 1,5 % ", "priority": True}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Milch"
    assert body["note"] == "1,5 %"
    assert body["priority"] is True
    assert body["bought"] is False
    assert body["buyer_id"] is None

    user = db_session.scalar(select(User))
    assert user is not None
    assert body["inserter_id"] == user.id


async def test_adding_emits_a_feed_event(founder: AsyncClient, db_session: Session) -> None:
    await add(founder, "Brot")

    event = db_session.scalars(select(FeedEvent).order_by(FeedEvent.id.desc())).first()
    assert event is not None
    assert event.type == FeedEventType.SHOPPING_ADDED
    assert event.body == "Brot"


async def test_the_list_puts_important_items_on_top_and_bought_ones_last(
    founder: AsyncClient,
) -> None:
    normal = await add(founder, "Brot")
    important = await add(founder, "Klopapier", priority=True)
    done = await add(founder, "Milch")
    await founder.post(f"{API_PREFIX}/shopping/{done['id']}/toggle")

    names = [item["name"] for item in (await founder.get(f"{API_PREFIX}/shopping")).json()]

    assert names == ["Klopapier", "Brot", "Milch"]
    assert normal["id"] != important["id"]


async def test_an_item_can_be_changed(founder: AsyncClient) -> None:
    item = await add(founder, "Milch")

    response = await founder.patch(
        f"{API_PREFIX}/shopping/{item['id']}", json={"name": "Hafermilch", "priority": True}
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Hafermilch"
    assert response.json()["priority"] is True


async def test_an_item_can_be_removed(founder: AsyncClient) -> None:
    item = await add(founder, "Milch")

    response = await founder.delete(f"{API_PREFIX}/shopping/{item['id']}")

    assert response.status_code == 204
    assert (await founder.get(f"{API_PREFIX}/shopping")).json() == []


# --- Ticking off ------------------------------------------------------------------


async def test_toggling_switches_back_and_forth(founder: AsyncClient, db_session: Session) -> None:
    """The same tap ticks off and puts back, so a mistap costs nothing."""
    item = await add(founder, "Milch")
    user = db_session.scalar(select(User))
    assert user is not None

    ticked = (await founder.post(f"{API_PREFIX}/shopping/{item['id']}/toggle")).json()
    assert ticked["bought"] is True
    assert ticked["buyer_id"] == user.id
    assert ticked["bought_at"] is not None

    back = (await founder.post(f"{API_PREFIX}/shopping/{item['id']}/toggle")).json()
    assert back["bought"] is False
    assert back["buyer_id"] is None
    assert back["bought_at"] is None


async def test_anybody_in_the_household_may_tick_off(
    founder: AsyncClient, second_client: AsyncClient, join_code: str, db_session: Session
) -> None:
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    item = await add(founder, "Milch")

    ticked = (await second_client.post(f"{API_PREFIX}/shopping/{item['id']}/toggle")).json()

    bea = db_session.scalar(select(User).where(User.username == "bea"))
    assert bea is not None
    assert ticked["buyer_id"] == bea.id


# --- Tidying up -------------------------------------------------------------------


async def test_clearing_removes_bought_items_and_writes_one_event(
    founder: AsyncClient, db_session: Session
) -> None:
    for name in ("Milch", "Brot", "Käse"):
        item = await add(founder, name)
        await founder.post(f"{API_PREFIX}/shopping/{item['id']}/toggle")
    await add(founder, "Klopapier")

    response = await founder.post(f"{API_PREFIX}/shopping/clear-bought")

    assert response.status_code == 200
    assert response.json() == {"removed": 3}
    assert [item["name"] for item in (await founder.get(f"{API_PREFIX}/shopping")).json()] == [
        "Klopapier"
    ]

    bulk = db_session.scalars(
        select(FeedEvent).where(FeedEvent.type == FeedEventType.SHOPPING_BOUGHT_BULK)
    ).all()
    # One line for the whole trip, not one per item.
    assert len(bulk) == 1
    assert bulk[0].body == "3"


async def test_clearing_without_anything_ticked_off_is_a_conflict(founder: AsyncClient) -> None:
    await add(founder, "Milch")

    response = await founder.post(f"{API_PREFIX}/shopping/clear-bought")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.CONFLICT


# --- Suggestions ------------------------------------------------------------------


def test_own_history_comes_before_the_word_list(db_session: Session, tmp_path) -> None:
    """ "Hafermilch" from last week beats a generic entry from the language file."""
    household = Household(name="WG", join_code="AAAABBBBCCCC")
    db_session.add(household)
    db_session.flush()
    user = User(username="alex", password_hash="x", first_name="Alex", household_id=household.id)
    user.locale = "de"
    db_session.add(user)
    db_session.flush()
    db_session.add(ShoppingItem(household_id=household.id, name="Hafermilch", inserter_id=user.id))
    db_session.commit()

    found = suggestions(db_session, household, user, Settings(data_dir=tmp_path), "haf")

    assert found[0] == "Hafermilch"
    assert "Haferflocken" in found


def test_suggestions_are_deduplicated_and_capped(db_session: Session, tmp_path) -> None:
    household = Household(name="WG", join_code="DDDDEEEEFFFF")
    db_session.add(household)
    db_session.flush()
    user = User(username="alex", password_hash="x", first_name="Alex", household_id=household.id)
    user.locale = "de"
    db_session.add(user)
    db_session.flush()
    # The same name written twice, plus enough entries to exceed the cap.
    for name in ["Milch", "milch", *[f"Ma{index}" for index in range(12)]]:
        db_session.add(ShoppingItem(household_id=household.id, name=name, inserter_id=user.id))
    db_session.commit()

    milk = suggestions(db_session, household, user, Settings(data_dir=tmp_path), "milch")
    many = suggestions(db_session, household, user, Settings(data_dir=tmp_path), "ma")

    assert milk == ["milch"], "the more recent spelling wins, and only once"
    assert len(many) == MAX_SUGGESTIONS


async def test_suggestions_follow_the_profile_language(
    founder: AsyncClient, db_session: Session
) -> None:
    user = db_session.scalar(select(User))
    assert user is not None

    german = (await founder.get(f"{API_PREFIX}/shopping/suggestions?q=zit")).json()
    user.locale = "en"
    db_session.commit()
    english = (await founder.get(f"{API_PREFIX}/shopping/suggestions?q=lem")).json()

    assert german == ["Zitronen"]
    assert english == ["Lemons"]


async def test_an_empty_query_suggests_nothing(founder: AsyncClient) -> None:
    assert (await founder.get(f"{API_PREFIX}/shopping/suggestions?q=")).json() == []
    assert (await founder.get(f"{API_PREFIX}/shopping/suggestions?q=%20")).json() == []


# --- Boundaries -------------------------------------------------------------------


async def test_items_of_another_household_are_not_found(
    founder: AsyncClient, db_session: Session
) -> None:
    other = Household(name="Andere WG", join_code="ZZZZZZZZZZZZ")
    db_session.add(other)
    db_session.flush()
    stranger = User(username="fremd", password_hash="x", first_name="Fremd", household_id=other.id)
    db_session.add(stranger)
    db_session.flush()
    foreign = ShoppingItem(household_id=other.id, name="Fremd", inserter_id=stranger.id)
    db_session.add(foreign)
    db_session.commit()

    assert (await founder.get(f"{API_PREFIX}/shopping")).json() == []
    assert (await founder.post(f"{API_PREFIX}/shopping/{foreign.id}/toggle")).status_code == 404
    assert (await founder.delete(f"{API_PREFIX}/shopping/{foreign.id}")).status_code == 404


async def test_the_shopping_list_needs_a_household(client: AsyncClient) -> None:
    await sign_up(client)

    assert (await client.get(f"{API_PREFIX}/shopping")).status_code == 404
    assert (await client.post(f"{API_PREFIX}/shopping", json={"name": "Milch"})).status_code == 404


async def test_the_shopping_list_needs_a_signed_in_person(client: AsyncClient) -> None:
    assert (await client.get(f"{API_PREFIX}/shopping")).status_code == 401
    assert (await client.post(f"{API_PREFIX}/shopping", json={"name": "Milch"})).status_code == 401


async def test_a_name_of_blanks_is_rejected(founder: AsyncClient) -> None:
    """Passes the length check, but an item without a name is useless on the list."""
    created = await founder.post(f"{API_PREFIX}/shopping", json={"name": "   "})
    item = await add(founder, "Milch")
    renamed = await founder.patch(f"{API_PREFIX}/shopping/{item['id']}", json={"name": " "})

    assert created.status_code == 400
    assert created.json()["error"]["field"] == "name"
    assert renamed.status_code == 400


# --- Change markers ----------------------------------------------------------------


async def test_the_state_marker_changes_when_the_list_changes(founder: AsyncClient) -> None:
    """Views poll this instead of the data — so it has to move on every change."""
    before = (await founder.get(f"{API_PREFIX}/household/state")).json()

    item = await add(founder, "Milch")
    after_add = (await founder.get(f"{API_PREFIX}/household/state")).json()

    await add(founder, "Brot")
    after_second = (await founder.get(f"{API_PREFIX}/household/state")).json()

    assert before["shopping"] != after_add["shopping"]
    assert after_add["shopping"] != after_second["shopping"]
    # Other modules stay untouched, so their views do not refetch for nothing.
    assert before["expenses"] == after_second["expenses"]
    assert before["feed"] != after_add["feed"], "adding writes a feed event"
    assert item["id"] > 0


def test_the_marker_reacts_to_a_change_that_keeps_the_count(db_session: Session) -> None:
    """Ticking an item off changes nothing but its timestamp — the marker must notice.

    The clock is moved by hand here rather than trusted: at second resolution, add
    and tick would otherwise fall into the same second.
    """
    household = Household(name="WG", join_code="GGGGHHHHJJJJ")
    db_session.add(household)
    db_session.flush()
    user = User(username="alex", password_hash="x", first_name="Alex", household_id=household.id)
    db_session.add(user)
    db_session.flush()
    item = ShoppingItem(household_id=household.id, name="Milch", inserter_id=user.id)
    db_session.add(item)
    db_session.commit()

    before = household_state(db_session, household, user)
    item.bought = True
    item.updated_at = utcnow() + timedelta(minutes=1)
    db_session.commit()
    after = household_state(db_session, household, user)

    assert before["shopping"] != after["shopping"]


async def test_the_state_marker_is_stable_without_changes(founder: AsyncClient) -> None:
    await add(founder, "Milch")

    first = (await founder.get(f"{API_PREFIX}/household/state")).json()
    second = (await founder.get(f"{API_PREFIX}/household/state")).json()

    assert first == second


async def test_the_state_covers_chores_and_notifications(founder: AsyncClient) -> None:
    before = (await founder.get(f"{API_PREFIX}/household/state")).json()

    chore = (
        await founder.post(
            f"{API_PREFIX}/chores", json={"title": "Bad", "points": 1, "rotation_seconds": 604800}
        )
    ).json()
    after_create = (await founder.get(f"{API_PREFIX}/household/state")).json()

    await founder.post(f"{API_PREFIX}/chores/{chore['id']}/complete")
    after_complete = (await founder.get(f"{API_PREFIX}/household/state")).json()

    assert before["chores"] != after_create["chores"]
    assert after_create["chores"] != after_complete["chores"]
    # Nothing is delivered yet, but the field is part of the contract from now on.
    assert after_complete["notifications"] == 0


async def test_the_state_stays_inside_the_household(
    founder: AsyncClient, second_client: AsyncClient, db_session: Session
) -> None:
    await add(founder, "Milch")
    await second_client.post(f"{API_PREFIX}/household", json=HOUSEHOLD | {"name": "Andere WG"})

    ours = (await founder.get(f"{API_PREFIX}/household/state")).json()
    theirs = (await second_client.get(f"{API_PREFIX}/household/state")).json()

    assert ours["shopping"] != theirs["shopping"]
    assert theirs["shopping"].startswith("0.")


async def test_the_state_needs_a_household(client: AsyncClient) -> None:
    await sign_up(client)

    assert (await client.get(f"{API_PREFIX}/household/state")).status_code == 404


async def test_the_displayed_language_wins_over_the_profile(
    founder: AsyncClient, db_session: Session
) -> None:
    """Somebody reading the app in English gets English suggestions, profile aside."""
    user = db_session.scalar(select(User))
    assert user is not None
    user.locale = "de"
    db_session.commit()

    profile = (await founder.get(f"{API_PREFIX}/shopping/suggestions?q=zit")).json()
    displayed = (await founder.get(f"{API_PREFIX}/shopping/suggestions?q=lem&locale=en")).json()

    assert profile == ["Zitronen"]
    assert displayed == ["Lemons"]
