"""In-app notifications and the scheduler that raises the due ones."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import API_PREFIX
from app.models import Chore, Household, Notification, NotificationType, User
from app.models.base import utcnow
from app.tasks import notify_due_chores
from tests.conftest import CsrfAwareClient
from tests.test_chores import CHORE
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


@pytest.fixture
async def housemate(second_client: AsyncClient, join_code: str) -> AsyncClient:
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    return second_client


@pytest.fixture
async def third_client(
    app: FastAPI, founder: AsyncClient, join_code: str
) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with CsrfAwareClient(transport=transport, base_url="https://testserver") as client:
        await client.get(f"{API_PREFIX}/meta")
        await sign_up(client, "chris")
        await client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
        yield client


def make_household(db: Session) -> tuple[Household, User]:
    """A household with one member, built directly — the scheduler needs no HTTP."""
    household = Household(name="WG", join_code="AAAABBBBCCCC")
    db.add(household)
    db.flush()
    member = User(username="alex", password_hash="x", first_name="Alex", household_id=household.id)
    db.add(member)
    db.commit()
    return household, member


# --- The scheduler --------------------------------------------------------------------


def test_a_due_chore_notifies_whoever_is_on_duty(db_session: Session) -> None:
    household, member = make_household(db_session)
    chore = Chore(
        household_id=household.id,
        title="Müll rausbringen",
        rotation_seconds=604800,
        member_order=[member.id],
        current_user_id=member.id,
        due_at=utcnow() - timedelta(minutes=1),
    )
    db_session.add(chore)
    db_session.commit()

    sent = notify_due_chores(db_session)

    notification = db_session.scalar(select(Notification))
    assert sent == 1
    assert notification is not None
    assert notification.user_id == member.id
    assert notification.type == NotificationType.CHORE_DUE
    assert notification.title_key == "notification.chore_due.title"
    assert notification.params["chore"] == "Müll rausbringen"
    assert notification.reference_type == "chore"
    assert notification.reference_id == chore.id


def test_the_same_due_date_is_announced_only_once(db_session: Session) -> None:
    """The clock is faked: three rounds over the same due date must stay one message."""
    household, member = make_household(db_session)
    due = utcnow() - timedelta(hours=1)
    db_session.add(
        Chore(
            household_id=household.id,
            title="Bad putzen",
            rotation_seconds=604800,
            member_order=[member.id],
            current_user_id=member.id,
            due_at=due,
        )
    )
    db_session.commit()

    first = notify_due_chores(db_session, now=due + timedelta(minutes=1))
    second = notify_due_chores(db_session, now=due + timedelta(hours=2))
    third = notify_due_chores(db_session, now=due + timedelta(days=1))

    assert [first, second, third] == [1, 0, 0]
    assert len(db_session.scalars(select(Notification)).all()) == 1


def test_a_new_due_date_is_announced_again(db_session: Session) -> None:
    household, member = make_household(db_session)
    chore = Chore(
        household_id=household.id,
        title="Bad putzen",
        rotation_seconds=604800,
        member_order=[member.id],
        current_user_id=member.id,
        due_at=utcnow() - timedelta(days=8),
    )
    db_session.add(chore)
    db_session.commit()
    notify_due_chores(db_session)

    # Next week's turn — same chore, new due date.
    chore.due_at = utcnow() - timedelta(minutes=5)
    db_session.commit()
    sent = notify_due_chores(db_session)

    assert sent == 1
    assert len(db_session.scalars(select(Notification)).all()) == 2


def test_chores_that_are_not_due_stay_quiet(db_session: Session) -> None:
    household, member = make_household(db_session)
    db_session.add_all(
        [
            Chore(
                household_id=household.id,
                title="Später",
                rotation_seconds=604800,
                member_order=[member.id],
                current_user_id=member.id,
                due_at=utcnow() + timedelta(days=1),
            ),
            # On demand: no due date, nothing to remind about.
            Chore(
                household_id=household.id,
                title="Nach Bedarf",
                rotation_seconds=-1,
                member_order=[member.id],
                current_user_id=member.id,
            ),
        ]
    )
    db_session.commit()

    assert notify_due_chores(db_session) == 0


# --- Notifications from the modules ---------------------------------------------------


async def test_handing_a_chore_on_tells_the_next_person(
    founder: AsyncClient, housemate: AsyncClient, db_session: Session
) -> None:
    chore = (await founder.post(f"{API_PREFIX}/chores", json=CHORE)).json()

    await founder.post(f"{API_PREFIX}/chores/{chore['id']}/complete")

    bea = db_session.scalar(select(User).where(User.username == "bea"))
    assert bea is not None
    notification = db_session.scalar(select(Notification).where(Notification.user_id == bea.id))
    assert notification is not None
    assert notification.type == NotificationType.CHORE_ASSIGNED
    assert notification.params["chore"] == chore["title"]


async def test_a_comment_reaches_the_author_of_the_post(
    founder: AsyncClient, housemate: AsyncClient, db_session: Session
) -> None:
    entry = (await founder.post(f"{API_PREFIX}/feed", json={"body": "Wer war das?"})).json()

    await housemate.post(f"{API_PREFIX}/feed/{entry['id']}/comments", json={"body": "Ich nicht"})
    await housemate.post(f"{API_PREFIX}/feed/{entry['id']}/like")

    types = [notification.type for notification in db_session.scalars(select(Notification)).all()]
    assert NotificationType.FEED_COMMENT in types
    assert NotificationType.FEED_LIKE in types


async def test_nobody_is_notified_about_their_own_doing(
    founder: AsyncClient, db_session: Session
) -> None:
    entry = (await founder.post(f"{API_PREFIX}/feed", json={"body": "Hallo"})).json()

    await founder.post(f"{API_PREFIX}/feed/{entry['id']}/like")
    await founder.post(f"{API_PREFIX}/feed/{entry['id']}/comments", json={"body": "Nachtrag"})

    assert db_session.scalars(select(Notification)).all() == []


async def test_archiving_tells_everybody_who_has_to_pay(
    founder: AsyncClient, housemate: AsyncClient, db_session: Session
) -> None:
    await founder.post(f"{API_PREFIX}/expenses", json={"title": "Einkauf", "amount_cents": 1000})

    await founder.post(f"{API_PREFIX}/expenses/archive")

    bea = db_session.scalar(select(User).where(User.username == "bea"))
    assert bea is not None
    notification = db_session.scalar(
        select(Notification).where(Notification.type == NotificationType.SETTLEMENT_DUE)
    )
    assert notification is not None
    assert notification.user_id == bea.id
    assert notification.params["amount_cents"] == 500


# --- The endpoints --------------------------------------------------------------------


async def test_the_panel_lists_own_notifications_newest_first(
    founder: AsyncClient, housemate: AsyncClient
) -> None:
    entry = (await founder.post(f"{API_PREFIX}/feed", json={"body": "Hallo"})).json()
    await housemate.post(f"{API_PREFIX}/feed/{entry['id']}/comments", json={"body": "Erster"})
    await housemate.post(f"{API_PREFIX}/feed/{entry['id']}/like")

    page = (await founder.get(f"{API_PREFIX}/notifications")).json()

    assert [item["type"] for item in page["items"]] == [
        NotificationType.FEED_LIKE,
        NotificationType.FEED_COMMENT,
    ]
    assert page["unread"] == 2
    assert page["next_cursor"] is None
    assert page["items"][0]["read_at"] is None
    # Keys plus parameters, never a finished sentence — the text is rendered on the
    # client, in the language of whoever is reading.
    assert page["items"][0]["title_key"] == "notification.feed_like.title"
    assert page["items"][0]["body_key"] == "notification.feed_like.body"
    assert set(page["items"][0]["params"]) == {"actor"}


async def test_the_badge_counts_only_unread_ones(
    founder: AsyncClient, housemate: AsyncClient
) -> None:
    entry = (await founder.post(f"{API_PREFIX}/feed", json={"body": "Hallo"})).json()
    await housemate.post(f"{API_PREFIX}/feed/{entry['id']}/comments", json={"body": "Erster"})
    await housemate.post(f"{API_PREFIX}/feed/{entry['id']}/like")
    first = (await founder.get(f"{API_PREFIX}/notifications")).json()["items"][0]

    read = await founder.post(f"{API_PREFIX}/notifications/{first['id']}/read")

    assert read.status_code == 200
    assert read.json()["read_at"] is not None
    assert (await founder.get(f"{API_PREFIX}/notifications")).json()["unread"] == 1
    # The bell of the household state agrees with the panel.
    assert (await founder.get(f"{API_PREFIX}/household/state")).json()["notifications"] == 1


async def test_reading_all_of_them_clears_the_badge(
    founder: AsyncClient, housemate: AsyncClient
) -> None:
    entry = (await founder.post(f"{API_PREFIX}/feed", json={"body": "Hallo"})).json()
    await housemate.post(f"{API_PREFIX}/feed/{entry['id']}/comments", json={"body": "Erster"})
    await housemate.post(f"{API_PREFIX}/feed/{entry['id']}/like")

    response = await founder.post(f"{API_PREFIX}/notifications/read-all")

    assert response.json() == {"read": 2}
    assert (await founder.get(f"{API_PREFIX}/notifications")).json()["unread"] == 0
    # Reading twice is harmless and reports that nothing was left.
    assert (await founder.post(f"{API_PREFIX}/notifications/read-all")).json() == {"read": 0}


async def test_the_panel_pages_through_with_the_cursor(
    founder: AsyncClient, housemate: AsyncClient
) -> None:
    entry = (await founder.post(f"{API_PREFIX}/feed", json={"body": "Hallo"})).json()
    for index in range(25):
        await housemate.post(
            f"{API_PREFIX}/feed/{entry['id']}/comments", json={"body": f"Nummer {index}"}
        )

    first = (await founder.get(f"{API_PREFIX}/notifications?limit=10")).json()
    second = (
        await founder.get(f"{API_PREFIX}/notifications?limit=10&cursor={first['next_cursor']}")
    ).json()
    third = (
        await founder.get(f"{API_PREFIX}/notifications?limit=10&cursor={second['next_cursor']}")
    ).json()

    assert [len(page["items"]) for page in (first, second, third)] == [10, 10, 5]
    assert third["next_cursor"] is None
    ids = [item["id"] for page in (first, second, third) for item in page["items"]]
    assert len(set(ids)) == 25


async def test_a_notification_of_somebody_else_does_not_exist(
    founder: AsyncClient, housemate: AsyncClient
) -> None:
    entry = (await founder.post(f"{API_PREFIX}/feed", json={"body": "Hallo"})).json()
    await housemate.post(f"{API_PREFIX}/feed/{entry['id']}/like")
    mine = (await founder.get(f"{API_PREFIX}/notifications")).json()["items"][0]

    response = await housemate.post(f"{API_PREFIX}/notifications/{mine['id']}/read")

    assert response.status_code == 404
    assert (await housemate.get(f"{API_PREFIX}/notifications")).json()["items"] == []


async def test_notifications_need_a_sign_in_but_no_household(client: AsyncClient) -> None:
    await client.get(f"{API_PREFIX}/meta")
    assert (await client.get(f"{API_PREFIX}/notifications")).status_code == 401

    await sign_up(client)

    page = await client.get(f"{API_PREFIX}/notifications")
    assert page.status_code == 200, "the bell belongs to the person, not to the household"
    assert page.json() == {"items": [], "next_cursor": None, "unread": 0}


async def test_one_settlement_is_one_notification_per_person(
    founder: AsyncClient, housemate: AsyncClient, third_client: AsyncClient, db_session: Session
) -> None:
    """Chris owes two people — that is still one settlement, so one message."""
    alex, bea, chris = list(db_session.scalars(select(User.id).order_by(User.id)))
    await founder.post(f"{API_PREFIX}/expenses", json={"title": "Einkauf", "amount_cents": 3000})
    await housemate.post(f"{API_PREFIX}/expenses", json={"title": "Getränke", "amount_cents": 3000})

    await founder.post(f"{API_PREFIX}/expenses/archive")

    owed = db_session.scalars(
        select(Notification).where(
            Notification.type == NotificationType.SETTLEMENT_DUE, Notification.user_id == chris
        )
    ).all()
    assert len(owed) == 1
    assert owed[0].params["amount_cents"] == 2000, "both payments added up"
    assert owed[0].params["payments"] == 2
