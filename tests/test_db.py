"""Engine construction and the session dependency."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import (
    Base,
    create_db_engine,
    dispose_engine,
    get_db,
    get_engine,
    get_session_factory,
    is_sqlite,
)


@pytest.fixture(autouse=True)
def _clear_engine_cache() -> None:
    dispose_engine()


def test_is_sqlite_recognises_the_backend() -> None:
    assert is_sqlite("sqlite:////data/kehrwoche.db")
    assert not is_sqlite("mysql+pymysql://user:pw@db/kehrwoche")
    assert not is_sqlite("postgresql+psycopg://user:pw@db/kehrwoche")


def test_sqlite_engine_creates_the_data_directory(tmp_path: Path) -> None:
    database_file = tmp_path / "nested" / "kehrwoche.db"

    engine = create_db_engine(Settings(database_url=f"sqlite+pysqlite:///{database_file}"))
    try:
        with engine.connect() as connection:
            assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
            assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
    finally:
        engine.dispose()

    assert database_file.parent.is_dir()


def test_in_memory_engine_enforces_foreign_keys_without_wal() -> None:
    engine = create_db_engine(Settings(database_url="sqlite+pysqlite:///:memory:"))
    try:
        with engine.connect() as connection:
            assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
            assert connection.execute(text("PRAGMA journal_mode")).scalar() == "memory"
    finally:
        engine.dispose()


def test_engine_and_session_factory_are_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        assert get_engine() is get_engine()
        assert get_session_factory() is get_session_factory()
    finally:
        dispose_engine()
        get_settings.cache_clear()


def test_get_db_yields_a_usable_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        sessions = get_db()
        session = next(sessions)
        assert isinstance(session, Session)
        assert session.connection().closed is False
        with pytest.raises(StopIteration):
            next(sessions)
    finally:
        dispose_engine()
        get_settings.cache_clear()


def test_metadata_uses_explicit_constraint_names() -> None:
    assert Base.metadata.naming_convention["pk"] == "pk_%(table_name)s"


def test_test_fixture_database_has_every_table(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())

    # A shared CI database may also carry Alembic's version table, which is not a model.
    assert set(Base.metadata.tables) <= tables
