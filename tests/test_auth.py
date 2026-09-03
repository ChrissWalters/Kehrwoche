"""Registration, sign-in, sessions and the password policy."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.errors import ErrorCode
from app.main import API_PREFIX
from app.models import Session as AuthSession
from app.models import User
from app.models.base import utcnow
from app.security import SESSION_COOKIE_NAME, hash_session_token, verify_password

CREDENTIALS = {"username": "alex", "password": "korrekt-pferd-batterie"}
REGISTRATION = CREDENTIALS | {"first_name": "Alex", "last_name": "Berg", "locale": "de"}


async def register(client: AsyncClient, **overrides: object) -> dict:
    response = await client.post(f"{API_PREFIX}/auth/register", json=REGISTRATION | overrides)
    return response.json()


async def login(client: AsyncClient, **overrides: object) -> dict:
    response = await client.post(f"{API_PREFIX}/auth/login", json=CREDENTIALS | overrides)
    return response.json()


async def test_register_login_me_roundtrip(client: AsyncClient) -> None:
    created = await client.post(f"{API_PREFIX}/auth/register", json=REGISTRATION)
    assert created.status_code == 201
    assert created.json()["username"] == "alex"
    # Nobody has to hand over an address to use Kehrwoche.
    assert created.json()["email"] is None
    assert created.json()["household_id"] is None
    assert CREDENTIALS["password"] not in created.text
    assert "password_hash" not in created.text

    signed_in = await client.post(f"{API_PREFIX}/auth/login", json=CREDENTIALS)
    assert signed_in.status_code == 200
    assert SESSION_COOKIE_NAME in signed_in.cookies

    profile = await client.get(f"{API_PREFIX}/me")
    assert profile.status_code == 200
    assert profile.json() == created.json()


async def test_password_is_stored_as_an_argon2id_hash(
    client: AsyncClient, db_session: Session
) -> None:
    await register(client)

    user = db_session.scalar(select(User))
    assert user is not None
    assert user.password_hash.startswith("$argon2id$")
    assert CREDENTIALS["password"] not in user.password_hash
    assert verify_password(user.password_hash, CREDENTIALS["password"])


async def test_login_name_is_normalised_and_unique(client: AsyncClient) -> None:
    await register(client)

    duplicate = await client.post(
        f"{API_PREFIX}/auth/register", json=REGISTRATION | {"username": "  ALEX  "}
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == ErrorCode.USERNAME_TAKEN
    assert duplicate.json()["error"]["field"] == "username"


async def test_an_email_address_is_optional(client: AsyncClient, db_session: Session) -> None:
    created = await client.post(
        f"{API_PREFIX}/auth/register", json=REGISTRATION | {"email": "alex@wg.example"}
    )

    assert created.status_code == 201
    assert created.json()["email"] == "alex@wg.example"


async def test_the_same_address_cannot_be_claimed_twice(client: AsyncClient) -> None:
    await register(client, email="shared@wg.example")

    duplicate = await client.post(
        f"{API_PREFIX}/auth/register",
        json=REGISTRATION | {"username": "bea", "email": "SHARED@wg.example"},
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == ErrorCode.EMAIL_TAKEN
    assert duplicate.json()["error"]["field"] == "email"


@pytest.mark.parametrize(
    "password",
    ["1234567890", "kurz", "password123", "PASSWORD123"],
)
async def test_weak_passwords_are_rejected(client: AsyncClient, password: str) -> None:
    response = await client.post(
        f"{API_PREFIX}/auth/register", json=REGISTRATION | {"password": password}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == ErrorCode.WEAK_PASSWORD
    assert response.json()["error"]["field"] == "password"


async def test_any_login_name_is_accepted(client: AsyncClient) -> None:
    """The login name is freely chosen — "alex", "alex@wg" and "Küchen-Chef" all work."""
    response = await client.post(
        f"{API_PREFIX}/auth/register", json=REGISTRATION | {"username": "Küchen-Chef"}
    )

    assert response.status_code == 201
    assert response.json()["username"] == "küchen-chef"


async def test_empty_login_name_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        f"{API_PREFIX}/auth/register", json=REGISTRATION | {"username": ""}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR
    assert response.json()["error"]["field"] == "username"


async def test_email_validation_can_be_switched_on(
    app: FastAPI, session_factory: sessionmaker[Session], tmp_path
) -> None:
    from httpx import ASGITransport
    from httpx import AsyncClient as Client

    from app.db import get_db
    from app.main import create_app

    strict_app = create_app(Settings(data_dir=tmp_path, tls_mode="off", email_validation=True))
    strict_app.dependency_overrides[get_db] = app.dependency_overrides[get_db]

    transport = ASGITransport(app=strict_app, raise_app_exceptions=False)
    async with Client(transport=transport, base_url="https://testserver") as client:
        # The setting now guards the optional address, never the login name.
        rejected = await client.post(
            f"{API_PREFIX}/auth/register", json=REGISTRATION | {"email": "not-an-email"}
        )
        accepted = await client.post(
            f"{API_PREFIX}/auth/register", json=REGISTRATION | {"email": "alex@wg.example"}
        )

    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR
    assert rejected.json()["error"]["field"] == "email"
    assert accepted.status_code == 201


async def test_wrong_password_is_unauthorised(client: AsyncClient) -> None:
    await register(client)

    response = await client.post(
        f"{API_PREFIX}/auth/login", json=CREDENTIALS | {"password": "falsch-aber-lang"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == ErrorCode.INVALID_CREDENTIALS
    assert SESSION_COOKIE_NAME not in response.cookies


async def test_unknown_account_looks_exactly_like_a_wrong_password(client: AsyncClient) -> None:
    response = await client.post(
        f"{API_PREFIX}/auth/login", json=CREDENTIALS | {"username": "nobody@example.org"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == ErrorCode.INVALID_CREDENTIALS


async def test_locked_account_cannot_sign_in(client: AsyncClient, db_session: Session) -> None:
    await register(client)
    user = db_session.scalar(select(User))
    assert user is not None
    user.is_active = False
    db_session.commit()

    response = await client.post(f"{API_PREFIX}/auth/login", json=CREDENTIALS)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.ACCOUNT_INACTIVE


async def test_me_without_session_is_unauthorised(client: AsyncClient) -> None:
    response = await client.get(f"{API_PREFIX}/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == ErrorCode.NOT_AUTHENTICATED


async def test_only_the_token_hash_reaches_the_database(
    client: AsyncClient, db_session: Session
) -> None:
    await register(client)
    response = await client.post(f"{API_PREFIX}/auth/login", json=CREDENTIALS)
    token = response.cookies[SESSION_COOKIE_NAME]

    session = db_session.scalar(select(AuthSession))
    assert session is not None
    assert session.token_hash == hash_session_token(token)
    assert token not in session.token_hash
    assert session.user_agent is not None


async def test_session_cookie_flags_follow_the_tls_mode(client: AsyncClient) -> None:
    await register(client)
    response = await client.post(f"{API_PREFIX}/auth/login", json=CREDENTIALS)

    cookie = response.headers["set-cookie"]
    assert "httponly" in cookie.lower()
    assert "samesite=lax" in cookie.lower()
    # The test app runs with TLS_MODE=off, where Secure would break the cookie.
    assert "secure" not in cookie.lower()


async def test_session_cookie_is_secure_when_tls_is_on(
    app: FastAPI, session_factory: sessionmaker[Session], tmp_path
) -> None:
    from httpx import ASGITransport
    from httpx import AsyncClient as Client

    from app.db import get_db
    from app.main import create_app

    tls_app = create_app(Settings(data_dir=tmp_path, tls_mode="self-signed"))
    tls_app.dependency_overrides[get_db] = app.dependency_overrides[get_db]

    transport = ASGITransport(app=tls_app, raise_app_exceptions=False)
    async with Client(transport=transport, base_url="https://testserver") as client:
        await register(client)
        response = await client.post(f"{API_PREFIX}/auth/login", json=CREDENTIALS)

    assert "secure" in response.headers["set-cookie"].lower()


async def test_expired_session_is_rejected_and_the_cookie_removed(
    client: AsyncClient, db_session: Session
) -> None:
    await register(client)
    await client.post(f"{API_PREFIX}/auth/login", json=CREDENTIALS)

    session = db_session.scalar(select(AuthSession))
    assert session is not None
    session.expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()

    response = await client.get(f"{API_PREFIX}/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == ErrorCode.NOT_AUTHENTICATED
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert db_session.scalar(select(AuthSession)) is None


async def test_activity_pushes_the_expiry_window_forward(
    client: AsyncClient, db_session: Session, settings: Settings
) -> None:
    await register(client)
    await client.post(f"{API_PREFIX}/auth/login", json=CREDENTIALS)

    session = db_session.scalar(select(AuthSession))
    assert session is not None
    seen_before = utcnow() - timedelta(days=5)
    session.last_seen_at = seen_before
    session.expires_at = utcnow() + timedelta(days=1)
    db_session.commit()

    await client.get(f"{API_PREFIX}/me")
    db_session.expire_all()

    session = db_session.scalar(select(AuthSession))
    assert session is not None
    assert session.last_seen_at > seen_before
    assert session.expires_at > utcnow() + timedelta(days=settings.session_max_age_days - 1)


async def test_unknown_token_is_rejected(client: AsyncClient) -> None:
    client.cookies.set(SESSION_COOKIE_NAME, "made-up-token")

    response = await client.get(f"{API_PREFIX}/me")

    assert response.status_code == 401


async def test_logout_removes_session_and_cookie(client: AsyncClient, db_session: Session) -> None:
    await register(client)
    await client.post(f"{API_PREFIX}/auth/login", json=CREDENTIALS)

    response = await client.post(f"{API_PREFIX}/auth/logout")

    assert response.status_code == 204
    assert db_session.scalar(select(AuthSession)) is None
    assert (await client.get(f"{API_PREFIX}/me")).status_code == 401


async def test_logout_without_session_is_accepted(client: AsyncClient) -> None:
    response = await client.post(f"{API_PREFIX}/auth/logout")

    assert response.status_code == 204
