"""The sliding window limiter and its use on the authentication endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.errors import AppError, ErrorCode
from app.main import API_PREFIX
from app.security import LOGIN_RATE_LIMIT, RateLimit, RateLimiter
from tests.test_auth import CREDENTIALS, REGISTRATION

LIMIT = RateLimit(max_attempts=5, window_seconds=300, block_seconds=60, max_block_seconds=600)


class FakeClock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def limiter(clock: FakeClock) -> RateLimiter:
    return RateLimiter(LIMIT, clock)


def test_attempts_below_the_limit_stay_allowed(limiter: RateLimiter) -> None:
    for _ in range(LIMIT.max_attempts - 1):
        limiter.check("key")
        limiter.record("key")

    limiter.check("key")


def test_the_attempt_after_the_limit_is_blocked(limiter: RateLimiter) -> None:
    for _ in range(LIMIT.max_attempts):
        limiter.record("key")

    with pytest.raises(AppError) as raised:
        limiter.check("key")

    assert raised.value.status_code == 429
    assert raised.value.code == ErrorCode.RATE_LIMITED
    assert raised.value.headers == {"Retry-After": "60"}


def test_the_block_expires(limiter: RateLimiter, clock: FakeClock) -> None:
    for _ in range(LIMIT.max_attempts):
        limiter.record("key")

    clock.advance(LIMIT.block_seconds)

    limiter.check("key")
    assert limiter.retry_after("key") == 0


def test_repeated_violations_double_the_block(limiter: RateLimiter, clock: FakeClock) -> None:
    blocks = []
    for _ in range(3):
        for _ in range(LIMIT.max_attempts):
            limiter.record("key")
        blocks.append(limiter.retry_after("key"))
        clock.advance(blocks[-1])

    assert blocks == [60, 120, 240]


def test_the_block_is_capped(limiter: RateLimiter, clock: FakeClock) -> None:
    for _ in range(6):
        for _ in range(LIMIT.max_attempts):
            limiter.record("key")
        clock.advance(limiter.retry_after("key"))

    assert limiter.retry_after("key") == 0
    for _ in range(LIMIT.max_attempts):
        limiter.record("key")
    assert limiter.retry_after("key") == LIMIT.max_block_seconds


def test_attempts_outside_the_window_do_not_count(limiter: RateLimiter, clock: FakeClock) -> None:
    for _ in range(LIMIT.max_attempts - 1):
        limiter.record("key")

    clock.advance(LIMIT.window_seconds + 1)
    limiter.record("key")

    limiter.check("key")


def test_reset_forgets_the_key(limiter: RateLimiter) -> None:
    for _ in range(LIMIT.max_attempts):
        limiter.record("key")

    limiter.reset("key")

    limiter.check("key")


def test_keys_are_independent(limiter: RateLimiter) -> None:
    for _ in range(LIMIT.max_attempts):
        limiter.record("one")

    limiter.check("two")


def test_production_login_limit_matches_the_specification() -> None:
    assert LOGIN_RATE_LIMIT.max_attempts == 5
    assert LOGIN_RATE_LIMIT.block_seconds < LOGIN_RATE_LIMIT.max_block_seconds


async def test_sixth_failed_login_is_rate_limited(client: AsyncClient) -> None:
    await client.post(f"{API_PREFIX}/auth/register", json=REGISTRATION)
    wrong = CREDENTIALS | {"password": "immer-noch-falsch"}

    for _ in range(LOGIN_RATE_LIMIT.max_attempts):
        assert (await client.post(f"{API_PREFIX}/auth/login", json=wrong)).status_code == 401

    blocked = await client.post(f"{API_PREFIX}/auth/login", json=wrong)

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == ErrorCode.RATE_LIMITED
    assert int(blocked.headers["Retry-After"]) > 0
    # Even the correct password has to wait now.
    assert (await client.post(f"{API_PREFIX}/auth/login", json=CREDENTIALS)).status_code == 429


async def test_a_successful_login_clears_the_counter(client: AsyncClient) -> None:
    await client.post(f"{API_PREFIX}/auth/register", json=REGISTRATION)
    wrong = CREDENTIALS | {"password": "immer-noch-falsch"}

    for _ in range(LOGIN_RATE_LIMIT.max_attempts - 1):
        await client.post(f"{API_PREFIX}/auth/login", json=wrong)
    assert (await client.post(f"{API_PREFIX}/auth/login", json=CREDENTIALS)).status_code == 200

    for _ in range(LOGIN_RATE_LIMIT.max_attempts - 1):
        assert (await client.post(f"{API_PREFIX}/auth/login", json=wrong)).status_code == 401


async def test_registration_is_rate_limited_per_source(client: AsyncClient) -> None:
    for index in range(5):
        response = await client.post(
            f"{API_PREFIX}/auth/register",
            json=REGISTRATION | {"username": f"person{index}@example.org"},
        )
        assert response.status_code == 201

    blocked = await client.post(
        f"{API_PREFIX}/auth/register", json=REGISTRATION | {"username": "person6@example.org"}
    )

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == ErrorCode.RATE_LIMITED
    assert "Retry-After" in blocked.headers
