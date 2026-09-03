"""Chores: due dates, rotation, completion, undo and reminders."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import ErrorCode
from app.main import API_PREFIX
from app.models import Chore, ChoreCompletion, FeedEvent, FeedEventType, Household, User
from app.models.base import utcnow
from app.models.chore import ON_DEMAND
from app.services.chores import next_due_at, next_member, remove_member_from_rotations
from tests.conftest import CsrfAwareClient
from tests.test_household import HOUSEHOLD, sign_up

WEEK = 7 * 24 * 3600
CHORE = {"title": "Bad putzen", "points": 2, "rotation_seconds": WEEK}


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
    """A second household member on their own device."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with CsrfAwareClient(transport=transport, base_url="https://testserver") as client:
        await client.get(f"{API_PREFIX}/meta")
        await sign_up(client, "bea@example.org")
        yield client


# --- Pure logic ------------------------------------------------------------------


def test_fixed_chores_keep_their_grid() -> None:
    """A late completion must not shift the rhythm of a fixed chore."""
    due = datetime(2026, 8, 3, 8, 0, tzinfo=UTC)
    done_late = due + timedelta(days=2)

    assert next_due_at(
        fixed=True, rotation_seconds=WEEK, previous_due_at=due, done_at=done_late
    ) == due + timedelta(seconds=WEEK)


def test_flexible_chores_count_from_the_completion() -> None:
    due = datetime(2026, 8, 3, 8, 0, tzinfo=UTC)
    done_late = due + timedelta(days=2)

    assert next_due_at(
        fixed=False, rotation_seconds=WEEK, previous_due_at=due, done_at=done_late
    ) == done_late + timedelta(seconds=WEEK)


def test_on_demand_chores_have_no_due_date() -> None:
    assert (
        next_due_at(
            fixed=True,
            rotation_seconds=ON_DEMAND,
            previous_due_at=None,
            done_at=utcnow(),
        )
        is None
    )


def test_a_fixed_chore_without_a_previous_date_starts_now() -> None:
    done = datetime(2026, 8, 3, 8, 0, tzinfo=UTC)

    assert next_due_at(
        fixed=True, rotation_seconds=WEEK, previous_due_at=None, done_at=done
    ) == done + timedelta(seconds=WEEK)


@pytest.mark.parametrize(
    ("current", "expected"),
    [(1, 2), (2, 3), (3, 1), (None, 1), (99, 1)],
)
def test_rotation_wraps_around(current: int | None, expected: int) -> None:
    assert next_member([1, 2, 3], current) == expected


def test_rotation_of_an_empty_list_has_nobody() -> None:
    assert next_member([], 1) is None


# --- API -------------------------------------------------------------------------


async def test_creating_a_chore_starts_with_the_first_member(founder: AsyncClient) -> None:
    response = await founder.post(f"{API_PREFIX}/chores", json=CHORE)

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Bad putzen"
    assert body["points"] == 2
    assert len(body["member_order"]) == 1
    assert body["current_user_id"] == body["member_order"][0]
    assert body["due_at"] is not None


async def test_creating_emits_a_feed_event(founder: AsyncClient, db_session: Session) -> None:
    await founder.post(f"{API_PREFIX}/chores", json=CHORE)

    event = db_session.scalars(select(FeedEvent).order_by(FeedEvent.id.desc())).first()
    assert event is not None
    assert event.type == FeedEventType.CHORE_CREATED
    assert event.body == "Bad putzen"


async def test_on_demand_chores_have_no_due_date_through_the_api(founder: AsyncClient) -> None:
    response = await founder.post(
        f"{API_PREFIX}/chores", json=CHORE | {"rotation_seconds": ON_DEMAND}
    )

    assert response.status_code == 201
    assert response.json()["due_at"] is None


async def test_chores_are_listed_with_due_ones_first(founder: AsyncClient) -> None:
    await founder.post(
        f"{API_PREFIX}/chores", json=CHORE | {"title": "Später", "rotation_seconds": WEEK}
    )
    await founder.post(
        f"{API_PREFIX}/chores", json=CHORE | {"title": "Bei Bedarf", "rotation_seconds": ON_DEMAND}
    )
    await founder.post(
        f"{API_PREFIX}/chores",
        json=CHORE | {"title": "Bald", "rotation_seconds": 3600},
    )

    titles = [chore["title"] for chore in (await founder.get(f"{API_PREFIX}/chores")).json()]

    assert titles == ["Bald", "Später", "Bei Bedarf"]


