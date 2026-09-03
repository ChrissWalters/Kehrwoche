"""Joining, roles, removal and moving out — including every edge case of the plan."""

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
from app.models import FeedEvent, FeedEventType, Household, User, UserRole
from app.models.base import utcnow
from app.security import JOIN_RATE_LIMIT
from tests.conftest import CsrfAwareClient
from tests.test_auth import CREDENTIALS, REGISTRATION
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
    """A second browser, signed up but without a household.

    Depends on the founder so account ids — and therefore the member order — are stable.
    """
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with CsrfAwareClient(transport=transport, base_url="https://testserver") as client:
        await client.get(f"{API_PREFIX}/meta")
        await sign_up(client, "bea@example.org")
        yield client


async def member_id_of(client: AsyncClient, username: str, db: Session) -> int:
    user = db.scalar(select(User).where(User.username == username))
    assert user is not None
    return user.id


async def test_joining_with_the_code_makes_a_member(
    second_client: AsyncClient, join_code: str, db_session: Session
) -> None:
    response = await second_client.post(
        f"{API_PREFIX}/household/join", json={"join_code": join_code}
    )

    assert response.status_code == 200
    members = response.json()["members"]
    assert len(members) == 2
    assert [member["role"] for member in members] == ["admin", "member"]

    joined = db_session.scalar(select(User).where(User.username == "bea@example.org"))
    assert joined is not None
    assert joined.role is UserRole.MEMBER


async def test_joining_is_case_insensitive(second_client: AsyncClient, join_code: str) -> None:
    response = await second_client.post(
        f"{API_PREFIX}/household/join", json={"join_code": join_code.lower()}
    )

    assert response.status_code == 200


async def test_joining_accepts_the_code_in_groups(
    second_client: AsyncClient, join_code: str
) -> None:
    """The code is shown as "ABCD EFGH JKMN" — typing it that way has to work."""
    grouped = " ".join(join_code[index : index + 4] for index in range(0, len(join_code), 4))

    response = await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": grouped})

    assert response.status_code == 200


async def test_joining_emits_a_member_joined_event(
    second_client: AsyncClient, join_code: str, db_session: Session
) -> None:
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})

    events = db_session.scalars(select(FeedEvent).order_by(FeedEvent.id)).all()
    assert [event.type for event in events] == [
        FeedEventType.MEMBER_JOINED,
        FeedEventType.MEMBER_JOINED,
    ]


