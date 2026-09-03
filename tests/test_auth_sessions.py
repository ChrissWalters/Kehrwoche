"""CSRF protection, session management and the forced password change."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import CurrentUser
from app.errors import ErrorCode
from app.main import API_PREFIX
from app.models import Session as AuthSession
from app.models import User
from app.models.base import utcnow
from app.security import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    verify_password,
)
from tests.test_auth import CREDENTIALS, REGISTRATION

NEW_PASSWORD = "ein-ganz-neues-langes"


@pytest.fixture
async def signed_in(client: AsyncClient) -> AsyncClient:
    await client.post(f"{API_PREFIX}/auth/register", json=REGISTRATION)
    await client.post(f"{API_PREFIX}/auth/login", json=CREDENTIALS)
    return client


@pytest.fixture
async def bare_client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """A client that does *not* mirror the CSRF cookie, i.e. a cross-site request."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://testserver") as client:
        yield client


async def test_safe_request_hands_out_a_csrf_cookie(client: AsyncClient) -> None:
    response = await client.get(f"{API_PREFIX}/meta")

    assert response.status_code == 200
    assert client.cookies.get(CSRF_COOKIE_NAME)
    # Readable on purpose: the client has to mirror it into the header.
    cookies = [c for c in response.headers.get_list("set-cookie") if CSRF_COOKIE_NAME in c]
    assert all("HttpOnly" not in cookie for cookie in cookies)


async def test_write_without_csrf_header_is_forbidden(bare_client: AsyncClient) -> None:
    await bare_client.get(f"{API_PREFIX}/meta")

    response = await bare_client.post(f"{API_PREFIX}/auth/logout")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.CSRF_FAILED