async def test_foreign_member_cannot_be_put_into_the_rotation(
    founder: AsyncClient, db_session: Session
) -> None:
    stranger = User(username="fremd@example.org", password_hash="x", first_name="Fremd")
    db_session.add(stranger)
    db_session.commit()

    response = await founder.post(
        f"{API_PREFIX}/chores", json=CHORE | {"member_order": [stranger.id]}
    )

    assert response.status_code == 400
    assert response.json()["error"]["field"] == "member_order"


async def test_chore_of_another_household_is_not_found(
    founder: AsyncClient, db_session: Session
) -> None:
    other = Household(name="Andere WG", join_code="ZZZZZZZZZZZZ")
    db_session.add(other)
    db_session.flush()
    foreign = Chore(household_id=other.id, title="Fremd")
    db_session.add(foreign)
    db_session.commit()

    assert (await founder.get(f"{API_PREFIX}/chores")).json() == []
    assert (await founder.post(f"{API_PREFIX}/chores/{foreign.id}/complete")).status_code == 404
    assert (await founder.delete(f"{API_PREFIX}/chores/{foreign.id}")).status_code == 404


async def test_chores_need_a_household(client: AsyncClient) -> None:
    await sign_up(client)

    assert (await client.get(f"{API_PREFIX}/chores")).status_code == 404
    assert (await client.post(f"{API_PREFIX}/chores", json=CHORE)).status_code == 404


async def test_completing_awards_points_and_hands_over(
    founder: AsyncClient, second_client: AsyncClient, join_code: str, db_session: Session
) -> None:
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    members = (await founder.get(f"{API_PREFIX}/household")).json()["members"]
    first, second = members[0]["id"], members[1]["id"]
    chore = (
        await founder.post(f"{API_PREFIX}/chores", json=CHORE | {"member_order": [first, second]})
    ).json()

    response = await founder.post(f"{API_PREFIX}/chores/{chore['id']}/complete")

    assert response.status_code == 201
    assert response.json()["points_awarded"] == 2
    after = (await founder.get(f"{API_PREFIX}/chores")).json()[0]
    assert after["current_user_id"] == second
    assert after["last_done_at"] is not None

    db_session.expire_all()
    booker = db_session.get(User, first)
    assert booker is not None and booker.points == 2


async def test_anybody_may_complete_and_gets_the_points(
    founder: AsyncClient, second_client: AsyncClient, join_code: str, db_session: Session
) -> None:
    """The person on duty is not the only one allowed to do the work."""
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    members = (await founder.get(f"{API_PREFIX}/household")).json()["members"]
    first, second = members[0]["id"], members[1]["id"]
    chore = (
        await founder.post(f"{API_PREFIX}/chores", json=CHORE | {"member_order": [first, second]})
    ).json()

    response = await second_client.post(f"{API_PREFIX}/chores/{chore['id']}/complete")

    assert response.status_code == 201
    db_session.expire_all()
    helper = db_session.get(User, second)
    assert helper is not None and helper.points == 2


async def test_rotation_over_three_members_and_four_completions(
    founder: AsyncClient, db_session: Session
) -> None:
    household = db_session.scalar(select(Household))
    assert household is not None
    for name in ("Bea", "Cem"):
        db_session.add(
            User(
                username=f"{name.lower()}@example.org",
                password_hash="x",
                first_name=name,
                household_id=household.id,
            )
        )
    db_session.commit()
    order = [
        member["id"] for member in (await founder.get(f"{API_PREFIX}/household")).json()["members"]
    ]
    chore = (
        await founder.post(f"{API_PREFIX}/chores", json=CHORE | {"member_order": order})
    ).json()

    responsible = [chore["current_user_id"]]
    for _ in range(4):
        await founder.post(f"{API_PREFIX}/chores/{chore['id']}/complete")
        responsible.append((await founder.get(f"{API_PREFIX}/chores")).json()[0]["current_user_id"])

    assert responsible == [order[0], order[1], order[2], order[0], order[1]]


