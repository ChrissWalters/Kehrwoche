"""Founding a household, reading it, changing it and the join code."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import ErrorCode
from app.main import API_PREFIX
from app.models import FeedEvent, FeedEventType, Household, User, UserRole
from app.models.household import JOIN_CODE_LENGTH
from app.services.household import JOIN_CODE_ALPHABET, find_by_join_code, generate_join_code
from tests.conftest import CsrfAwareClient
from tests.test_auth import CREDENTIALS, REGISTRATION

HOUSEHOLD = {"name": "Wohnung 3b", "type": "wg", "currency": "EUR"}


async def sign_up(client: AsyncClient, username: str = CREDENTIALS["username"]) -> None:
    await client.post(f"{API_PREFIX}/auth/register", json=REGISTRATION | {"username": username})
    await client.post(f"{API_PREFIX}/auth/login", json=CREDENTIALS | {"username": username})


@pytest.fixture
async def founder(client: AsyncClient) -> AsyncClient:
    """A signed-in person who has founded a household."""
    await sign_up(client)
    await client.post(f"{API_PREFIX}/household", json=HOUSEHOLD)
    return client


async def test_founding_makes_the_creator_an_admin(
    client: AsyncClient, db_session: Session
) -> None:
    await sign_up(client)

    response = await client.post(f"{API_PREFIX}/household", json=HOUSEHOLD)

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Wohnung 3b"
    assert body["type"] == "wg"
    assert body["currency"] == "EUR"
    assert [member["role"] for member in body["members"]] == ["admin"]

    user = db_session.scalar(select(User))
    assert user is not None
    assert user.household_id == body["id"]
    assert user.role is UserRole.ADMIN


async def test_founding_emits_a_member_joined_event(
    founder: AsyncClient, db_session: Session
) -> None:
    event = db_session.scalar(select(FeedEvent))

    assert event is not None
    assert event.type == FeedEventType.MEMBER_JOINED
    assert event.reference_type == "user"


async def test_join_code_uses_an_unmistakable_alphabet(founder: AsyncClient) -> None:
    code = (await founder.get(f"{API_PREFIX}/household")).json()["join_code"]

    assert len(code) == JOIN_CODE_LENGTH
    assert set(code) <= set(JOIN_CODE_ALPHABET)
    assert not (set(code) & set("0O1IL"))


async def test_second_household_is_a_conflict(founder: AsyncClient) -> None:
    response = await founder.post(f"{API_PREFIX}/household", json=HOUSEHOLD | {"name": "Zweit"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.ALREADY_IN_HOUSEHOLD


async def test_reading_shows_members_with_role_points_and_avatar(
    founder: AsyncClient, db_session: Session
) -> None:
    household = db_session.scalar(select(Household))
    assert household is not None
    db_session.add(
        User(
            username="bea@example.org",
            password_hash="x",
            first_name="Bea",
            household_id=household.id,
            points=7,
        )
    )
    db_session.commit()

    body = (await founder.get(f"{API_PREFIX}/household")).json()

    assert [member["first_name"] for member in body["members"]] == ["Alex", "Bea"]
    assert body["members"][1] == {
        "id": body["members"][1]["id"],
        "username": "bea@example.org",
        "first_name": "Bea",
        "last_name": None,
        "avatar_file": None,
        "role": "member",
        "points": 7,
    }


async def test_without_a_household_reading_is_not_found(client: AsyncClient) -> None:
    await sign_up(client)

    response = await client.get(f"{API_PREFIX}/household")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == ErrorCode.NOT_FOUND


async def test_household_needs_a_signed_in_person(client: AsyncClient) -> None:
    assert (await client.get(f"{API_PREFIX}/household")).status_code == 401
    assert (await client.post(f"{API_PREFIX}/household", json=HOUSEHOLD)).status_code == 401


async def test_admin_can_change_name_type_and_currency(founder: AsyncClient) -> None:
    response = await founder.patch(
        f"{API_PREFIX}/household",
        json={"name": "Familie Berg", "type": "family", "currency": "chf"},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Familie Berg"
    assert response.json()["type"] == "family"
    # Currencies are stored the way ISO 4217 writes them.
    assert response.json()["currency"] == "CHF"


async def test_partial_update_leaves_the_rest_alone(founder: AsyncClient) -> None:
    response = await founder.patch(f"{API_PREFIX}/household", json={"name": "Nur der Name"})

    assert response.status_code == 200
    assert response.json()["name"] == "Nur der Name"
    assert response.json()["type"] == "wg"
    assert response.json()["currency"] == "EUR"


async def test_invalid_currency_is_rejected(founder: AsyncClient) -> None:
    response = await founder.patch(f"{API_PREFIX}/household", json={"currency": "Euro"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR
    assert response.json()["error"]["field"] == "currency"


async def test_member_cannot_change_the_household(
    founder: AsyncClient, app: FastAPI, db_session: Session
) -> None:
    household = db_session.scalar(select(Household))
    assert household is not None

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with CsrfAwareClient(transport=transport, base_url="https://testserver") as second:
        await second.get(f"{API_PREFIX}/meta")
        await sign_up(second, "bea@example.org")
        # AP10 adds the join endpoint; until then the membership is set directly.
        member = db_session.scalar(select(User).where(User.username == "bea@example.org"))
        assert member is not None
        member.household_id = household.id
        member.role = UserRole.MEMBER
        db_session.commit()

        readable = await second.get(f"{API_PREFIX}/household")
        patched = await second.patch(f"{API_PREFIX}/household", json={"name": "Geklaut"})
        regenerated = await second.post(f"{API_PREFIX}/household/regenerate-code")

    # A member sees everything but may not change anything.
    assert readable.status_code == 200
    assert patched.status_code == 403
    assert patched.json()["error"]["code"] == ErrorCode.FORBIDDEN
    assert regenerated.status_code == 403


async def test_regenerating_replaces_the_old_code(
    founder: AsyncClient, db_session: Session
) -> None:
    old_code = (await founder.get(f"{API_PREFIX}/household")).json()["join_code"]

    response = await founder.post(f"{API_PREFIX}/household/regenerate-code")

    assert response.status_code == 200
    new_code = response.json()["join_code"]
    assert new_code != old_code
    assert (await founder.get(f"{API_PREFIX}/household")).json()["join_code"] == new_code
    # The old code no longer leads anywhere — AP10 turns this into a 404 on join.
    assert find_by_join_code(db_session, old_code) is None
    assert find_by_join_code(db_session, new_code) is not None


async def test_join_codes_are_unique_across_households(
    client: AsyncClient, db_session: Session
) -> None:
    codes = set()
    for index in range(5):
        household = Household(name=f"Haus {index}", join_code=f"CODE{index:08d}")
        db_session.add(household)
    db_session.commit()

    for _ in range(20):
        codes.add(generate_join_code(db_session))

    assert len(codes) == 20
