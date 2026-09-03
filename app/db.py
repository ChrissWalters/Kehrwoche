"""Database engine, session factory and the request-scoped session dependency.

The engine is built solely from ``DATABASE_URL`` so that SQLite, MariaDB/MySQL and
PostgreSQL are interchangeable without code changes. Anything dialect specific lives
here — models, services and routers stay dialect agnostic.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import suppress
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, MetaData, create_engine, event, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import Settings, get_settings

#: Explicit constraint names keep migrations portable: SQLite and MariaDB otherwise
#: invent different names, which makes a later ``DROP CONSTRAINT`` unpredictable.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

#: Isolation level for the server-backed dialects, set explicitly so all three behave
#: alike. PostgreSQL already defaults to this; MySQL/MariaDB default to REPEATABLE READ,
#: where a session keeps serving the snapshot it started with and misses commits made by
#: other requests in the meantime.
ISOLATION_LEVEL = "READ COMMITTED"


class Base(DeclarativeBase):
    """Declarative base for all models (see ``app/models/``)."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def is_sqlite(url: str) -> bool:
    return make_url(url).get_backend_name() == "sqlite"


def sqlite_file(url: str) -> Path | None:
    """Return the database file of a file-backed SQLite URL, ``None`` for in-memory."""
    database = make_url(url).database
    if not database or database == ":memory:":
        return None
    return Path(database)


def apply_sqlite_pragmas(engine: Engine, *, wal: bool = True) -> None:
    """Enforce foreign keys and enable WAL — neither is a SQLite default."""

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection: Any, _: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        if wal:
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


def create_db_engine(settings: Settings | None = None) -> Engine:
    settings = settings or get_settings()
    url = settings.database_url

    if is_sqlite(url):
        database_file = sqlite_file(url)
        if database_file is not None:
            # Best effort: if the directory cannot be created, connecting fails later
            # with a message that names the actual path.
            with suppress(OSError):
                database_file.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            pool_pre_ping=True,
            future=True,
        )
        apply_sqlite_pragmas(engine, wal=database_file is not None)
        return engine

    return create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=1800,
        isolation_level=ISOLATION_LEVEL,
        future=True,
    )


@lru_cache
def get_engine() -> Engine:
    return create_db_engine()


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed afterwards."""
    with get_session_factory()() as session:
        yield session


def dispose_engine() -> None:
    """Drop the cached engine — used by tests and by the admin CLI."""
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