async def test_completing_a_fixed_chore_keeps_the_grid(
    founder: AsyncClient, db_session: Session
) -> None:
    # Whole seconds: a stored timestamp may carry no fractions, so a value with
    # microseconds would come back rounded and the comparison would be about storage
    # precision rather than about the rotation.
    due = (utcnow() - timedelta(days=2)).replace(microsecond=0)
    created = (await founder.post(f"{API_PREFIX}/chores", json=CHORE | {"fixed": True})).json()
    chore = db_session.get(Chore, created["id"])
    assert chore is not None
    chore.due_at = due
    db_session.commit()

    await founder.post(f"{API_PREFIX}/chores/{created['id']}/complete")

    db_session.expire_all()
    chore = db_session.get(Chore, created["id"])
    assert chore is not None
    assert chore.due_at == due + timedelta(seconds=WEEK)


async def test_undo_restores_points_responsibility_and_due_date(
    founder: AsyncClient, second_client: AsyncClient, join_code: str, db_session: Session
) -> None:
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    members = (await founder.get(f"{API_PREFIX}/household")).json()["members"]
    order = [members[0]["id"], members[1]["id"]]
    chore = (
        await founder.post(f"{API_PREFIX}/chores", json=CHORE | {"member_order": order})
    ).json()
    before = (await founder.get(f"{API_PREFIX}/chores")).json()[0]

    await founder.post(f"{API_PREFIX}/chores/{chore['id']}/complete")
    undone = await founder.post(f"{API_PREFIX}/chores/{chore['id']}/undo-complete")

    assert undone.status_code == 204
    after = (await founder.get(f"{API_PREFIX}/chores")).json()[0]
    assert after["current_user_id"] == before["current_user_id"]
    assert after["due_at"] == before["due_at"]
    assert after["last_done_at"] is None

    db_session.expire_all()
    booker = db_session.get(User, order[0])
    assert booker is not None and booker.points == 0
    assert db_session.scalars(select(ChoreCompletion)).all() == []
    # The feed entry of the completion is gone as well.
    types = [event.type for event in db_session.scalars(select(FeedEvent)).all()]
    assert FeedEventType.CHORE_DONE not in types


async def test_undo_after_the_window_is_refused(founder: AsyncClient, db_session: Session) -> None:
    chore = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()
    await founder.post(f"{API_PREFIX}/chores/{chore['id']}/complete")
    completion = db_session.scalar(select(ChoreCompletion))
    assert completion is not None
    completion.done_at = utcnow() - timedelta(minutes=6)
    db_session.commit()

    response = await founder.post(f"{API_PREFIX}/chores/{chore['id']}/undo-complete")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.UNDO_WINDOW_EXPIRED


async def test_only_the_booking_person_may_undo(
    founder: AsyncClient, second_client: AsyncClient, join_code: str
) -> None:
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    chore = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()
    await founder.post(f"{API_PREFIX}/chores/{chore['id']}/complete")

    response = await second_client.post(f"{API_PREFIX}/chores/{chore['id']}/undo-complete")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.FORBIDDEN


async def test_undo_without_a_completion_is_a_conflict(founder: AsyncClient) -> None:
    chore = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()

    response = await founder.post(f"{API_PREFIX}/chores/{chore['id']}/undo-complete")

    assert response.status_code == 409


async def test_reminding_twice_a_day_is_refused(
    founder: AsyncClient, second_client: AsyncClient, join_code: str
) -> None:
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    chore = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()

    first = await founder.post(f"{API_PREFIX}/chores/{chore['id']}/remind")
    second = await founder.post(f"{API_PREFIX}/chores/{chore['id']}/remind")

    assert first.status_code == 200
    assert first.json()["first_name"] == "Alex"
    assert second.status_code == 429
    assert second.json()["error"]["code"] == ErrorCode.RATE_LIMITED
    assert int(second.headers["Retry-After"]) > 0


