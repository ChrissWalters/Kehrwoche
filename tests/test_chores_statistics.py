"""Templates, history and statistics of the chore module."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import ErrorCode
from app.main import API_PREFIX
from app.models import ChoreCompletion, FeedEvent, FeedEventType, User
from app.models.base import utcnow
from app.services.chores import RECENT_DAYS
from tests.conftest import CsrfAwareClient
from tests.test_chores import CHORE, WEEK
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
        await sign_up(client, "bea@example.org")
        yield client


# --- Templates -------------------------------------------------------------------


async def test_templates_come_in_the_language_of_the_profile(
    founder: AsyncClient, db_session: Session
) -> None:
    user = db_session.scalar(select(User))
    assert user is not None
    user.locale = "de"
    db_session.commit()

    german = (await founder.get(f"{API_PREFIX}/chores/templates")).json()

    assert {"Bad putzen", "Blumen gießen"} <= {template["title"] for template in german}

    user.locale = "en"
    db_session.commit()
    english = (await founder.get(f"{API_PREFIX}/chores/templates")).json()

    assert {"Clean the bathroom", "Water the plants"} <= {t["title"] for t in english}


async def test_templates_carry_a_rhythm_and_a_value(founder: AsyncClient) -> None:
    """The test account has a German profile, so the German list arrives."""
    templates = (await founder.get(f"{API_PREFIX}/chores/templates")).json()

    by_title = {template["title"]: template for template in templates}
    bathroom = by_title["Bad putzen"]
    assert bathroom["rotation_seconds"] == WEEK
    assert bathroom["points"] > 0
    assert bathroom["fixed"] is True


async def test_unknown_profile_language_falls_back_to_english(
    founder: AsyncClient, db_session: Session
) -> None:
    user = db_session.scalar(select(User))
    assert user is not None
    user.locale = "xx"
    db_session.commit()

    templates = (await founder.get(f"{API_PREFIX}/chores/templates")).json()

    assert any(template["title"] == "Clean the bathroom" for template in templates)


async def test_a_template_can_be_turned_into_a_chore(founder: AsyncClient) -> None:
    template = (await founder.get(f"{API_PREFIX}/chores/templates")).json()[0]

    response = await founder.post(f"{API_PREFIX}/chores", json=template)

    assert response.status_code == 201
    assert response.json()["title"] == template["title"]
    assert response.json()["rotation_seconds"] == template["rotation_seconds"]


# --- History ---------------------------------------------------------------------


async def test_history_lists_completions_newest_first(founder: AsyncClient) -> None:
    first = (await founder.post(f"{API_PREFIX}/chores", json=CHORE | {"title": "Bad"})).json()
    second = (await founder.post(f"{API_PREFIX}/chores", json=CHORE | {"title": "Müll"})).json()
    await founder.post(f"{API_PREFIX}/chores/{first['id']}/complete")
    await founder.post(f"{API_PREFIX}/chores/{second['id']}/complete")

    page = (await founder.get(f"{API_PREFIX}/chores/history")).json()

    assert [entry["chore_title"] for entry in page["items"]] == ["Müll", "Bad"]
    assert page["items"][0]["points_awarded"] == 2
    assert page["next_cursor"] is None


async def test_history_pages_through_with_the_cursor(founder: AsyncClient) -> None:
    chore = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()
    for _ in range(25):
        await founder.post(f"{API_PREFIX}/chores/{chore['id']}/complete")

    first = (await founder.get(f"{API_PREFIX}/chores/history?limit=10")).json()
    second = (
        await founder.get(f"{API_PREFIX}/chores/history?limit=10&cursor={first['next_cursor']}")
    ).json()
    third = (
        await founder.get(f"{API_PREFIX}/chores/history?limit=10&cursor={second['next_cursor']}")
    ).json()

    assert len(first["items"]) == 10
    assert len(second["items"]) == 10
    assert len(third["items"]) == 5
    assert third["next_cursor"] is None
    ids = [entry["id"] for page in (first, second, third) for entry in page["items"]]
    assert len(set(ids)) == 25, "no entry may be skipped or repeated"


async def test_history_stays_inside_the_household(
    founder: AsyncClient, second_client: AsyncClient, join_code: str, db_session: Session
) -> None:
    chore = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()
    await founder.post(f"{API_PREFIX}/chores/{chore['id']}/complete")

    # A second household with a completion of its own must not show up.
    await second_client.post(f"{API_PREFIX}/household", json=HOUSEHOLD | {"name": "Andere WG"})
    other_chore = (await second_client.post(f"{API_PREFIX}/chores", json=CHORE)).json()
    await second_client.post(f"{API_PREFIX}/chores/{other_chore['id']}/complete")

    page = (await founder.get(f"{API_PREFIX}/chores/history")).json()

    assert len(page["items"]) == 1
    assert db_session.scalars(select(ChoreCompletion)).all() != []


async def test_undoing_removes_the_entry_from_the_history(founder: AsyncClient) -> None:
    chore = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()
    await founder.post(f"{API_PREFIX}/chores/{chore['id']}/complete")

    await founder.post(f"{API_PREFIX}/chores/{chore['id']}/undo-complete")

    assert (await founder.get(f"{API_PREFIX}/chores/history")).json()["items"] == []


# --- Statistics ------------------------------------------------------------------


async def test_statistics_match_the_history(
    founder: AsyncClient, second_client: AsyncClient, join_code: str, db_session: Session
) -> None:
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    chore = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()
    await founder.post(f"{API_PREFIX}/chores/{chore['id']}/complete")
    await founder.post(f"{API_PREFIX}/chores/{chore['id']}/complete")
    await second_client.post(f"{API_PREFIX}/chores/{chore['id']}/complete")

    statistics = (await founder.get(f"{API_PREFIX}/chores/statistics")).json()
    history = (await founder.get(f"{API_PREFIX}/chores/history")).json()["items"]

    by_user = {row["user_id"]: row for row in statistics}
    for user_id, row in by_user.items():
        booked = [entry for entry in history if entry["user_id"] == user_id]
        assert row["completions"] == len(booked)
        assert row["points"] == sum(entry["points_awarded"] for entry in booked)
    # Leader board: whoever booked more comes first.
    assert statistics[0]["completions"] == 2


async def test_statistics_count_only_the_recent_window_separately(
    founder: AsyncClient, db_session: Session
) -> None:
    chore = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()
    await founder.post(f"{API_PREFIX}/chores/{chore['id']}/complete")
    await founder.post(f"{API_PREFIX}/chores/{chore['id']}/complete")
    old = db_session.scalars(select(ChoreCompletion).order_by(ChoreCompletion.id)).first()
    assert old is not None
    old.done_at = utcnow() - timedelta(days=RECENT_DAYS + 1)
    db_session.commit()

    statistics = (await founder.get(f"{API_PREFIX}/chores/statistics")).json()

    assert statistics[0]["completions"] == 2
    assert statistics[0]["completions_recent"] == 1


async def test_members_without_completions_appear_with_zero(
    founder: AsyncClient, second_client: AsyncClient, join_code: str
) -> None:
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})

    statistics = (await founder.get(f"{API_PREFIX}/chores/statistics")).json()

    assert len(statistics) == 2
    assert all(row["completions"] == 0 and row["points"] == 0 for row in statistics)


async def test_admin_resets_the_scores_but_keeps_the_history(
    founder: AsyncClient, db_session: Session
) -> None:
    chore = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()
    await founder.post(f"{API_PREFIX}/chores/{chore['id']}/complete")

    response = await founder.post(f"{API_PREFIX}/chores/reset-statistics")

    assert response.status_code == 204
    statistics = (await founder.get(f"{API_PREFIX}/chores/statistics")).json()
    assert statistics[0]["points"] == 0
    # The completion itself is untouched.
    assert statistics[0]["completions"] == 1
    assert len((await founder.get(f"{API_PREFIX}/chores/history")).json()["items"]) == 1

    event = db_session.scalars(select(FeedEvent).order_by(FeedEvent.id.desc())).first()
    assert event is not None
    assert event.type == FeedEventType.CHORE_STATISTICS_RESET


async def test_members_cannot_reset_the_statistics(
    founder: AsyncClient, second_client: AsyncClient, join_code: str
) -> None:
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})

    response = await second_client.post(f"{API_PREFIX}/chores/reset-statistics")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.FORBIDDEN


async def test_statistics_and_history_need_a_household(client: AsyncClient) -> None:
    await sign_up(client)

    assert (await client.get(f"{API_PREFIX}/chores/statistics")).status_code == 404
    assert (await client.get(f"{API_PREFIX}/chores/history")).status_code == 404
    assert (await client.get(f"{API_PREFIX}/chores/templates")).status_code == 404


async def test_templates_follow_the_displayed_language(
    founder: AsyncClient, db_session: Session
) -> None:
    """The header switch changes the language without touching the profile."""
    user = db_session.scalar(select(User))
    assert user is not None
    user.locale = "de"
    db_session.commit()

    english = (await founder.get(f"{API_PREFIX}/chores/templates?locale=en")).json()

    assert any(template["title"] == "Clean the bathroom" for template in english)
