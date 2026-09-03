"""Shared fixtures: a fresh database per test and a client bound to it.

By default every test runs against its own SQLite in-memory database. Setting
``TEST_DATABASE_URL`` points the same suite at PostgreSQL — that is how CI
proves that the schema stays independent of the dialect.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import (
    models,  # noqa: F401  — populates Base.metadata before create_all
    security,
)
from app.config import Settings
from app.db import ISOLATION_LEVEL, Base, apply_sqlite_pragmas, get_db, is_sqlite
from app.main import API_PREFIX, create_app
from app.security import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, CSRF_SAFE_METHODS

SQLITE_MEMORY_URL = "sqlite+pysqlite:///:memory:"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", SQLITE_MEMORY_URL)


@pytest.fixture(autouse=True)
def cheap_password_hashing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hash with minimal cost during tests.

    The production parameters take about half a second per hash by design, which would
    dominate the runtime of the suite. Verifying the algorithm choice itself is the job
    of the dedicated security tests, not of every fixture that needs an account.
    """
    monkeypatch.setattr(
        security, "_password_hasher", PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    )


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(database_url=TEST_DATABASE_URL, data_dir=tmp_path, tls_mode="off")


@pytest.fixture
def engine() -> Iterator[Engine]:
    """A database with all tables, emptied again after the test."""
    if is_sqlite(TEST_DATABASE_URL):
        # StaticPool keeps the one in-memory database alive across connections.
        engine = create_engine(
            TEST_DATABASE_URL,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
        apply_sqlite_pragmas(engine, wal=False)
    else:
        # Same isolation level as the application, so a test session sees what the app
        # committed through its own session.
        engine = create_engine(
            TEST_DATABASE_URL,
            pool_pre_ping=True,
            isolation_level=ISOLATION_LEVEL,
            future=True,
        )

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@pytest.fixture
def db_session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as session:
        yield session


@pytest.fixture
def app(settings: Settings, session_factory: sessionmaker[Session]) -> FastAPI:
    app = create_app(settings)

    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    return app


class CsrfAwareClient(AsyncClient):
    """Mirrors the CSRF cookie into the header, exactly like ``js/api.js`` will."""

    async def request(self, method: str, url: object, **kwargs: object) -> object:
        token = self.cookies.get(CSRF_COOKIE_NAME)
        if token and method.upper() not in CSRF_SAFE_METHODS:
            headers = dict(kwargs.pop("headers", None) or {})
            headers.setdefault(CSRF_HEADER_NAME, token)
            kwargs["headers"] = headers
        return await super().request(method, url, **kwargs)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with CsrfAwareClient(transport=transport, base_url="https://testserver") as client:
        # A browser picks the CSRF cookie up with the first page load; one safe request
        # puts the test client into the same state.
        await client.get(f"{API_PREFIX}/meta")
        yield client