async def test_the_reminder_limit_is_per_person_not_per_chore(
    founder: AsyncClient, second_client: AsyncClient, join_code: str
) -> None:
    """Whoever reminded first must not silence everybody else in the household."""
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    chore = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()

    await founder.post(f"{API_PREFIX}/chores/{chore['id']}/remind")
    from_somebody_else = await second_client.post(f"{API_PREFIX}/chores/{chore['id']}/remind")

    assert from_somebody_else.status_code == 200
    assert (
        await second_client.post(f"{API_PREFIX}/chores/{chore['id']}/remind")
    ).status_code == 429, "the second person is limited on their own attempts"


async def test_updating_a_chore(founder: AsyncClient) -> None:
    chore = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()

    response = await founder.patch(
        f"{API_PREFIX}/chores/{chore['id']}",
        json={"title": "Bad gründlich putzen", "points": 5, "fixed": True},
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Bad gründlich putzen"
    assert response.json()["points"] == 5
    assert response.json()["fixed"] is True


async def test_switching_to_on_demand_clears_the_due_date(founder: AsyncClient) -> None:
    chore = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()

    response = await founder.patch(
        f"{API_PREFIX}/chores/{chore['id']}", json={"rotation_seconds": ON_DEMAND}
    )

    assert response.status_code == 200
    assert response.json()["due_at"] is None


async def test_deleting_a_chore(founder: AsyncClient, db_session: Session) -> None:
    chore = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()

    response = await founder.delete(f"{API_PREFIX}/chores/{chore['id']}")

    assert response.status_code == 204
    assert (await founder.get(f"{API_PREFIX}/chores")).json() == []


# --- The hook from AP10 ----------------------------------------------------------


def test_leaving_removes_the_member_from_every_rotation(db_session: Session) -> None:
    household = Household(name="WG", join_code="AAAABBBBCCCC")
    db_session.add(household)
    db_session.flush()
    people = []
    for name in ("Alex", "Bea", "Cem"):
        user = User(
            username=f"{name.lower()}@example.org",
            password_hash="x",
            first_name=name,
            household_id=household.id,
        )
        db_session.add(user)
        people.append(user)
    db_session.flush()
    order = [person.id for person in people]
    on_duty = Chore(
        household_id=household.id,
        title="Bad",
        member_order=list(order),
        current_user_id=order[1],
    )
    not_on_duty = Chore(
        household_id=household.id,
        title="Müll",
        member_order=list(order),
        current_user_id=order[0],
    )
    db_session.add_all([on_duty, not_on_duty])
    db_session.commit()

    remove_member_from_rotations(db_session, household.id, order[1])
    db_session.commit()

    assert on_duty.member_order == [order[0], order[2]]
    # Whoever was on duty hands over to the next person in line.
    assert on_duty.current_user_id == order[2]
    assert not_on_duty.member_order == [order[0], order[2]]
    assert not_on_duty.current_user_id == order[0]


def test_the_last_person_in_a_rotation_leaves_nobody_on_duty(db_session: Session) -> None:
    household = Household(name="WG", join_code="DDDDEEEEFFFF")
    db_session.add(household)
    db_session.flush()
    only = User(
        username="alex@example.org",
        password_hash="x",
        first_name="Alex",
        household_id=household.id,
    )
    db_session.add(only)
    db_session.flush()
    chore = Chore(
        household_id=household.id,
        title="Bad",
        member_order=[only.id],
        current_user_id=only.id,
    )
    db_session.add(chore)
    db_session.commit()

    remove_member_from_rotations(db_session, household.id, only.id)
    db_session.commit()

    assert chore.member_order == []
    assert chore.current_user_id is None


async def test_leaving_the_household_updates_the_rotation(
    founder: AsyncClient, second_client: AsyncClient, join_code: str, db_session: Session
) -> None:
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    members = (await founder.get(f"{API_PREFIX}/household")).json()["members"]
    order = [members[0]["id"], members[1]["id"]]
    chore = (
        await founder.post(f"{API_PREFIX}/chores", json=CHORE | {"member_order": order})
    ).json()

    await second_client.post(f"{API_PREFIX}/household/leave")

    after = (await founder.get(f"{API_PREFIX}/chores")).json()[0]
    assert after["member_order"] == [order[0]]
    assert after["current_user_id"] == order[0]
    assert chore["member_order"] == order


# --- Booking on behalf of the person on duty ---------------------------------------


async def test_booking_for_the_person_on_duty_credits_them(
    founder: AsyncClient, second_client: AsyncClient, join_code: str, db_session: Session
) -> None:
    """Bea did the work but has no device at hand — Alex books it for her."""
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    members = (await founder.get(f"{API_PREFIX}/household")).json()["members"]
    alex, bea = members[0]["id"], members[1]["id"]
    chore = (
        await founder.post(f"{API_PREFIX}/chores", json=CHORE | {"member_order": [bea, alex]})
    ).json()

    response = await founder.post(
        f"{API_PREFIX}/chores/{chore['id']}/complete", json={"for_user_id": bea}
    )

    assert response.status_code == 201
    assert response.json()["user_id"] == bea
    assert response.json()["booked_by_id"] == alex

    db_session.expire_all()
    assert db_session.get(User, bea).points == 2
    assert db_session.get(User, alex).points == 0
    # The rotation moves on from whoever was on duty, exactly as with any completion.
    assert (await founder.get(f"{API_PREFIX}/chores")).json()[0]["current_user_id"] == alex


async def test_booking_for_somebody_outside_the_household_is_rejected(
    founder: AsyncClient, db_session: Session
) -> None:
    stranger = User(username="fremd@example.org", password_hash="x", first_name="Fremd")
    db_session.add(stranger)
    db_session.commit()
    chore = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()

    response = await founder.post(
        f"{API_PREFIX}/chores/{chore['id']}/complete", json={"for_user_id": stranger.id}
    )

    assert response.status_code == 400
    assert response.json()["error"]["field"] == "for_user_id"


async def test_booking_for_oneself_is_a_normal_completion(
    founder: AsyncClient, db_session: Session
) -> None:
    chore = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()
    me = db_session.scalar(select(User))
    assert me is not None

    response = await founder.post(
        f"{API_PREFIX}/chores/{chore['id']}/complete", json={"for_user_id": me.id}
    )

    assert response.status_code == 201
    assert response.json()["booked_by_id"] is None


async def test_only_the_booking_person_may_undo_a_booking_on_behalf(
    founder: AsyncClient, second_client: AsyncClient, join_code: str, db_session: Session
) -> None:
    """The window corrects a mistap; it is not a veto for the credited person."""
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    members = (await founder.get(f"{API_PREFIX}/household")).json()["members"]
    bea = members[1]["id"]
    chore = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()
    await founder.post(f"{API_PREFIX}/chores/{chore['id']}/complete", json={"for_user_id": bea})

    # Bea was credited but did not book it — she cannot take it back.
    refused = await second_client.post(f"{API_PREFIX}/chores/{chore['id']}/undo-complete")
    # The person who tapped can.
    accepted = await founder.post(f"{API_PREFIX}/chores/{chore['id']}/undo-complete")

    assert refused.status_code == 403
    assert accepted.status_code == 204
    db_session.expire_all()
    assert db_session.get(User, bea).points == 0


async def test_a_third_person_cannot_undo_a_booking(
    founder: AsyncClient, second_client: AsyncClient, join_code: str, app: FastAPI
) -> None:
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    chore = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()
    await founder.post(f"{API_PREFIX}/chores/{chore['id']}/complete")

    response = await second_client.post(f"{API_PREFIX}/chores/{chore['id']}/undo-complete")

    assert response.status_code == 403


async def test_the_feed_names_the_person_the_work_is_credited_to(
    founder: AsyncClient, second_client: AsyncClient, join_code: str, db_session: Session
) -> None:
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    members = (await founder.get(f"{API_PREFIX}/household")).json()["members"]
    bea = members[1]["id"]
    chore = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()

    await founder.post(f"{API_PREFIX}/chores/{chore['id']}/complete", json={"for_user_id": bea})

    event = db_session.scalars(
        select(FeedEvent).where(FeedEvent.type == FeedEventType.CHORE_DONE)
    ).first()
    assert event is not None
    assert event.actor_id == bea


async def test_the_form_can_hand_the_current_turn_to_somebody_else(
    founder: AsyncClient, second_client: AsyncClient, join_code: str
) -> None:
    """Reordering alone keeps the turn; the explicit field moves it."""
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    members = (await founder.get(f"{API_PREFIX}/household")).json()["members"]
    alex, bea = members[0]["id"], members[1]["id"]
    chore = (
        await founder.post(f"{API_PREFIX}/chores", json=CHORE | {"member_order": [alex, bea]})
    ).json()
    assert chore["current_user_id"] == alex

    reordered = await founder.patch(
        f"{API_PREFIX}/chores/{chore['id']}", json={"member_order": [bea, alex]}
    )
    assert reordered.json()["current_user_id"] == alex

    handed_over = await founder.patch(
        f"{API_PREFIX}/chores/{chore['id']}", json={"current_user_id": bea}
    )
    assert handed_over.json()["current_user_id"] == bea


async def test_the_turn_can_only_go_to_somebody_in_the_rotation(founder: AsyncClient) -> None:
    chore = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()

    response = await founder.patch(
        f"{API_PREFIX}/chores/{chore['id']}", json={"current_user_id": 9999}
    )

    assert response.status_code == 400
    assert response.json()["error"]["field"] == "current_user_id"


# --- Joining after a chore already exists ------------------------------------------


async def test_joining_adds_the_new_member_to_every_rotation(
    founder: AsyncClient, second_client: AsyncClient, join_code: str, db_session: Session
) -> None:
    """Somebody joining later must not be left out of the plan for good."""
    first = (await founder.post(f"{API_PREFIX}/chores", json=CHORE | {"title": "Bad"})).json()
    second = (await founder.post(f"{API_PREFIX}/chores", json=CHORE | {"title": "Müll"})).json()
    assert len(first["member_order"]) == 1

    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})

    members = (await founder.get(f"{API_PREFIX}/household")).json()["members"]
    alex, bea = members[0]["id"], members[1]["id"]
    chores = {chore["title"]: chore for chore in (await founder.get(f"{API_PREFIX}/chores")).json()}
    # Appended at the end: the running cycle finishes first.
    assert chores["Bad"]["member_order"] == [alex, bea]
    assert chores["Müll"]["member_order"] == [alex, bea]
    assert chores["Bad"]["current_user_id"] == alex
    assert second["id"] in {chore["id"] for chore in chores.values()}


