"""The household kitty: recording expenses and dividing them."""

from __future__ import annotations

import random
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import ErrorCode
from app.main import API_PREFIX
from app.models import (
    Expense,
    ExpenseShare,
    FeedEvent,
    FeedEventType,
    Household,
    SettlementPeriod,
    User,
)
from app.models.base import utcnow
from app.services.expenses import settlement_payments, split_evenly
from app.services.sync import household_state
from tests.conftest import CsrfAwareClient
from tests.test_household import HOUSEHOLD, sign_up

EXPENSE = {"title": "Großeinkauf", "amount_cents": 1000}


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
    """A second person who has joined the household of the founder."""
    await second_client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
    return second_client


@pytest.fixture
async def third_client(
    app: FastAPI, founder: AsyncClient, join_code: str
) -> AsyncIterator[AsyncClient]:
    """A third person in the household — enough for a settlement worth calculating."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with CsrfAwareClient(transport=transport, base_url="https://testserver") as client:
        await client.get(f"{API_PREFIX}/meta")
        await sign_up(client, "chris")
        await client.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
        yield client


async def record(client: AsyncClient, **overrides: object) -> dict:
    response = await client.post(f"{API_PREFIX}/expenses", json={**EXPENSE, **overrides})
    return response.json()


def archive(db_session: Session, expense_id: int) -> None:
    """Move one expense into a closed period, as AP20 will do for the whole period."""
    expense = db_session.get(Expense, expense_id)
    assert expense is not None
    period = SettlementPeriod(household_id=expense.household_id, closed_at=utcnow())
    db_session.add(period)
    db_session.flush()
    expense.period_id = period.id
    db_session.commit()


# --- Splitting (unit) --------------------------------------------------------------


def test_ten_euros_on_three_people() -> None:
    assert split_evenly(1000, [1, 2, 3]) == [(1, 334), (2, 333), (3, 333)]


def test_an_amount_that_divides_cleanly_leaves_no_remainder() -> None:
    assert split_evenly(900, [7, 4]) == [(4, 450), (7, 450)]


def test_a_single_participant_carries_everything() -> None:
    assert split_evenly(999, [5]) == [(5, 999)]


def test_the_order_the_client_sends_does_not_change_the_result() -> None:
    assert split_evenly(1000, [3, 1, 2]) == split_evenly(1000, [1, 2, 3])


def test_the_shares_always_add_up_to_the_amount() -> None:
    for amount in (1, 7, 101, 1000, 99_999):
        for people in range(1, 8):
            shares = split_evenly(amount, list(range(1, people + 1)))
            assert sum(share for _, share in shares) == amount


# --- Recording ---------------------------------------------------------------------


async def test_an_expense_defaults_to_the_whole_household_and_today(
    founder: AsyncClient, housemate: AsyncClient, db_session: Session
) -> None:
    response = await founder.post(f"{API_PREFIX}/expenses", json=EXPENSE)

    assert response.status_code == 201
    body = response.json()
    assert body["spent_at"] == utcnow().date().isoformat()
    assert body["period_id"] is None
    assert [share["share_cents"] for share in body["shares"]] == [500, 500]
    payer = db_session.scalars(select(User).order_by(User.id)).first()
    assert payer is not None
    assert body["paid_by_id"] == payer.id


async def test_recording_an_expense_writes_one_feed_entry(
    founder: AsyncClient, db_session: Session
) -> None:
    expense = await record(founder)

    event = db_session.scalars(select(FeedEvent).order_by(FeedEvent.id.desc())).first()
    assert event is not None
    assert event.type == FeedEventType.EXPENSE_ADDED
    assert event.reference_type == "expense"
    assert event.reference_id == expense["id"]
    assert event.body == "Großeinkauf"


async def test_the_leftover_cent_goes_to_the_first_participant(
    founder: AsyncClient, housemate: AsyncClient, db_session: Session
) -> None:
    members = db_session.scalars(select(User.id).order_by(User.id)).all()

    expense = await record(founder, amount_cents=1001)

    assert expense["shares"] == [
        {"user_id": members[0], "share_cents": 501},
        {"user_id": members[1], "share_cents": 500},
    ]


async def test_an_even_split_over_a_selection(
    founder: AsyncClient, housemate: AsyncClient, db_session: Session
) -> None:
    """Not everybody drinks the beer — the split follows the selection."""
    members = list(db_session.scalars(select(User.id).order_by(User.id)))

    expense = await record(founder, participants=[members[1]])

    assert expense["shares"] == [{"user_id": members[1], "share_cents": 1000}]


async def test_a_manual_split_is_stored_as_sent(
    founder: AsyncClient, housemate: AsyncClient, db_session: Session
) -> None:
    members = list(db_session.scalars(select(User.id).order_by(User.id)))

    expense = await record(
        founder,
        shares=[
            {"user_id": members[0], "share_cents": 700},
            {"user_id": members[1], "share_cents": 300},
        ],
    )

    assert {share["user_id"]: share["share_cents"] for share in expense["shares"]} == {
        members[0]: 700,
        members[1]: 300,
    }


async def test_somebody_else_can_be_named_as_the_payer(
    founder: AsyncClient, housemate: AsyncClient, db_session: Session
) -> None:
    members = list(db_session.scalars(select(User.id).order_by(User.id)))

    expense = await record(founder, paid_by_id=members[1])

    assert expense["paid_by_id"] == members[1]


async def test_a_backdated_expense_keeps_its_date(founder: AsyncClient) -> None:
    yesterday = (utcnow().date() - timedelta(days=1)).isoformat()

    expense = await record(founder, spent_at=yesterday)

    assert expense["spent_at"] == yesterday


# --- Refused input -----------------------------------------------------------------


async def test_shares_that_do_not_add_up_are_refused(
    founder: AsyncClient, housemate: AsyncClient, db_session: Session
) -> None:
    members = list(db_session.scalars(select(User.id).order_by(User.id)))

    response = await founder.post(
        f"{API_PREFIX}/expenses",
        json={
            **EXPENSE,
            "shares": [
                {"user_id": members[0], "share_cents": 700},
                {"user_id": members[1], "share_cents": 200},
            ],
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == ErrorCode.SHARES_MISMATCH
    assert response.json()["error"]["field"] == "shares"
    assert db_session.scalars(select(Expense)).all() == []


async def test_both_split_modes_at_once_are_refused(
    founder: AsyncClient, db_session: Session
) -> None:
    member = db_session.scalar(select(User.id))

    response = await founder.post(
        f"{API_PREFIX}/expenses",
        json={
            **EXPENSE,
            "participants": [member],
            "shares": [{"user_id": member, "share_cents": 1000}],
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR


async def test_the_same_person_cannot_appear_twice(
    founder: AsyncClient, db_session: Session
) -> None:
    member = db_session.scalar(select(User.id))

    response = await founder.post(
        f"{API_PREFIX}/expenses",
        json={
            **EXPENSE,
            "shares": [
                {"user_id": member, "share_cents": 500},
                {"user_id": member, "share_cents": 500},
            ],
        },
    )

    assert response.status_code == 400


async def test_an_empty_selection_is_refused(founder: AsyncClient) -> None:
    response = await founder.post(f"{API_PREFIX}/expenses", json={**EXPENSE, "participants": []})

    assert response.status_code == 400
    assert response.json()["error"]["field"] == "participants"


@pytest.mark.parametrize("amount", [0, -100])
async def test_an_amount_has_to_be_positive(founder: AsyncClient, amount: int) -> None:
    response = await founder.post(
        f"{API_PREFIX}/expenses", json={**EXPENSE, "amount_cents": amount}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR


async def test_a_title_of_spaces_is_refused(founder: AsyncClient) -> None:
    response = await founder.post(f"{API_PREFIX}/expenses", json={**EXPENSE, "title": "   "})

    assert response.status_code == 400
    assert response.json()["error"]["field"] == "title"


async def test_somebody_from_another_household_cannot_be_involved(
    founder: AsyncClient, second_client: AsyncClient, db_session: Session
) -> None:
    """A stranger's id must not even confirm that the account exists."""
    stranger = db_session.scalars(select(User.id).order_by(User.id.desc())).first()

    for payload in ({"paid_by_id": stranger}, {"participants": [stranger]}):
        response = await founder.post(f"{API_PREFIX}/expenses", json={**EXPENSE, **payload})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == ErrorCode.NOT_FOUND


