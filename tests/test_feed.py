"""The pinboard: system events, posts, likes and comments."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import ErrorCode
from app.main import API_PREFIX
from app.models import Comment, FeedEvent, FeedEventType, Household, Like, User
from app.services.feed import PAGE_SIZE, emit_event
from tests.conftest import CsrfAwareClient
from tests.test_household import HOUSEHOLD, sign_up

POST = {"body": "Wer hat das Bad benutzt?"}


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


async def post(client: AsyncClient, body: str = POST["body"]) -> dict:
    return (await client.post(f"{API_PREFIX}/feed", json={"body": body})).json()


# --- emit_event ---------------------------------------------------------------------


def test_an_event_belongs_to_the_transaction_of_its_change(db_session: Session) -> None:
    """The feed is the audit log: no entry without the change it describes."""
    household = Household(name="WG", join_code="AAAABBBBCCCC")
    db_session.add(household)
    db_session.commit()

    emit_event(db_session, household, FeedEventType.CHORE_CREATED, body="Bad putzen")
    db_session.rollback()

    assert db_session.scalars(select(FeedEvent)).all() == []


def test_an_event_without_an_actor_is_allowed(db_session: Session) -> None:
    """Scheduled events have nobody to name."""
    household = Household(name="WG", join_code="DDDDEEEEFFFF")
    db_session.add(household)
    db_session.commit()

    event = emit_event(db_session, household, FeedEventType.CHORE_DONE)
    db_session.commit()

    assert event.actor_id is None


async def test_every_module_writes_through_the_feed(founder: AsyncClient) -> None:
    """One round through the modules; each has to leave its entry."""
    chore = (
        await founder.post(
            f"{API_PREFIX}/chores", json={"title": "Bad", "points": 1, "rotation_seconds": 604800}
        )
    ).json()
    await founder.post(f"{API_PREFIX}/chores/{chore['id']}/complete")
    await founder.post(f"{API_PREFIX}/shopping", json={"name": "Milch"})
    await founder.post(f"{API_PREFIX}/expenses", json={"title": "Einkauf", "amount_cents": 500})

    types = [item["type"] for item in (await founder.get(f"{API_PREFIX}/feed")).json()["items"]]

    assert types == [
        FeedEventType.EXPENSE_ADDED,
        FeedEventType.SHOPPING_ADDED,
        FeedEventType.CHORE_DONE,
        FeedEventType.CHORE_CREATED,
        FeedEventType.MEMBER_JOINED,
    ]


# --- Reading the feed ---------------------------------------------------------------


async def test_the_feed_is_newest_first_with_everything_a_card_needs(
    founder: AsyncClient, db_session: Session
) -> None:
    entry = await post(founder)

    page = (await founder.get(f"{API_PREFIX}/feed")).json()

    first = page["items"][0]
    assert first["id"] == entry["id"]
    assert first["type"] == FeedEventType.USER_POST
    assert first["body"] == POST["body"]
    assert first["actor_id"] == db_session.scalar(select(User.id))
    assert first["like_count"] == 0
    assert first["liked_by_me"] is False
    assert first["comment_count"] == 0
    assert first["comments_unread"] == 0
    assert page["next_cursor"] is None


async def test_the_feed_pages_through_forty_five_events(founder: AsyncClient) -> None:
    """The acceptance case of the plan — 45 entries, nothing skipped or repeated."""
    for index in range(44):  # plus the member_joined event of founding
        await post(founder, f"Beitrag {index}")

    seen: list[int] = []
    cursor = None
    pages = 0
    while True:
        query = f"?cursor={cursor}" if cursor else ""
        page = (await founder.get(f"{API_PREFIX}/feed{query}")).json()
        seen.extend(item["id"] for item in page["items"])
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert pages == 3, f"45 entries at {PAGE_SIZE} per page"
    assert len(seen) == 45
    assert len(set(seen)) == 45
    assert seen == sorted(seen, reverse=True), "newest first, across page borders"


async def test_the_feed_stays_inside_the_household(
    founder: AsyncClient, second_client: AsyncClient
) -> None:
    await post(founder)
    await second_client.post(f"{API_PREFIX}/household", json=HOUSEHOLD | {"name": "Andere WG"})

    page = (await second_client.get(f"{API_PREFIX}/feed")).json()

    assert [item["type"] for item in page["items"]] == [FeedEventType.MEMBER_JOINED]


# --- Posts ---------------------------------------------------------------------------


async def test_a_post_is_written_as_a_user_event(founder: AsyncClient) -> None:
    response = await founder.post(f"{API_PREFIX}/feed", json={"body": "  Hallo WG  "})

    assert response.status_code == 201
    assert response.json()["body"] == "Hallo WG"
    assert response.json()["type"] == FeedEventType.USER_POST


async def test_a_post_of_spaces_is_refused(founder: AsyncClient) -> None:
    response = await founder.post(f"{API_PREFIX}/feed", json={"body": "   "})

    assert response.status_code == 400
    assert response.json()["error"]["field"] == "body"


async def test_your_own_post_can_be_deleted(founder: AsyncClient, db_session: Session) -> None:
    entry = await post(founder)

    response = await founder.delete(f"{API_PREFIX}/feed/{entry['id']}")

    assert response.status_code == 204
    assert db_session.get(FeedEvent, entry["id"]) is None


async def test_a_post_of_somebody_else_cannot_be_deleted(
    founder: AsyncClient, housemate: AsyncClient
) -> None:
    entry = await post(founder)

    response = await housemate.delete(f"{API_PREFIX}/feed/{entry['id']}")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.FORBIDDEN


async def test_a_system_entry_cannot_be_deleted(founder: AsyncClient) -> None:
    """The feed is the audit log — not even your own system entry goes away."""
    await founder.post(
        f"{API_PREFIX}/chores", json={"title": "Bad", "points": 1, "rotation_seconds": 604800}
    )
    system_entry = (await founder.get(f"{API_PREFIX}/feed")).json()["items"][0]

    response = await founder.delete(f"{API_PREFIX}/feed/{system_entry['id']}")

    assert response.status_code == 403
    assert system_entry["type"] == FeedEventType.CHORE_CREATED


async def test_deleting_a_post_takes_its_likes_and_comments(
    founder: AsyncClient, housemate: AsyncClient, db_session: Session
) -> None:
    entry = await post(founder)
    await housemate.post(f"{API_PREFIX}/feed/{entry['id']}/like")
    await housemate.post(f"{API_PREFIX}/feed/{entry['id']}/comments", json={"body": "Ich nicht"})

    await founder.delete(f"{API_PREFIX}/feed/{entry['id']}")

    assert db_session.scalars(select(Like)).all() == []
    assert db_session.scalars(select(Comment)).all() == []


# --- Likes ---------------------------------------------------------------------------


async def test_liking_twice_turns_the_like_off_again(
    founder: AsyncClient, housemate: AsyncClient
) -> None:
    entry = await post(founder)

    liked = (await housemate.post(f"{API_PREFIX}/feed/{entry['id']}/like")).json()
    unliked = (await housemate.post(f"{API_PREFIX}/feed/{entry['id']}/like")).json()

    assert liked == {"liked": True, "like_count": 1}
    assert unliked == {"liked": False, "like_count": 0}


async def test_a_like_shows_up_on_the_card_of_the_person_who_gave_it(
    founder: AsyncClient, housemate: AsyncClient
) -> None:
    entry = await post(founder)
    await housemate.post(f"{API_PREFIX}/feed/{entry['id']}/like")

    mine = (await housemate.get(f"{API_PREFIX}/feed")).json()["items"][0]
    theirs = (await founder.get(f"{API_PREFIX}/feed")).json()["items"][0]

    assert mine["liked_by_me"] is True
    assert theirs["liked_by_me"] is False
    assert theirs["like_count"] == 1


async def test_system_entries_can_be_liked_too(founder: AsyncClient) -> None:
    """A finished chore deserves applause as much as a post does."""
    await founder.post(
        f"{API_PREFIX}/chores", json={"title": "Bad", "points": 1, "rotation_seconds": 604800}
    )
    entry = (await founder.get(f"{API_PREFIX}/feed")).json()["items"][0]

    response = await founder.post(f"{API_PREFIX}/feed/{entry['id']}/like")

    assert response.json() == {"liked": True, "like_count": 1}


# --- Comments ------------------------------------------------------------------------


async def test_comments_are_listed_oldest_first(
    founder: AsyncClient, housemate: AsyncClient
) -> None:
    entry = await post(founder)
    await housemate.post(f"{API_PREFIX}/feed/{entry['id']}/comments", json={"body": "Nicht ich"})
    await founder.post(f"{API_PREFIX}/feed/{entry['id']}/comments", json={"body": "Ich auch nicht"})

    comments = (await founder.get(f"{API_PREFIX}/feed/{entry['id']}/comments")).json()

    assert [comment["body"] for comment in comments] == ["Nicht ich", "Ich auch nicht"]


async def test_a_comment_of_spaces_is_refused(founder: AsyncClient) -> None:
    entry = await post(founder)

    response = await founder.post(f"{API_PREFIX}/feed/{entry['id']}/comments", json={"body": " "})

    assert response.status_code == 400


async def test_only_the_author_sees_the_unread_marker(
    founder: AsyncClient, housemate: AsyncClient
) -> None:
    entry = await post(founder)

    await housemate.post(f"{API_PREFIX}/feed/{entry['id']}/comments", json={"body": "Ich nicht"})

    author_card = (await founder.get(f"{API_PREFIX}/feed")).json()["items"][0]
    other_card = (await housemate.get(f"{API_PREFIX}/feed")).json()["items"][0]
    assert author_card["comments_unread"] == 1
    assert author_card["comment_count"] == 1
    assert other_card["comments_unread"] == 0, "the marker belongs to the author only"


async def test_reading_the_comments_clears_the_marker(
    founder: AsyncClient, housemate: AsyncClient
) -> None:
    entry = await post(founder)
    await housemate.post(f"{API_PREFIX}/feed/{entry['id']}/comments", json={"body": "Eins"})
    await housemate.post(f"{API_PREFIX}/feed/{entry['id']}/comments", json={"body": "Zwei"})

    await founder.get(f"{API_PREFIX}/feed/{entry['id']}/comments")

    card = (await founder.get(f"{API_PREFIX}/feed")).json()["items"][0]
    assert card["comments_unread"] == 0

    await housemate.post(f"{API_PREFIX}/feed/{entry['id']}/comments", json={"body": "Drei"})
    assert (await founder.get(f"{API_PREFIX}/feed")).json()["items"][0]["comments_unread"] == 1


async def test_your_own_comment_never_counts_as_unread(founder: AsyncClient) -> None:
    entry = await post(founder)

    await founder.post(f"{API_PREFIX}/feed/{entry['id']}/comments", json={"body": "Nachtrag"})

    card = (await founder.get(f"{API_PREFIX}/feed")).json()["items"][0]
    assert card["comment_count"] == 1
    assert card["comments_unread"] == 0


async def test_reading_the_comments_of_somebody_else_changes_no_marker(
    founder: AsyncClient, housemate: AsyncClient, db_session: Session
) -> None:
    entry = await post(founder)
    await housemate.post(f"{API_PREFIX}/feed/{entry['id']}/comments", json={"body": "Ich nicht"})

    await housemate.get(f"{API_PREFIX}/feed/{entry['id']}/comments")

    stored = db_session.get(FeedEvent, entry["id"])
    assert stored is not None
    assert stored.comments_seen_id is None
    assert (await founder.get(f"{API_PREFIX}/feed")).json()["items"][0]["comments_unread"] == 1


# --- Household boundary and permissions ----------------------------------------------


async def test_an_entry_of_another_household_does_not_exist(
    founder: AsyncClient, second_client: AsyncClient
) -> None:
    entry = await post(founder)
    await second_client.post(f"{API_PREFIX}/household", json=HOUSEHOLD | {"name": "Andere WG"})

    liked = await second_client.post(f"{API_PREFIX}/feed/{entry['id']}/like")
    commented = await second_client.post(
        f"{API_PREFIX}/feed/{entry['id']}/comments", json={"body": "Fremd"}
    )
    read = await second_client.get(f"{API_PREFIX}/feed/{entry['id']}/comments")
    removed = await second_client.delete(f"{API_PREFIX}/feed/{entry['id']}")

    assert [response.status_code for response in (liked, commented, read, removed)] == [
        404,
        404,
        404,
        404,
    ]


async def test_the_pinboard_needs_a_household(client: AsyncClient) -> None:
    await sign_up(client)

    assert (await client.get(f"{API_PREFIX}/feed")).status_code == 404
    assert (await client.post(f"{API_PREFIX}/feed", json=POST)).status_code == 404


async def test_the_pinboard_needs_a_sign_in(client: AsyncClient) -> None:
    await client.get(f"{API_PREFIX}/meta")

    assert (await client.get(f"{API_PREFIX}/feed")).status_code == 401
    assert (await client.post(f"{API_PREFIX}/feed", json=POST)).status_code == 401