async def test_the_new_member_actually_gets_a_turn(
    founder: AsyncClient, second_client: AsyncClient, join_code: str
) -> None:
    chore = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    members = (await founder.get(f"{API_PREFIX}/household")).json()["members"]

    await founder.post(f"{API_PREFIX}/chores/{chore['id']}/complete")

    after = (await founder.get(f"{API_PREFIX}/chores")).json()[0]
    assert after["current_user_id"] == members[1]["id"]


async def test_joining_an_orphaned_rotation_puts_somebody_on_duty(
    founder: AsyncClient, second_client: AsyncClient, join_code: str, db_session: Session
) -> None:
    """A rotation whose last member left gets its first person back on joining."""
    created = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()
    chore = db_session.get(Chore, created["id"])
    assert chore is not None
    chore.member_order = []
    chore.current_user_id = None
    db_session.commit()

    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})

    db_session.expire_all()
    chore = db_session.get(Chore, created["id"])
    assert chore is not None
    assert len(chore.member_order) == 1
    assert chore.current_user_id == chore.member_order[0]


def test_a_fixed_chore_skips_missed_rounds_instead_of_piling_them_up() -> None:
    """Nine days late on a weekly bin round: the next date is the Monday after, not the past one."""
    due = datetime(2026, 8, 3, 20, 0, tzinfo=UTC)  # Monday
    done = datetime(2026, 8, 12, 19, 0, tzinfo=UTC)  # Wednesday of the following week

    result = next_due_at(fixed=True, rotation_seconds=WEEK, previous_due_at=due, done_at=done)

    assert result == datetime(2026, 8, 17, 20, 0, tzinfo=UTC)
    assert result > done, "a completed chore must not stay overdue"