async def test_unknown_code_is_not_found(second_client: AsyncClient, join_code: str) -> None:
    response = await second_client.post(
        f"{API_PREFIX}/household/join", json={"join_code": "ZZZZZZZZZZZZ"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == ErrorCode.NOT_FOUND


async def test_regenerated_code_stops_working(
    founder: AsyncClient, second_client: AsyncClient, join_code: str
) -> None:
    await founder.post(f"{API_PREFIX}/household/regenerate-code")

    response = await second_client.post(
        f"{API_PREFIX}/household/join", json={"join_code": join_code}
    )

    assert response.status_code == 404


async def test_joining_twice_is_a_conflict(second_client: AsyncClient, join_code: str) -> None:
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})

    response = await second_client.post(
        f"{API_PREFIX}/household/join", json={"join_code": join_code}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.ALREADY_IN_HOUSEHOLD


async def test_guessing_codes_is_rate_limited(second_client: AsyncClient, join_code: str) -> None:
    for _ in range(JOIN_RATE_LIMIT.max_attempts):
        response = await second_client.post(
            f"{API_PREFIX}/household/join", json={"join_code": "ZZZZZZZZZZZZ"}
        )
        assert response.status_code == 404

    blocked = await second_client.post(
        f"{API_PREFIX}/household/join", json={"join_code": join_code}
    )

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == ErrorCode.RATE_LIMITED
    assert int(blocked.headers["Retry-After"]) > 0


async def test_admin_can_promote_and_demote(
    founder: AsyncClient, second_client: AsyncClient, join_code: str, db_session: Session
) -> None:
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    member_id = await member_id_of(second_client, "bea@example.org", db_session)

    promoted = await founder.patch(
        f"{API_PREFIX}/household/members/{member_id}", json={"role": "admin"}
    )
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "admin"

    demoted = await founder.patch(
        f"{API_PREFIX}/household/members/{member_id}", json={"role": "member"}
    )
    assert demoted.status_code == 200
    assert demoted.json()["role"] == "member"


async def test_last_admin_cannot_demote_themselves(
    founder: AsyncClient, second_client: AsyncClient, join_code: str, db_session: Session
) -> None:
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    admin_id = await member_id_of(founder, CREDENTIALS["username"], db_session)

    response = await founder.patch(
        f"{API_PREFIX}/household/members/{admin_id}", json={"role": "member"}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.LAST_ADMIN


async def test_admin_may_step_down_once_someone_else_is_admin(
    founder: AsyncClient, second_client: AsyncClient, join_code: str, db_session: Session
) -> None:
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    member_id = await member_id_of(second_client, "bea@example.org", db_session)
    admin_id = await member_id_of(founder, CREDENTIALS["username"], db_session)
    await founder.patch(f"{API_PREFIX}/household/members/{member_id}", json={"role": "admin"})

    response = await founder.patch(
        f"{API_PREFIX}/household/members/{admin_id}", json={"role": "member"}
    )

    assert response.status_code == 200
    assert response.json()["role"] == "member"


async def test_member_cannot_change_roles(
    founder: AsyncClient, second_client: AsyncClient, join_code: str, db_session: Session
) -> None:
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    admin_id = await member_id_of(founder, CREDENTIALS["username"], db_session)

    response = await second_client.patch(
        f"{API_PREFIX}/household/members/{admin_id}", json={"role": "member"}
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.FORBIDDEN


async def test_foreign_member_is_not_found(founder: AsyncClient, db_session: Session) -> None:
    stranger = User(username="fremd@example.org", password_hash="x", first_name="Fremd")
    other_household = Household(name="Andere WG", join_code="ZZZZZZZZZZZZ")
    db_session.add(other_household)
    db_session.flush()
    stranger.household_id = other_household.id
    db_session.add(stranger)
    db_session.commit()

    patched = await founder.patch(
        f"{API_PREFIX}/household/members/{stranger.id}", json={"role": "admin"}
    )
    removed = await founder.delete(f"{API_PREFIX}/household/members/{stranger.id}")

    assert patched.status_code == 404
    assert removed.status_code == 404


async def test_admin_removes_a_member(
    founder: AsyncClient, second_client: AsyncClient, join_code: str, db_session: Session
) -> None:
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    member_id = await member_id_of(second_client, "bea@example.org", db_session)

    response = await founder.delete(f"{API_PREFIX}/household/members/{member_id}")

    assert response.status_code == 204
    db_session.expire_all()
    removed = db_session.get(User, member_id)
    assert removed is not None
    assert removed.household_id is None
    # The removed person lands in the create-or-join state.
    assert (await second_client.get(f"{API_PREFIX}/household")).status_code == 404


async def test_admin_cannot_remove_themselves(
    founder: AsyncClient, second_client: AsyncClient, join_code: str, db_session: Session
) -> None:
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    admin_id = await member_id_of(founder, CREDENTIALS["username"], db_session)

    response = await founder.delete(f"{API_PREFIX}/household/members/{admin_id}")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.CANNOT_TARGET_SELF


async def test_removal_emits_member_left(
    founder: AsyncClient, second_client: AsyncClient, join_code: str, db_session: Session
) -> None:
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    member_id = await member_id_of(second_client, "bea@example.org", db_session)

    await founder.delete(f"{API_PREFIX}/household/members/{member_id}")

    event = db_session.scalars(select(FeedEvent).order_by(FeedEvent.id.desc())).first()
    assert event is not None
    assert event.type == FeedEventType.MEMBER_LEFT
    assert event.reference_id == member_id


async def test_member_can_leave(
    second_client: AsyncClient, join_code: str, db_session: Session
) -> None:
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})

    response = await second_client.post(f"{API_PREFIX}/household/leave")

    assert response.status_code == 204
    assert (await second_client.get(f"{API_PREFIX}/household")).status_code == 404
    assert (await second_client.get(f"{API_PREFIX}/me")).json()["household_id"] is None


async def test_last_admin_with_members_left_behind_is_a_conflict(
    founder: AsyncClient, second_client: AsyncClient, join_code: str
) -> None:
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})

    response = await founder.post(f"{API_PREFIX}/household/leave")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.LAST_ADMIN
    # Still in charge.
    assert (await founder.get(f"{API_PREFIX}/household")).status_code == 200


