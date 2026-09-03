"""Every entity can be written and read back, and the relationships navigate both ways."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from app.models import (
    Chore,
    ChoreCompletion,
    Comment,
    Expense,
    ExpenseShare,
    FeedEvent,
    FeedEventType,
    Household,
    HouseholdType,
    Like,
    Notification,
    NotificationType,
    SettlementPayment,
    SettlementPeriod,
    ShoppingItem,
    User,
    UserRole,
)
from app.models import Session as UserSession
from app.models.base import utcnow


def make_household(db: Session, join_code: str = "ABCDEFGHJKMN") -> Household:
    household = Household(name="Wohnung 3b", type=HouseholdType.WG, join_code=join_code)
    db.add(household)
    db.flush()
    return household


def make_user(db: Session, household: Household | None = None, username: str = "alex") -> User:
    user = User(
        username=username,
        password_hash="argon2-hash",
        first_name="Alex",
        household=household,
        role=UserRole.ADMIN if household else UserRole.MEMBER,
    )
    db.add(user)
    db.flush()
    return user


def test_household_and_membership_navigate_both_ways(db_session: Session) -> None:
    household = make_household(db_session)
    user = make_user(db_session, household)
    db_session.commit()
    db_session.expire_all()

    assert user.household is household
    assert household.members == [user]
    assert user.is_admin
    assert household.currency == "EUR"
    assert household.type is HouseholdType.WG
    # Enums are stored as their lowercase value, not as the member name.
    stored = db_session.connection().exec_driver_sql(
        "SELECT households.type, users.role FROM households, users"
    )
    assert stored.one() == ("wg", "admin")


def test_user_without_household_is_allowed(db_session: Session) -> None:
    user = make_user(db_session)
    db_session.commit()

    assert user.household_id is None
    assert user.role is UserRole.MEMBER
    assert user.points == 0
    assert user.is_active is True
    assert user.must_change_password is False


def test_timestamps_are_utc_aware_and_updated_on_change(db_session: Session) -> None:
    household = make_household(db_session)
    db_session.commit()
    created_at, updated_at = household.created_at, household.updated_at

    assert created_at.tzinfo is not None
    assert created_at.utcoffset() == timedelta(0)

    household.name = "Wohnung 4a"
    db_session.commit()

    assert household.created_at == created_at
    assert household.updated_at >= updated_at


def test_naive_datetimes_are_rejected(db_session: Session) -> None:
    household = make_household(db_session)
    user = make_user(db_session, household)
    chore = Chore(household=household, title="Bad putzen", due_at=datetime(2026, 8, 2, 12, 0))
    db_session.add(chore)

    with pytest.raises(StatementError):
        db_session.flush()

    db_session.rollback()
    assert user.username


def test_datetimes_are_returned_as_utc(db_session: Session) -> None:
    household = make_household(db_session)
    berlin_noon = datetime(2026, 8, 2, 14, 0, tzinfo=UTC).astimezone()
    chore = Chore(household=household, title="Müll rausbringen", due_at=berlin_noon)
    db_session.add(chore)
    db_session.commit()
    db_session.expire_all()

    assert chore.due_at == datetime(2026, 8, 2, 14, 0, tzinfo=UTC)
    assert chore.due_at.tzinfo is not None


def test_chore_keeps_its_rotation_list_and_completions(db_session: Session) -> None:
    household = make_household(db_session)
    first = make_user(db_session, household, "first")
    second = make_user(db_session, household, "second")
    chore = Chore(
        household=household,
        title="Bad putzen",
        description="inkl. Spiegel",
        points=2,
        rotation_seconds=7 * 24 * 3600,
        fixed=True,
        member_order=[first.id, second.id],
        current_user=first,
        due_at=utcnow(),
    )
    db_session.add(chore)
    db_session.flush()

    completion = ChoreCompletion(chore=chore, user=first, done_at=utcnow(), points_awarded=2)
    db_session.add(completion)
    db_session.commit()
    db_session.expire_all()

    assert chore.member_order == [first.id, second.id]
    assert chore.current_user is first
    assert chore.completions == [completion]
    assert completion.chore is chore
    assert household.chores == [chore]


def test_shopping_item_tracks_inserter_and_buyer(db_session: Session) -> None:
    household = make_household(db_session)
    inserter = make_user(db_session, household, "inserter")
    buyer = make_user(db_session, household, "buyer")
    item = ShoppingItem(
        household=household,
        name="Milch",
        note="1,5 %",
        priority=True,
        inserter=inserter,
        buyer=buyer,
        bought=True,
        bought_at=utcnow(),
    )
    db_session.add(item)
    db_session.commit()
    db_session.expire_all()

    assert item.inserter is inserter
    assert item.buyer is buyer
    assert household.shopping_items == [item]


def test_expense_shares_and_period_archive(db_session: Session) -> None:
    household = make_household(db_session)
    payer = make_user(db_session, household, "payer")
    other = make_user(db_session, household, "other")

    expense = Expense(
        household=household,
        title="Wocheneinkauf",
        amount_cents=1000,
        paid_by=payer,
        spent_at=date(2026, 8, 1),
        shares=[
            ExpenseShare(user=payer, share_cents=500),
            ExpenseShare(user=other, share_cents=500),
        ],
    )
    db_session.add(expense)
    db_session.flush()

    period = SettlementPeriod(household=household, closed_at=utcnow(), closed_by=payer)
    period.payments.append(SettlementPayment(from_user=other, to_user=payer, amount_cents=500))
    expense.period = period
    db_session.add(period)
    db_session.commit()
    db_session.expire_all()

    assert sum(share.share_cents for share in expense.shares) == expense.amount_cents
    assert expense.period is period
    assert period.expenses == [expense]
    assert period.payments[0].to_user is payer
    assert household.settlement_periods == [period]


def test_feed_event_with_comment_and_like(db_session: Session) -> None:
    household = make_household(db_session)
    author = make_user(db_session, household, "author")
    event = FeedEvent(
        household=household,
        type=FeedEventType.USER_POST,
        actor=author,
        body="Wer hat den Schlüssel?",
    )
    event.comments.append(Comment(author=author, body="Ich!"))
    event.likes.append(Like(user=author))
    db_session.add(event)
    db_session.commit()
    db_session.expire_all()

    assert event.type == FeedEventType.USER_POST
    assert event.comments[0].event is event
    assert event.likes[0].user is author
    assert household.feed_events == [event]


def test_notification_stores_keys_and_parameters(db_session: Session) -> None:
    household = make_household(db_session)
    user = make_user(db_session, household)
    notification = Notification(
        user=user,
        type=NotificationType.CHORE_DUE,
        title_key="notification.chore_due.title",
        body_key="notification.chore_due.body",
        params={"chore": "Bad putzen"},
        reference_type="chore",
        reference_id=1,
    )
    db_session.add(notification)
    db_session.commit()
    db_session.expire_all()

    assert notification.params == {"chore": "Bad putzen"}
    assert notification.read_at is None
    assert user.notifications == [notification]


def test_session_belongs_to_its_user(db_session: Session) -> None:
    user = make_user(db_session)
    now = utcnow()
    session = UserSession(
        token_hash="a" * 64,
        user=user,
        last_seen_at=now,
        expires_at=now + timedelta(days=30),
        user_agent="Firefox",
    )
    db_session.add(session)
    db_session.commit()
    db_session.expire_all()

    assert user.sessions == [session]
    assert session.user is user


def test_username_is_unique(db_session: Session) -> None:
    make_user(db_session, username="taken")
    db_session.commit()

    db_session.add(User(username="taken", password_hash="x", first_name="Bea"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_an_email_address_is_optional_but_unique(db_session: Session) -> None:
    """Two accounts without an address are fine; two with the same one are not."""
    make_user(db_session, username="one")
    make_user(db_session, username="two")
    db_session.commit()

    db_session.add(
        User(username="three", password_hash="x", first_name="Cem", email="a@wg.example")
    )
    db_session.add(
        User(username="four", password_hash="x", first_name="Dana", email="a@wg.example")
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_join_code_is_unique(db_session: Session) -> None:
    make_household(db_session, "CODECODECOD1")
    db_session.commit()

    with pytest.raises(IntegrityError):
        make_household(db_session, "CODECODECOD1")


def test_like_is_unique_per_event_and_user(db_session: Session) -> None:
    household = make_household(db_session)
    user = make_user(db_session, household)
    event = FeedEvent(household=household, type=FeedEventType.USER_POST, actor=user)
    db_session.add(event)
    db_session.flush()
    db_session.add(Like(event=event, user=user))
    db_session.commit()

    db_session.add(Like(event=event, user=user))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_foreign_keys_are_enforced(db_session: Session) -> None:
    db_session.add(Chore(household_id=404, title="Ghost"))

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_deleting_a_household_removes_its_data(db_session: Session) -> None:
    household = make_household(db_session)
    user = make_user(db_session, household)
    db_session.add(Chore(household=household, title="Bad putzen"))
    db_session.add(ShoppingItem(household=household, name="Milch", inserter=user))
    db_session.commit()

    db_session.delete(household)
    db_session.commit()
    db_session.expire_all()

    assert db_session.query(Chore).count() == 0
    assert db_session.query(ShoppingItem).count() == 0
    # The account survives its household and falls back to the "without household" state.
    assert user.household_id is None