def test_a_fixed_chore_completed_exactly_on_time_moves_one_interval() -> None:
    due = datetime(2026, 8, 3, 20, 0, tzinfo=UTC)

    result = next_due_at(fixed=True, rotation_seconds=WEEK, previous_due_at=due, done_at=due)

    assert result == due + timedelta(seconds=WEEK)


def test_a_fixed_chore_completed_early_keeps_the_next_slot() -> None:
    due = datetime(2026, 8, 3, 20, 0, tzinfo=UTC)
    done = due - timedelta(days=2)

    result = next_due_at(fixed=True, rotation_seconds=WEEK, previous_due_at=due, done_at=done)

    assert result == due + timedelta(seconds=WEEK)


async def test_a_chore_can_be_given_a_first_due_date(founder: AsyncClient) -> None:
    """ "Every Saturday" is a weekly fixed chore whose first date falls on a Saturday."""
    saturday = datetime(2026, 8, 8, 20, 0, tzinfo=UTC)

    response = await founder.post(
        f"{API_PREFIX}/chores",
        json=CHORE | {"fixed": True, "due_at": saturday.isoformat()},
    )

    assert response.status_code == 201
    assert response.json()["due_at"].startswith("2026-08-08")


# --- Rotation completed server side -------------------------------------------------


async def test_a_chore_created_from_a_stale_member_list_still_covers_everybody(
    founder: AsyncClient, second_client: AsyncClient, join_code: str
) -> None:
    """The screen of the person creating the chore may be a minute out of date.

    Bea joins, Alex — whose tab has not reloaded since — creates a chore and sends the
    old list. Without the server completing it, Bea would never get a turn.
    """
    members_before = (await founder.get(f"{API_PREFIX}/household")).json()["members"]
    stale_order = [member["id"] for member in members_before]
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})

    chore = (
        await founder.post(f"{API_PREFIX}/chores", json=CHORE | {"member_order": stale_order})
    ).json()

    members_now = (await founder.get(f"{API_PREFIX}/household")).json()["members"]
    newcomer = [member["id"] for member in members_now if member["id"] not in stale_order]
    assert chore["member_order"] == [*stale_order, *newcomer], "appended at the end, as on joining"
    assert chore["current_user_id"] == stale_order[0], "the order that was sent still leads"