async def test_admin_can_leave_after_handing_the_role_over(
    founder: AsyncClient, second_client: AsyncClient, join_code: str, db_session: Session
) -> None:
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    member_id = await member_id_of(second_client, "bea@example.org", db_session)
    await founder.patch(f"{API_PREFIX}/household/members/{member_id}", json={"role": "admin"})

    response = await founder.post(f"{API_PREFIX}/household/leave")

    assert response.status_code == 204
    assert (await second_client.get(f"{API_PREFIX}/household")).status_code == 200


async def test_the_last_member_takes_the_household_with_them(
    founder: AsyncClient, db_session: Session
) -> None:
    response = await founder.post(f"{API_PREFIX}/household/leave")

    assert response.status_code == 204
    assert db_session.scalar(select(Household)) is None
    # Feed events of the household are gone as well.
    assert db_session.scalars(select(FeedEvent)).all() == []
    assert (await founder.get(f"{API_PREFIX}/household")).status_code == 404


async def test_leaving_needs_a_household(client: AsyncClient) -> None:
    await sign_up(client)

    response = await client.post(f"{API_PREFIX}/household/leave")

    assert response.status_code == 404


async def test_membership_routes_need_a_signed_in_person(client: AsyncClient) -> None:
    assert (
        await client.post(f"{API_PREFIX}/household/join", json={"join_code": "X" * 12})
    ).status_code == 401
    assert (await client.post(f"{API_PREFIX}/household/leave")).status_code == 401
    assert (await client.delete(f"{API_PREFIX}/household/members/1")).status_code == 401


async def test_rejoining_after_leaving_works(second_client: AsyncClient, join_code: str) -> None:
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    await second_client.post(f"{API_PREFIX}/household/leave")

    response = await second_client.post(
        f"{API_PREFIX}/household/join", json={"join_code": join_code}
    )

    assert response.status_code == 200


async def test_registration_only_person_has_no_household(client: AsyncClient) -> None:
    await client.post(f"{API_PREFIX}/auth/register", json=REGISTRATION)
    await client.post(f"{API_PREFIX}/auth/login", json=CREDENTIALS)

    assert (await client.get(f"{API_PREFIX}/me")).json()["household_id"] is None


# --- Change marker of the membership -----------------------------------------------


async def test_the_state_marker_moves_when_somebody_joins(
    founder: AsyncClient, second_client: AsyncClient, join_code: str
) -> None:
    """Open tabs build their forms on the member list — they have to learn about it."""
    before = (await founder.get(f"{API_PREFIX}/household/state")).json()

    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    after_join = (await founder.get(f"{API_PREFIX}/household/state")).json()

    await second_client.post(f"{API_PREFIX}/household/leave")
    after_leave = (await founder.get(f"{API_PREFIX}/household/state")).json()

    assert before["household"] != after_join["household"]
    assert after_join["household"] != after_leave["household"]


async def test_the_state_marker_moves_when_the_household_is_renamed(
    founder: AsyncClient, db_session: Session
) -> None:
    """A rename keeps the row count, so only the timestamp can tell the marker apart.

    The clock is moved by hand: at second resolution, founding and renaming fall into
    the same second. Such collisions are accepted in production
    (see the synchronisation chapter) — a test must not depend on them.
    """
    household = db_session.scalar(select(Household))
    assert household is not None
    household.updated_at = utcnow() - timedelta(minutes=1)
    db_session.commit()
    before = (await founder.get(f"{API_PREFIX}/household/state")).json()

    await founder.patch(f"{API_PREFIX}/household", json={"name": "Neue WG"})

    after = (await founder.get(f"{API_PREFIX}/household/state")).json()
    assert before["household"] != after["household"]


# --- Handing the household on (AP30b) -----------------------------------------------