async def test_write_with_wrong_csrf_header_is_forbidden(bare_client: AsyncClient) -> None:
    await bare_client.get(f"{API_PREFIX}/meta")

    response = await bare_client.post(
        f"{API_PREFIX}/auth/logout", headers={CSRF_HEADER_NAME: "not-the-cookie"}
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.CSRF_FAILED


async def test_login_and_register_do_not_need_a_token(bare_client: AsyncClient) -> None:
    created = await bare_client.post(f"{API_PREFIX}/auth/register", json=REGISTRATION)
    signed_in = await bare_client.post(f"{API_PREFIX}/auth/login", json=CREDENTIALS)

    assert created.status_code == 201
    assert signed_in.status_code == 200


async def test_login_rotates_the_csrf_token(client: AsyncClient) -> None:
    before = client.cookies[CSRF_COOKIE_NAME]
    await client.post(f"{API_PREFIX}/auth/register", json=REGISTRATION)

    await client.post(f"{API_PREFIX}/auth/login", json=CREDENTIALS)

    assert client.cookies[CSRF_COOKIE_NAME] != before


async def test_session_list_shows_devices_and_marks_the_current_one(
    signed_in: AsyncClient,
) -> None:
    response = await signed_in.get(f"{API_PREFIX}/auth/sessions")

    assert response.status_code == 200
    sessions = response.json()
    assert len(sessions) == 1
    assert sessions[0]["current"] is True
    assert sessions[0]["user_agent"]
    assert sessions[0]["last_seen_at"] and sessions[0]["expires_at"]


async def test_revoking_another_session_signs_that_device_out(
    signed_in: AsyncClient, app: FastAPI, db_session: Session
) -> None:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://testserver") as second_device:
        await second_device.post(f"{API_PREFIX}/auth/login", json=CREDENTIALS)
        assert (await second_device.get(f"{API_PREFIX}/me")).status_code == 200

        sessions = (await signed_in.get(f"{API_PREFIX}/auth/sessions")).json()
        other = next(session for session in sessions if not session["current"])
        revoked = await signed_in.delete(f"{API_PREFIX}/auth/sessions/{other['id']}")

        assert revoked.status_code == 204
        assert (await second_device.get(f"{API_PREFIX}/me")).status_code == 401
        assert (await signed_in.get(f"{API_PREFIX}/me")).status_code == 200


async def test_revoking_a_foreign_session_is_not_found(
    signed_in: AsyncClient, app: FastAPI, db_session: Session
) -> None:
    other = User(username="bea@example.org", password_hash="x", first_name="Bea")
    db_session.add(other)
    db_session.flush()
    foreign = AuthSession(
        token_hash="b" * 64,
        user_id=other.id,
        last_seen_at=utcnow(),
        expires_at=utcnow() + timedelta(days=1),
    )
    db_session.add(foreign)
    db_session.commit()

    response = await signed_in.delete(f"{API_PREFIX}/auth/sessions/{foreign.id}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == ErrorCode.NOT_FOUND


async def test_sessions_require_a_signed_in_person(client: AsyncClient) -> None:
    assert (await client.get(f"{API_PREFIX}/auth/sessions")).status_code == 401


async def test_password_change_signs_out_other_devices(
    signed_in: AsyncClient, app: FastAPI, db_session: Session
) -> None:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://testserver") as second_device:
        await second_device.post(f"{API_PREFIX}/auth/login", json=CREDENTIALS)

        changed = await signed_in.post(
            f"{API_PREFIX}/auth/change-password",
            json={"current_password": CREDENTIALS["password"], "new_password": NEW_PASSWORD},
        )

        assert changed.status_code == 204
        assert (await second_device.get(f"{API_PREFIX}/me")).status_code == 401
        # The device that changed the password stays signed in.
        assert (await signed_in.get(f"{API_PREFIX}/me")).status_code == 200

    user = db_session.scalar(select(User))
    assert user is not None
    assert verify_password(user.password_hash, NEW_PASSWORD)


async def test_password_change_needs_the_current_password(signed_in: AsyncClient) -> None:
    response = await signed_in.post(
        f"{API_PREFIX}/auth/change-password",
        json={"current_password": "falsch-aber-lang", "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == ErrorCode.INVALID_CREDENTIALS
    assert response.json()["error"]["field"] == "current_password"


async def test_password_change_enforces_the_policy(signed_in: AsyncClient) -> None:
    response = await signed_in.post(
        f"{API_PREFIX}/auth/change-password",
        json={"current_password": CREDENTIALS["password"], "new_password": "1234567890"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == ErrorCode.WEAK_PASSWORD
    assert response.json()["error"]["field"] == "new_password"


async def test_forced_password_change_blocks_business_routes(
    signed_in: AsyncClient, app: FastAPI, db_session: Session
) -> None:
    @app.get("/probe/business")
    def _probe_business(current_user: CurrentUser) -> dict[str, int]:
        return {"id": current_user.id}

    assert (await signed_in.get("/probe/business")).status_code == 200

    user = db_session.scalar(select(User))
    assert user is not None
    user.must_change_password = True
    db_session.commit()

    blocked = await signed_in.get("/probe/business")

    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == ErrorCode.PASSWORD_CHANGE_REQUIRED
    # The account endpoints stay reachable so the password can actually be changed.
    assert (await signed_in.get(f"{API_PREFIX}/me")).status_code == 200

    changed = await signed_in.post(
        f"{API_PREFIX}/auth/change-password",
        json={"current_password": CREDENTIALS["password"], "new_password": NEW_PASSWORD},
    )
    assert changed.status_code == 204
    assert (await signed_in.get("/probe/business")).status_code == 200


async def test_logout_clears_both_cookies(signed_in: AsyncClient) -> None:
    response = await signed_in.post(f"{API_PREFIX}/auth/logout")

    assert response.status_code == 204
    removals = response.headers.get_list("set-cookie")
    assert any(SESSION_COOKIE_NAME in cookie and "Max-Age=0" in cookie for cookie in removals)
    assert any(CSRF_COOKIE_NAME in cookie and "Max-Age=0" in cookie for cookie in removals)