async def test_editing_with_a_stale_member_list_does_not_drop_anybody(
    founder: AsyncClient, second_client: AsyncClient, join_code: str
) -> None:
    chore = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()
    stale_order = list(chore["member_order"])
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})

    updated = (
        await founder.patch(
            f"{API_PREFIX}/chores/{chore['id']}", json={"member_order": stale_order}
        )
    ).json()

    assert len(updated["member_order"]) == 2
    assert updated["member_order"][0] == stale_order[0]


async def test_any_edit_repairs_a_rotation_that_is_missing_somebody(
    founder: AsyncClient, second_client: AsyncClient, join_code: str, db_session: Session
) -> None:
    """The chore from the stale window heals when somebody edits it — for any reason."""
    chore = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    broken = db_session.get(Chore, chore["id"])
    assert broken is not None
    broken.member_order = [broken.member_order[0]]
    db_session.commit()

    # Only the title is sent — the rotation is not mentioned at all.
    updated = (
        await founder.patch(f"{API_PREFIX}/chores/{chore['id']}", json={"title": "Bad putzen"})
    ).json()

    assert len(updated["member_order"]) == 2


# --- Taking over without moving the turn on (AP33) -----------------------------------


async def test_taking_over_hands_the_turn_on_by_default(
    founder: AsyncClient, second_client: AsyncClient, join_code: str
) -> None:
    """The behaviour every existing household keeps: whoever did it is next."""
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    members = (await founder.get(f"{API_PREFIX}/household")).json()["members"]
    alex, bea = members[0]["id"], members[1]["id"]
    chore = (
        await founder.post(f"{API_PREFIX}/chores", json=CHORE | {"member_order": [bea, alex]})
    ).json()

    # Alex does Bea's turn and takes the credit for it.
    await founder.post(f"{API_PREFIX}/chores/{chore['id']}/complete", json={})

    assert (await founder.get(f"{API_PREFIX}/chores")).json()[0]["current_user_id"] == alex