async def test_the_last_admin_can_hand_the_household_on_and_leave(
    founder: AsyncClient, second_client: AsyncClient, join_code: str, db_session: Session
) -> None:
    """The acceptance chain: promote, step down, leave — and the household keeps an admin.

    This is the reason the interface needs role management at all: without a way to pass
    the role on, the last admin is locked in.
    """
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    successor = await member_id_of(second_client, "bea@example.org", db_session)
    admin_id = await member_id_of(founder, CREDENTIALS["username"], db_session)

    # 1. Without a successor the door stays shut.
    refused = await founder.post(f"{API_PREFIX}/household/leave")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == ErrorCode.LAST_ADMIN
    # The refusal has to arrive as a key: it is read in whatever language was chosen.
    assert refused.json()["error"]["message_key"] == "error.household.last_admin_leave"

    # 2. Hand the role over, step down, leave.
    assert (
        await founder.patch(f"{API_PREFIX}/household/members/{successor}", json={"role": "admin"})
    ).status_code == 200
    assert (
        await founder.patch(f"{API_PREFIX}/household/members/{admin_id}", json={"role": "member"})
    ).status_code == 200
    assert (await founder.post(f"{API_PREFIX}/household/leave")).status_code == 204

    # 3. What is left behind is a working household with an administration.
    remaining = (await second_client.get(f"{API_PREFIX}/household")).json()
    assert [member["role"] for member in remaining["members"]] == ["admin"]
    assert (await founder.get(f"{API_PREFIX}/household")).status_code == 404


async def test_a_removed_member_loses_access_but_leaves_their_traces(
    founder: AsyncClient, second_client: AsyncClient, join_code: str, db_session: Session
) -> None:
    """Removing somebody is about membership, not about erasing what they did."""
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    member_id = await member_id_of(second_client, "bea@example.org", db_session)
    chore = (
        await second_client.post(
            f"{API_PREFIX}/chores", json={"title": "Bad", "points": 1, "rotation_seconds": 604800}
        )
    ).json()
    await second_client.post(f"{API_PREFIX}/chores/{chore['id']}/complete")

    await founder.delete(f"{API_PREFIX}/household/members/{member_id}")

    assert (await second_client.get(f"{API_PREFIX}/household")).status_code == 404
    history = (await founder.get(f"{API_PREFIX}/chores/history")).json()["items"]
    assert [entry["user_id"] for entry in history] == [member_id], "the completion stays"


async def test_a_regenerated_code_replaces_the_old_one(
    founder: AsyncClient, second_client: AsyncClient, join_code: str
) -> None:
    fresh = (await founder.post(f"{API_PREFIX}/household/regenerate-code")).json()["join_code"]

    stale = await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    assert stale.status_code == 404, "the old code is gone the moment it is replaced"

    assert (
        await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": fresh})
    ).status_code == 200


async def test_leaving_frees_the_account_for_another_household(
    founder: AsyncClient, second_client: AsyncClient, join_code: str
) -> None:
    """The point of leaving: the account survives and can start over somewhere else.

    Somebody moves out of one flat and into another — same person, same login, different
    household. Deleting the account would be a different decision entirely.
    """
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})

    # Bea moves out and lands in the state of a fresh account.
    assert (await second_client.post(f"{API_PREFIX}/household/leave")).status_code == 204
    assert (await second_client.get(f"{API_PREFIX}/household")).status_code == 404
    assert (await second_client.get(f"{API_PREFIX}/me")).json()["household_id"] is None

    # …and founds one of her own, with the very same account.
    founded = await second_client.post(
        f"{API_PREFIX}/household", json=HOUSEHOLD | {"name": "Eigene WG"}
    )
    assert founded.status_code == 201
    assert founded.json()["members"][0]["role"] == "admin"
    assert (await second_client.get(f"{API_PREFIX}/chores")).status_code == 200


async def test_leaving_and_joining_a_different_household(
    app: FastAPI, founder: AsyncClient, second_client: AsyncClient, join_code: str
) -> None:
    """The other half of the acceptance case: join somewhere else after leaving."""
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    await second_client.post(f"{API_PREFIX}/household/leave")

    # A third person opens a household of their own and passes the code on.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with CsrfAwareClient(transport=transport, base_url="https://testserver") as third:
        await third.get(f"{API_PREFIX}/meta")
        await sign_up(third, "chris@example.org")
        await third.post(f"{API_PREFIX}/household", json=HOUSEHOLD | {"name": "Andere WG"})
        other_code = (await third.get(f"{API_PREFIX}/household")).json()["join_code"]

    joined = await second_client.post(
        f"{API_PREFIX}/household/join", json={"join_code": other_code}
    )

    assert joined.status_code == 200
    assert joined.json()["name"] == "Andere WG"
    assert (await second_client.get(f"{API_PREFIX}/household")).json()["name"] == "Andere WG"
