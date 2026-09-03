"""The instance administration: accounts, households, backup — and the CLI around it."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from app.cli import cli
from app.config import Settings
from app.db import Base
from app.main import API_PREFIX
from app.models import Chore, Household, HouseholdType, User, UserRole
from app.models import Session as AuthSession
from app.models.base import utcnow
from app.security import hash_password, verify_password
from app.services import admin as admin_service
from app.services.admin import AdminError
from tests.test_auth import CREDENTIALS, REGISTRATION
from tests.test_household import HOUSEHOLD, sign_up

runner = CliRunner()


def make_user(db: Session, username: str = "alex", household: Household | None = None) -> User:
    user = User(
        username=username,
        password_hash=hash_password("Ein-gutes-Passwort-2026"),
        first_name="Alex",
        household_id=household.id if household else None,
        role=UserRole.ADMIN if household else UserRole.MEMBER,
    )
    db.add(user)
    db.commit()
    return user


def make_household(db: Session, name: str = "WG Sonnenblick") -> Household:
    household = Household(name=name, type=HouseholdType.WG, join_code="AAAABBBBCCCC")
    db.add(household)
    db.commit()
    return household


# --- One-time passwords ---------------------------------------------------------------


def test_a_one_time_password_can_be_read_out_loud() -> None:
    password = admin_service.generate_one_time_password()

    assert len(password) == 19, "four groups of four plus the dashes"
    assert password.count("-") == 3
    # No character that turns into another one over the phone.
    assert not set(password) & set("0Oo1Il")


def test_two_one_time_passwords_differ() -> None:
    assert admin_service.generate_one_time_password() != admin_service.generate_one_time_password()


# --- Accounts ---------------------------------------------------------------------------


def test_resetting_hands_out_a_working_password(db_session: Session) -> None:
    user = make_user(db_session)

    password = admin_service.reset_password(db_session, "alex")

    db_session.refresh(user)
    assert verify_password(user.password_hash, password)
    assert user.must_change_password is True


def test_resetting_signs_every_device_out(db_session: Session) -> None:
    """Whoever needs a reset has lost control of the account — the sessions go too."""
    user = make_user(db_session)
    db_session.add(
        AuthSession(
            user_id=user.id,
            token_hash="x" * 64,
            last_seen_at=user.created_at,
            expires_at=user.created_at,
        )
    )
    db_session.commit()

    admin_service.reset_password(db_session, "alex")

    assert db_session.scalars(select(AuthSession)).all() == []


def test_locking_ends_the_running_sessions(db_session: Session) -> None:
    user = make_user(db_session)
    db_session.add(
        AuthSession(
            user_id=user.id,
            token_hash="y" * 64,
            last_seen_at=user.created_at,
            expires_at=user.created_at,
        )
    )
    db_session.commit()

    admin_service.set_active(db_session, "alex", active=False)

    assert user.is_active is False
    assert db_session.scalars(select(AuthSession)).all() == []

    admin_service.set_active(db_session, "alex", active=True)
    assert user.is_active is True


def test_an_unknown_login_name_is_an_error(db_session: Session) -> None:
    with pytest.raises(AdminError):
        admin_service.reset_password(db_session, "nobody")


def test_the_user_list_shows_household_and_state(db_session: Session) -> None:
    household = make_household(db_session)
    make_user(db_session, household=household)
    admin_service.set_active(db_session, "alex", active=False)

    rows = admin_service.list_users(db_session)

    assert [row.username for row in rows] == ["alex"]
    assert rows[0].household == "WG Sonnenblick"
    assert rows[0].active is False


# --- Households ---------------------------------------------------------------------------


def test_deleting_a_household_keeps_the_accounts(db_session: Session) -> None:
    household = make_household(db_session)
    user = make_user(db_session, household=household)
    db_session.add(
        Chore(household_id=household.id, title="Bad", rotation_seconds=-1, member_order=[user.id])
    )
    db_session.commit()

    name = admin_service.delete_household(db_session, household.id)

    assert name == "WG Sonnenblick"
    assert db_session.scalars(select(Household)).all() == []
    assert db_session.scalars(select(Chore)).all() == [], "its data goes with it"
    assert db_session.scalar(select(User)) is not None, "the person stays"


def test_deleting_an_unknown_household_is_an_error(db_session: Session) -> None:
    with pytest.raises(AdminError):
        admin_service.delete_household(db_session, 999)


# --- Backup -------------------------------------------------------------------------------


@pytest.fixture
def file_database(tmp_path: Path) -> Iterator[tuple[Settings, Engine]]:
    """A real SQLite file, because a backup of an in-memory database proves nothing."""
    path = tmp_path / "kehrwoche.db"
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{path}", data_dir=tmp_path, tls_mode="off"
    )
    engine = create_engine(settings.database_url, future=True)
    Base.metadata.create_all(engine)
    try:
        yield settings, engine
    finally:
        engine.dispose()


def test_the_backup_is_a_usable_database(
    file_database: tuple[Settings, Engine], tmp_path: Path
) -> None:
    settings, engine = file_database
    with sessionmaker(bind=engine)() as db:
        make_household(db)

    written = admin_service.backup_database(settings, tmp_path / "backup.db")

    copy = sqlite3.connect(written)
    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert copy.execute("SELECT name FROM households").fetchone()[0] == "WG Sonnenblick"
    copy.close()


def test_the_backup_may_name_a_directory(
    file_database: tuple[Settings, Engine], tmp_path: Path
) -> None:
    settings, _ = file_database
    target = tmp_path / "backups"
    target.mkdir()

    written = admin_service.backup_database(settings, target)

    assert written.parent == target
    assert written.is_file()


def test_an_external_database_gets_a_hint_instead_of_a_file(tmp_path: Path) -> None:
    """A file that only looks like a backup would be worse than none."""
    settings = Settings(
        database_url="postgresql+psycopg://user:pw@localhost/kehrwoche",
        data_dir=tmp_path,
        tls_mode="off",
    )

    with pytest.raises(AdminError) as error:
        admin_service.backup_database(settings, tmp_path / "backup.db")

    assert "pg_dump" in str(error.value)


# --- The command line ---------------------------------------------------------------------


@pytest.fixture
def instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the CLI at a database of its own, the way the container does."""
    from app.config import get_settings
    from app.db import dispose_engine

    path = tmp_path / "kehrwoche.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{path}")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    dispose_engine()

    engine = create_engine(f"sqlite+pysqlite:///{path}", future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        household = make_household(db)
        make_user(db, household=household)
    engine.dispose()

    try:
        yield path
    finally:
        get_settings.cache_clear()
        dispose_engine()


def test_the_cli_lists_users_and_households(instance: Path) -> None:
    users = runner.invoke(cli, ["user", "list"])
    households = runner.invoke(cli, ["household", "list"])

    assert users.exit_code == 0
    assert "alex" in users.stdout
    assert households.exit_code == 0
    assert "WG Sonnenblick" in households.stdout


def test_the_cli_resets_a_password(instance: Path) -> None:
    result = runner.invoke(cli, ["user", "reset-password", "alex"])

    assert result.exit_code == 0
    assert "One-time password" in result.stdout


def test_the_cli_reports_an_unknown_account(instance: Path) -> None:
    result = runner.invoke(cli, ["user", "reset-password", "nobody"])

    assert result.exit_code == 1


def test_the_cli_asks_before_deleting_a_household(instance: Path) -> None:
    refused = runner.invoke(cli, ["household", "delete", "1"], input="n\n")
    assert refused.exit_code != 0, "aborted on purpose"

    confirmed = runner.invoke(cli, ["household", "delete", "1"], input="y\n")
    assert confirmed.exit_code == 0
    assert runner.invoke(cli, ["household", "list"]).stdout.strip() == "No households yet."


def test_the_cli_can_skip_the_question(instance: Path) -> None:
    result = runner.invoke(cli, ["household", "delete", "1", "--yes"])

    assert result.exit_code == 0
    assert "deleted" in result.stdout


def test_the_cli_writes_a_backup(instance: Path, tmp_path: Path) -> None:
    result = runner.invoke(cli, ["backup", str(tmp_path / "copy.db")])

    assert result.exit_code == 0
    assert (tmp_path / "copy.db").is_file()


# --- End to end -----------------------------------------------------------------------------


async def test_a_reset_password_carries_all_the_way_into_the_app(
    client: AsyncClient, db_session: Session
) -> None:
    """The acceptance case: CLI resets, sign-in works once, then a change is required."""
    await sign_up(client)
    await client.post(f"{API_PREFIX}/household", json=HOUSEHOLD)

    one_time = admin_service.reset_password(db_session, CREDENTIALS["username"])

    # The old session is gone with the old password.
    assert (await client.get(f"{API_PREFIX}/me")).status_code == 401

    signed_in = await client.post(
        f"{API_PREFIX}/auth/login", json=CREDENTIALS | {"password": one_time}
    )
    assert signed_in.status_code == 200
    assert signed_in.json()["must_change_password"] is True

    # Signed in, but the app stays closed until the password is changed.
    assert (await client.get(f"{API_PREFIX}/chores")).status_code == 403

    changed = await client.post(
        f"{API_PREFIX}/auth/change-password",
        json={"current_password": one_time, "new_password": REGISTRATION["password"]},
    )
    assert changed.status_code in (200, 204)

    # And from here on everything is normal again.
    assert (await client.get(f"{API_PREFIX}/chores")).status_code == 200


async def test_a_locked_account_cannot_sign_in(client: AsyncClient, db_session: Session) -> None:
    await sign_up(client)

    admin_service.set_active(db_session, CREDENTIALS["username"], active=False)

    response = await client.post(f"{API_PREFIX}/auth/login", json=CREDENTIALS)
    assert response.status_code == 403


# --- Completing an erasure by hand ------------------------------------------------------


def waiting_account(db: Session, household: Household, username: str = "bea") -> User:
    """Somebody who deleted their account while their share was still open."""
    from app.models import Expense, ExpenseShare

    # The payer's name has to be unique per call — several households in one test.
    payer = make_user(db, f"payer-{username}", household)
    leaver = make_user(db, username, household)
    expense = Expense(
        household_id=household.id,
        title="Einkauf",
        amount_cents=2000,
        paid_by_id=payer.id,
        spent_at=payer.created_at.date(),
    )
    db.add(expense)
    db.flush()
    db.add_all(
        [
            ExpenseShare(expense_id=expense.id, user_id=payer.id, share_cents=1000),
            ExpenseShare(expense_id=expense.id, user_id=leaver.id, share_cents=1000),
        ]
    )
    leaver.household_id = None
    leaver.is_active = False
    leaver.erasure_requested_at = utcnow()
    db.commit()
    return leaver


def test_one_account_can_be_released_from_the_wait(db_session: Session) -> None:
    household = make_household(db_session)
    leaver = waiting_account(db_session, household)

    from app.services.users import erase_now

    erase_now(db_session, leaver)

    db_session.refresh(leaver)
    assert leaver.first_name == ""
    assert leaver.username.startswith("deleted-")
    assert leaver.erasure_requested_at is None


def test_the_scope_of_a_household_leaves_the_others_alone(db_session: Session) -> None:
    """An objection in one household is no reason to empty another one's books."""
    from app.services.users import pending_erasures

    first = make_household(db_session, "WG Eins")
    waiting_account(db_session, first, "bea")
    second = Household(name="WG Zwei", type=HouseholdType.WG, join_code="DDDDEEEEFFFF")
    db_session.add(second)
    db_session.commit()
    waiting_account(db_session, second, "chris")

    scoped = pending_erasures(db_session, household_id=first.id)

    assert [user.username for user in scoped] == ["bea"]
    assert len(pending_erasures(db_session)) == 2, "unscoped still sees both"


def test_the_cli_erases_one_account(instance: Path) -> None:
    from app.config import get_settings
    from app.db import get_session_factory

    with get_session_factory()() as db:
        household = db.scalar(select(Household))
        assert household is not None
        waiting_account(db, household, "bea")

    result = runner.invoke(cli, ["user", "erase", "bea"])

    assert result.exit_code == 0
    assert "name and login name removed" in result.stdout
    with get_session_factory()() as db:
        assert db.scalar(select(User).where(User.username == "bea")) is None
    get_settings.cache_clear()


def test_the_cli_refuses_to_guess_the_scope(instance: Path) -> None:
    """Neither argument nor --all: the default must never be "everybody"."""
    nothing = runner.invoke(cli, ["user", "erase"])
    both = runner.invoke(cli, ["user", "erase", "bea", "--all"])

    assert nothing.exit_code == 1
    assert both.exit_code == 1


def test_the_cli_says_when_there_is_nothing_to_erase(instance: Path) -> None:
    result = runner.invoke(cli, ["user", "erase", "alex"])

    assert result.exit_code == 1, "silence would look like success"


def test_the_cli_asks_before_erasing_everything(instance: Path) -> None:
    from app.db import get_session_factory

    with get_session_factory()() as db:
        household = db.scalar(select(Household))
        assert household is not None
        waiting_account(db, household, "bea")

    refused = runner.invoke(cli, ["user", "erase", "--all"], input="n\n")
    assert refused.exit_code != 0

    confirmed = runner.invoke(cli, ["user", "erase", "--all"], input="y\n")
    assert confirmed.exit_code == 0
    assert "waiting" in confirmed.stdout