async def test_the_switch_leaves_the_turn_where_it_was(
    founder: AsyncClient, second_client: AsyncClient, join_code: str, db_session: Session
) -> None:
    """Two people, one chore: without this, doing Bea's turn puts Alex up twice in a row."""
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    await founder.patch(f"{API_PREFIX}/household", json={"takeover_keeps_turn": True})
    members = (await founder.get(f"{API_PREFIX}/household")).json()["members"]
    alex, bea = members[0]["id"], members[1]["id"]
    chore = (
        await founder.post(f"{API_PREFIX}/chores", json=CHORE | {"member_order": [bea, alex]})
    ).json()
    await founder.post(f"{API_PREFIX}/chores/{chore['id']}/complete", json={})

    after = (await founder.get(f"{API_PREFIX}/chores")).json()[0]
    assert after["current_user_id"] == bea, "Bea still owes the household a turn"
    # Everything else is untouched: the work counts, the points are Alex's, time moves on.
    db_session.expire_all()
    assert db_session.get(User, alex).points == 2
    # Deliberately not "the due date differs from the one it was created with": at
    # second resolution a chore created and completed inside the same second carries
    # the identical value, and the comparison would say nothing. What the rhythm
    # actually promises is this — the work is logged, and the chore comes round again.
    assert after["last_done_at"] is not None
    assert datetime.fromisoformat(after["due_at"]) > utcnow(), "the chore is due again later"


async def test_the_switch_does_not_touch_booking_for_the_person_on_duty(
    founder: AsyncClient, second_client: AsyncClient, join_code: str
) -> None:
    """Bea did the work herself; the turn moves on as it always would."""
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    await founder.patch(f"{API_PREFIX}/household", json={"takeover_keeps_turn": True})
    members = (await founder.get(f"{API_PREFIX}/household")).json()["members"]
    alex, bea = members[0]["id"], members[1]["id"]
    chore = (
        await founder.post(f"{API_PREFIX}/chores", json=CHORE | {"member_order": [bea, alex]})
    ).json()

    await founder.post(f"{API_PREFIX}/chores/{chore['id']}/complete", json={"for_user_id": bea})

    assert (await founder.get(f"{API_PREFIX}/chores")).json()[0]["current_user_id"] == alex


async def test_the_switch_survives_the_undo_window(
    founder: AsyncClient, second_client: AsyncClient, join_code: str
) -> None:
    """Taking a completion back restores exactly the state before it — turn included."""
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    await founder.patch(f"{API_PREFIX}/household", json={"takeover_keeps_turn": True})
    members = (await founder.get(f"{API_PREFIX}/household")).json()["members"]
    bea = members[1]["id"]
    chore = (
        await founder.post(
            f"{API_PREFIX}/chores", json=CHORE | {"member_order": [bea, members[0]["id"]]}
        )
    ).json()

    await founder.post(f"{API_PREFIX}/chores/{chore['id']}/complete", json={})
    await founder.post(f"{API_PREFIX}/chores/{chore['id']}/undo-complete")

    after = (await founder.get(f"{API_PREFIX}/chores")).json()[0]
    assert after["current_user_id"] == bea
    assert after["due_at"] == chore["due_at"]


# --- The pinboard records edits and deletions (AP33) ---------------------------------


async def test_editing_a_chore_names_the_fields_that_changed(founder: AsyncClient) -> None:
    chore = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()

    await founder.patch(
        f"{API_PREFIX}/chores/{chore['id']}", json={"title": "Küche putzen", "points": 5}
    )

    entry = (await founder.get(f"{API_PREFIX}/feed")).json()["items"][0]
    assert entry["type"] == "chore_updated"
    assert entry["body"] == "Küche putzen"
    assert entry["params"]["fields"] == ["title", "points"]


async def test_an_edit_that_changes_nothing_is_not_an_event(founder: AsyncClient) -> None:
    """Opening the form and saving it unchanged is not something that happened."""
    chore = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()
    before = len((await founder.get(f"{API_PREFIX}/feed")).json()["items"])

    await founder.patch(f"{API_PREFIX}/chores/{chore['id']}", json={"title": CHORE["title"]})

    assert len((await founder.get(f"{API_PREFIX}/feed")).json()["items"]) == before


async def test_deleting_a_chore_leaves_the_entry_that_says_so(founder: AsyncClient) -> None:
    """The chore goes, the record of its going stays — that is what an audit log is."""
    chore = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()

    await founder.delete(f"{API_PREFIX}/chores/{chore['id']}")

    entry = (await founder.get(f"{API_PREFIX}/feed")).json()["items"][0]
    assert entry["type"] == "chore_deleted"
    assert entry["body"] == CHORE["title"]