# --- Changing ----------------------------------------------------------------------


async def test_the_title_can_be_corrected(founder: AsyncClient) -> None:
    expense = await record(founder)

    response = await founder.patch(
        f"{API_PREFIX}/expenses/{expense['id']}", json={"title": " Wocheneinkauf "}
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Wocheneinkauf"
    assert response.json()["shares"] == expense["shares"]


async def test_a_new_amount_needs_the_split_that_goes_with_it(
    founder: AsyncClient, housemate: AsyncClient, db_session: Session
) -> None:
    """A household that agreed on 70/30 must not silently end up at 50/50."""
    members = list(db_session.scalars(select(User.id).order_by(User.id)))
    expense = await record(
        founder,
        shares=[
            {"user_id": members[0], "share_cents": 700},
            {"user_id": members[1], "share_cents": 300},
        ],
    )

    response = await founder.patch(
        f"{API_PREFIX}/expenses/{expense['id']}", json={"amount_cents": 2000}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == ErrorCode.SHARES_MISMATCH
    unchanged = (await founder.get(f"{API_PREFIX}/expenses")).json()["items"][0]
    assert unchanged["amount_cents"] == 1000


async def test_amount_and_split_change_together(
    founder: AsyncClient, housemate: AsyncClient, db_session: Session
) -> None:
    members = list(db_session.scalars(select(User.id).order_by(User.id)))
    expense = await record(founder)

    response = await founder.patch(
        f"{API_PREFIX}/expenses/{expense['id']}",
        json={"amount_cents": 1500, "participants": members},
    )

    assert response.status_code == 200
    assert [share["share_cents"] for share in response.json()["shares"]] == [750, 750]
    rows = db_session.scalars(
        select(ExpenseShare).where(ExpenseShare.expense_id == expense["id"])
    ).all()
    assert len(rows) == 2, "the old split must be replaced, not extended"


async def test_the_split_can_be_changed_on_its_own(
    founder: AsyncClient, housemate: AsyncClient, db_session: Session
) -> None:
    members = list(db_session.scalars(select(User.id).order_by(User.id)))
    expense = await record(founder)

    response = await founder.patch(
        f"{API_PREFIX}/expenses/{expense['id']}", json={"participants": [members[0]]}
    )

    assert response.json()["shares"] == [{"user_id": members[0], "share_cents": 1000}]


async def test_everyone_in_the_household_may_correct_an_expense(
    founder: AsyncClient, housemate: AsyncClient
) -> None:
    """Shared money is a shared responsibility — no owner-only editing."""
    expense = await record(founder)

    response = await housemate.patch(
        f"{API_PREFIX}/expenses/{expense['id']}", json={"title": "Korrigiert"}
    )

    assert response.status_code == 200


async def test_an_expense_can_be_removed(founder: AsyncClient, db_session: Session) -> None:
    expense = await record(founder)

    response = await founder.delete(f"{API_PREFIX}/expenses/{expense['id']}")

    assert response.status_code == 204
    assert db_session.scalars(select(Expense)).all() == []
    assert db_session.scalars(select(ExpenseShare)).all() == [], "shares go with the expense"


# --- Archived periods --------------------------------------------------------------


async def test_an_archived_expense_cannot_be_changed(
    founder: AsyncClient, db_session: Session
) -> None:
    expense = await record(founder)
    archive(db_session, expense["id"])

    changed = await founder.patch(f"{API_PREFIX}/expenses/{expense['id']}", json={"title": "Neu"})
    removed = await founder.delete(f"{API_PREFIX}/expenses/{expense['id']}")

    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == ErrorCode.CONFLICT
    assert removed.status_code == 409


async def test_the_list_shows_only_the_open_period(
    founder: AsyncClient, db_session: Session
) -> None:
    open_expense = await record(founder, title="Offen")
    archived = await record(founder, title="Archiviert")
    archive(db_session, archived["id"])

    page = (await founder.get(f"{API_PREFIX}/expenses")).json()

    assert [item["id"] for item in page["items"]] == [open_expense["id"]]


# --- Listing -----------------------------------------------------------------------


async def test_the_list_is_newest_first_by_date(founder: AsyncClient) -> None:
    today = utcnow().date()
    await record(founder, title="Vorgestern", spent_at=(today - timedelta(days=2)).isoformat())
    await record(founder, title="Heute")
    await record(founder, title="Gestern", spent_at=(today - timedelta(days=1)).isoformat())

    page = (await founder.get(f"{API_PREFIX}/expenses")).json()

    assert [item["title"] for item in page["items"]] == ["Heute", "Gestern", "Vorgestern"]
    assert page["next_cursor"] is None


async def test_the_list_pages_through_with_the_cursor(founder: AsyncClient) -> None:
    for index in range(25):
        await record(founder, title=f"Ausgabe {index}")

    first = (await founder.get(f"{API_PREFIX}/expenses?limit=10")).json()
    second = (
        await founder.get(f"{API_PREFIX}/expenses?limit=10&cursor={first['next_cursor']}")
    ).json()
    third = (
        await founder.get(f"{API_PREFIX}/expenses?limit=10&cursor={second['next_cursor']}")
    ).json()

    assert [len(page["items"]) for page in (first, second, third)] == [10, 10, 5]
    assert third["next_cursor"] is None
    ids = [item["id"] for page in (first, second, third) for item in page["items"]]
    assert len(set(ids)) == 25, "no expense may be skipped or repeated"


async def test_paging_works_across_days(founder: AsyncClient) -> None:
    """Two expenses per day: the cursor has to carry the date and the id."""
    today = utcnow().date()
    for day in range(3):
        for half in range(2):
            await record(
                founder,
                title=f"Tag {day} / {half}",
                spent_at=(today - timedelta(days=day)).isoformat(),
            )

    pages = []
    cursor = None
    while True:
        query = f"?limit=2{f'&cursor={cursor}' if cursor else ''}"
        page = (await founder.get(f"{API_PREFIX}/expenses{query}")).json()
        pages.extend(item["title"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert pages == [
        "Tag 0 / 1",
        "Tag 0 / 0",
        "Tag 1 / 1",
        "Tag 1 / 0",
        "Tag 2 / 1",
        "Tag 2 / 0",
    ]


async def test_an_invalid_cursor_is_refused(founder: AsyncClient) -> None:
    response = await founder.get(f"{API_PREFIX}/expenses?cursor=gestern")

    assert response.status_code == 400
    assert response.json()["error"]["field"] == "cursor"


async def test_mine_keeps_what_concerns_the_requester(
    founder: AsyncClient, housemate: AsyncClient, db_session: Session
) -> None:
    members = list(db_session.scalars(select(User.id).order_by(User.id)))
    await record(founder, title="Beide")
    await record(founder, title="Nur Bea", participants=[members[1]], paid_by_id=members[1])
    await record(founder, title="Nur ich", participants=[members[0]])

    page = (await housemate.get(f"{API_PREFIX}/expenses?mine=true")).json()

    assert [item["title"] for item in page["items"]] == ["Nur Bea", "Beide"]


# --- Household boundary and permissions --------------------------------------------


async def test_an_expense_of_another_household_does_not_exist(
    founder: AsyncClient, second_client: AsyncClient, db_session: Session
) -> None:
    expense = await record(founder)
    await second_client.post(f"{API_PREFIX}/household", json=HOUSEHOLD | {"name": "Andere WG"})

    read = await second_client.get(f"{API_PREFIX}/expenses")
    changed = await second_client.patch(
        f"{API_PREFIX}/expenses/{expense['id']}", json={"title": "Fremd"}
    )
    removed = await second_client.delete(f"{API_PREFIX}/expenses/{expense['id']}")

    assert read.json()["items"] == []
    assert changed.status_code == 404
    assert removed.status_code == 404
    assert db_session.get(Expense, expense["id"]) is not None


async def test_the_kitty_needs_a_household(client: AsyncClient) -> None:
    await sign_up(client)

    assert (await client.get(f"{API_PREFIX}/expenses")).status_code == 404
    assert (await client.post(f"{API_PREFIX}/expenses", json=EXPENSE)).status_code == 404


async def test_the_kitty_needs_a_sign_in(client: AsyncClient) -> None:
    await client.get(f"{API_PREFIX}/meta")

    read = await client.get(f"{API_PREFIX}/expenses")
    written = await client.post(f"{API_PREFIX}/expenses", json=EXPENSE)

    assert read.status_code == 401
    assert written.status_code == 401


# --- Synchronisation ---------------------------------------------------------------


async def test_the_change_marker_moves_with_every_change(
    founder: AsyncClient, housemate: AsyncClient, db_session: Session
) -> None:
    household = db_session.scalar(select(Household))
    user = db_session.scalar(select(User).order_by(User.id))
    assert household is not None and user is not None
    empty = household_state(db_session, household, user)["expenses"]

    expense = await record(founder)
    db_session.expire_all()
    after_create = household_state(db_session, household, user)["expenses"]
    assert empty != after_create

    # Age the row so the comparison holds on MariaDB as well, where a timestamp only
    # has second resolution and two calls in the same second would look identical.
    stored = db_session.get(Expense, expense["id"])
    assert stored is not None
    stored.updated_at = utcnow() - timedelta(minutes=1)
    db_session.commit()
    before_split = household_state(db_session, household, user)["expenses"]

    members = list(db_session.scalars(select(User.id).order_by(User.id)))
    await founder.patch(
        f"{API_PREFIX}/expenses/{expense['id']}", json={"participants": [members[0]]}
    )
    db_session.expire_all()

    after_split = household_state(db_session, household, user)["expenses"]
    assert before_split != after_split, "a changed split alone has to move the marker too"


# --- Balances ----------------------------------------------------------------------


async def test_balances_show_credit_and_debt(
    founder: AsyncClient, housemate: AsyncClient, db_session: Session
) -> None:
    members = list(db_session.scalars(select(User.id).order_by(User.id)))
    await record(founder)  # 10 €, paid by the founder, split evenly

    rows = (await founder.get(f"{API_PREFIX}/expenses/balances")).json()

    by_user = {row["user_id"]: row for row in rows}
    assert by_user[members[0]]["paid_cents"] == 1000
    assert by_user[members[0]]["balance_cents"] == 500
    assert by_user[members[1]]["owed_cents"] == 500
    assert by_user[members[1]]["balance_cents"] == -500
    assert sum(row["balance_cents"] for row in rows) == 0


async def test_everybody_starts_at_zero(founder: AsyncClient, housemate: AsyncClient) -> None:
    rows = (await founder.get(f"{API_PREFIX}/expenses/balances")).json()

    assert len(rows) == 2
    assert all(row["balance_cents"] == 0 for row in rows)


async def test_somebody_who_moved_out_keeps_their_debt(
    founder: AsyncClient, housemate: AsyncClient, db_session: Session
) -> None:
    """Leaving the household does not settle a bill — the balances have to add up."""
    members = list(db_session.scalars(select(User.id).order_by(User.id)))
    await record(founder)

    await housemate.post(f"{API_PREFIX}/household/leave")

    rows = (await founder.get(f"{API_PREFIX}/expenses/balances")).json()
    by_user = {row["user_id"]: row["balance_cents"] for row in rows}
    assert by_user[members[1]] == -500
    assert sum(by_user.values()) == 0


async def test_balances_stay_inside_the_household(
    founder: AsyncClient, second_client: AsyncClient
) -> None:
    await record(founder)
    await second_client.post(f"{API_PREFIX}/household", json=HOUSEHOLD | {"name": "Andere WG"})

    rows = (await second_client.get(f"{API_PREFIX}/expenses/balances")).json()

    assert [row["balance_cents"] for row in rows] == [0]


# --- Settlement (unit) -------------------------------------------------------------


def _settled(balances_by_user: dict[int, int], payments: list[tuple[int, int, int]]) -> dict:
    """Apply the payments to the balances; everybody should end up at zero."""
    result = dict(balances_by_user)
    for debtor, creditor, amount in payments:
        result[debtor] += amount
        result[creditor] -= amount
    return result


def _random_balances(rng: random.Random, people: int) -> dict[int, int]:
    amounts = [rng.randint(-50_000, 50_000) for _ in range(people - 1)]
    amounts.append(-sum(amounts))
    return dict(enumerate(amounts, start=1))


def test_the_proposal_settles_everybody_exactly() -> None:
    rng = random.Random(20260806)

    for _ in range(300):
        balances_by_user = _random_balances(rng, rng.randint(2, 8))
        payments = settlement_payments(balances_by_user)

        assert all(amount > 0 for _, _, amount in payments), "no payment of nothing"
        assert set(_settled(balances_by_user, payments).values()) == {0}


def test_the_proposal_never_needs_more_than_n_minus_one_payments() -> None:
    rng = random.Random(6082026)

    for _ in range(300):
        balances_by_user = _random_balances(rng, rng.randint(2, 8))
        payments = settlement_payments(balances_by_user)

        involved = [amount for amount in balances_by_user.values() if amount != 0]
        assert len(payments) <= max(len(involved) - 1, 0)


def test_the_proposal_does_not_depend_on_the_order_of_the_books() -> None:
    rng = random.Random(4711)
    balances_by_user = _random_balances(rng, 6)
    shuffled_keys = list(balances_by_user)
    rng.shuffle(shuffled_keys)

    first = settlement_payments(balances_by_user)
    second = settlement_payments({key: balances_by_user[key] for key in shuffled_keys})

    assert first == second


def test_a_balanced_household_owes_nothing() -> None:
    assert settlement_payments({1: 0, 2: 0}) == []


def test_the_largest_debtor_pays_the_largest_creditor() -> None:
    payments = settlement_payments({1: 900, 2: 100, 3: -1000})

    assert payments == [(3, 1, 900), (3, 2, 100)]


# --- Settlement (endpoint) ---------------------------------------------------------


async def test_the_settlement_names_who_pays_whom(
    founder: AsyncClient, housemate: AsyncClient, db_session: Session
) -> None:
    members = list(db_session.scalars(select(User.id).order_by(User.id)))
    await record(founder)

    body = (await founder.get(f"{API_PREFIX}/expenses/settlement")).json()

    assert body["payments"] == [
        {"from_user_id": members[1], "to_user_id": members[0], "amount_cents": 500}
    ]


async def test_a_household_of_three_by_hand(
    founder: AsyncClient, housemate: AsyncClient, third_client: AsyncClient, db_session: Session
) -> None:
    """The worked example from the acceptance criteria of the frontend package."""
    alex, bea, chris = list(db_session.scalars(select(User.id).order_by(User.id)))
    await record(founder, title="Wocheneinkauf", amount_cents=6000)  # Alex, 2000 each
    await record(housemate, title="Putzmittel", amount_cents=3000)  # Bea, 1000 each
    await record(third_client, title="Getränke", amount_cents=1500)  # Chris, 500 each
    await record(founder, title="Pizza", amount_cents=3000, participants=[alex, bea])

    rows = (await founder.get(f"{API_PREFIX}/expenses/balances")).json()
    payments = (await founder.get(f"{API_PREFIX}/expenses/settlement")).json()["payments"]

    # Paid: 9000 / 3000 / 1500. Carried: 5000 / 5000 / 3500.
    assert {row["user_id"]: row["balance_cents"] for row in rows} == {
        alex: 4000,
        bea: -2000,
        chris: -2000,
    }
    # Two debtors of the same size: the lower member id goes first, so the proposal
    # looks the same on every screen.
    assert payments == [
        {"from_user_id": bea, "to_user_id": alex, "amount_cents": 2000},
        {"from_user_id": chris, "to_user_id": alex, "amount_cents": 2000},
    ]


# --- Archiving ---------------------------------------------------------------------


async def test_archiving_freezes_the_period_and_resets_the_balances(
    founder: AsyncClient, housemate: AsyncClient, db_session: Session
) -> None:
    members = list(db_session.scalars(select(User.id).order_by(User.id)))
    expense = await record(founder)

    response = await founder.post(f"{API_PREFIX}/expenses/archive")

    assert response.status_code == 201
    period = response.json()
    assert period["expense_count"] == 1
    assert period["total_cents"] == 1000
    assert period["closed_by_id"] == members[0]
    assert period["payments"] == [
        {"from_user_id": members[1], "to_user_id": members[0], "amount_cents": 500}
    ]
    assert [item["id"] for item in period["expenses"]] == [expense["id"]]

    balances_after = (await founder.get(f"{API_PREFIX}/expenses/balances")).json()
    assert all(row["balance_cents"] == 0 for row in balances_after)
    assert (await founder.get(f"{API_PREFIX}/expenses/settlement")).json()["payments"] == []
    assert (await founder.get(f"{API_PREFIX}/expenses")).json()["items"] == []


async def test_archiving_writes_a_feed_entry(founder: AsyncClient, db_session: Session) -> None:
    await record(founder)

    period = (await founder.post(f"{API_PREFIX}/expenses/archive")).json()

    event = db_session.scalars(select(FeedEvent).order_by(FeedEvent.id.desc())).first()
    assert event is not None
    assert event.type == FeedEventType.SETTLEMENT_ARCHIVED
    assert event.reference_type == "settlement_period"
    assert event.reference_id == period["id"]


async def test_only_admins_archive(founder: AsyncClient, housemate: AsyncClient) -> None:
    await record(founder)

    response = await housemate.post(f"{API_PREFIX}/expenses/archive")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.FORBIDDEN


async def test_archiving_an_empty_period_is_refused(founder: AsyncClient) -> None:
    response = await founder.post(f"{API_PREFIX}/expenses/archive")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.CONFLICT


async def test_a_new_expense_starts_the_next_period(
    founder: AsyncClient, housemate: AsyncClient
) -> None:
    await record(founder, title="Alte Periode")
    first = (await founder.post(f"{API_PREFIX}/expenses/archive")).json()

    await record(founder, title="Neue Periode", amount_cents=2000)
    second = (await founder.post(f"{API_PREFIX}/expenses/archive")).json()

    assert first["id"] != second["id"]
    assert second["total_cents"] == 2000
    unchanged = (await founder.get(f"{API_PREFIX}/expenses/periods/{first['id']}")).json()
    assert unchanged["total_cents"] == 1000
    assert [item["title"] for item in unchanged["expenses"]] == ["Alte Periode"]


# --- The archive -------------------------------------------------------------------


async def test_the_archive_lists_the_periods_newest_first(founder: AsyncClient) -> None:
    await record(founder, title="Erste")
    first = (await founder.post(f"{API_PREFIX}/expenses/archive")).json()
    await record(founder, title="Zweite", amount_cents=500)
    second = (await founder.post(f"{API_PREFIX}/expenses/archive")).json()

    rows = (await founder.get(f"{API_PREFIX}/expenses/periods")).json()

    assert [row["id"] for row in rows] == [second["id"], first["id"]]
    assert [row["total_cents"] for row in rows] == [500, 1000]


async def test_a_period_of_another_household_does_not_exist(
    founder: AsyncClient, second_client: AsyncClient
) -> None:
    await record(founder)
    period = (await founder.post(f"{API_PREFIX}/expenses/archive")).json()
    await second_client.post(f"{API_PREFIX}/household", json=HOUSEHOLD | {"name": "Andere WG"})

    response = await second_client.get(f"{API_PREFIX}/expenses/periods/{period['id']}")

    assert response.status_code == 404
    assert (await second_client.get(f"{API_PREFIX}/expenses/periods")).json() == []


async def test_an_unknown_period_is_not_found(founder: AsyncClient) -> None:
    assert (await founder.get(f"{API_PREFIX}/expenses/periods/9999")).status_code == 404


async def test_the_settlement_needs_a_household_and_a_sign_in(
    client: AsyncClient, app: FastAPI
) -> None:
    await client.get(f"{API_PREFIX}/meta")
    assert (await client.get(f"{API_PREFIX}/expenses/balances")).status_code == 401

    await sign_up(client)
    for path in ("/expenses/balances", "/expenses/settlement", "/expenses/periods"):
        assert (await client.get(f"{API_PREFIX}{path}")).status_code == 404
    assert (await client.post(f"{API_PREFIX}/expenses/archive")).status_code == 404


async def test_somebody_can_pay_for_others_without_sharing_the_cost(
    founder: AsyncClient, housemate: AsyncClient, third_client: AsyncClient, db_session: Session
) -> None:
    """Chris lays out the money at the checkout but keeps nothing: A and B owe it all.

    Payer and participants are independent — that is what makes "I'll get this, pay me
    back" work at all.
    """
    alex, bea, chris = list(db_session.scalars(select(User.id).order_by(User.id)))

    expense = await record(
        third_client, title="Regal", amount_cents=10000, participants=[alex, bea]
    )

    assert expense["paid_by_id"] == chris
    assert [share["share_cents"] for share in expense["shares"]] == [5000, 5000]

    balances = (await founder.get(f"{API_PREFIX}/expenses/balances")).json()
    assert {row["user_id"]: row["balance_cents"] for row in balances} == {
        alex: -5000,
        bea: -5000,
        chris: 10000,
    }
    payments = (await founder.get(f"{API_PREFIX}/expenses/settlement")).json()["payments"]
    assert payments == [
        {"from_user_id": alex, "to_user_id": chris, "amount_cents": 5000},
        {"from_user_id": bea, "to_user_id": chris, "amount_cents": 5000},
    ]
