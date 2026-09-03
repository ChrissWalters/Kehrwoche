"""Transport security: the certificate an instance serves and when it is replaced."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path

import pytest
from cryptography import x509
from fastapi import FastAPI
from httpx import AsyncClient

from app.config import Settings
from app.errors import ErrorCode
from app.main import API_PREFIX
from app.security import TlsError, ensure_certificate
from app.tasks import watch_certificate
from tests.test_auth import REGISTRATION
from tests.test_household import HOUSEHOLD, sign_up

# --- Transport security ---------------------------------------------------------------


def test_a_self_signed_certificate_names_hosts_and_addresses(tmp_path: Path) -> None:
    """Acceptance case: a valid certificate with the right subject alternative names."""
    settings = Settings(
        database_url="sqlite:///:memory:",
        data_dir=tmp_path,
        tls_mode="self-signed",
        external_hostnames="kehrwoche.local, 192.168.1.20",
    )

    certificate_file, key_file = ensure_certificate(settings)

    certificate = x509.load_pem_x509_certificate(certificate_file.read_bytes())
    names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert names.get_values_for_type(x509.DNSName) == ["kehrwoche.local"]
    assert [str(address) for address in names.get_values_for_type(x509.IPAddress)] == [
        "192.168.1.20"
    ], "a home network is reached by address as often as by name"
    assert (certificate.not_valid_after_utc - certificate.not_valid_before_utc).days == 3650
    assert oct(key_file.stat().st_mode)[-3:] == "600", "the key stays private"


def test_a_second_start_keeps_the_certificate(tmp_path: Path) -> None:
    """Otherwise every restart would ask every device to confirm a new warning."""
    settings = Settings(
        database_url="sqlite:///:memory:", data_dir=tmp_path, tls_mode="self-signed"
    )

    first, _ = ensure_certificate(settings)
    serial = x509.load_pem_x509_certificate(first.read_bytes()).serial_number
    second, _ = ensure_certificate(settings)

    assert x509.load_pem_x509_certificate(second.read_bytes()).serial_number == serial


def test_tls_off_needs_no_certificate(tmp_path: Path) -> None:
    settings = Settings(database_url="sqlite:///:memory:", data_dir=tmp_path, tls_mode="off")

    assert ensure_certificate(settings) is None
    assert not (tmp_path / "tls").exists()


def test_a_custom_certificate_is_used_as_it_is(tmp_path: Path) -> None:
    own = Settings(database_url="sqlite:///:memory:", data_dir=tmp_path, tls_mode="self-signed")
    certificate_file, key_file = ensure_certificate(own)

    settings = Settings(
        database_url="sqlite:///:memory:",
        data_dir=tmp_path,
        tls_mode="custom",
        tls_cert_file=certificate_file,
        tls_key_file=key_file,
    )

    assert ensure_certificate(settings) == (certificate_file, key_file)


def test_a_missing_custom_certificate_is_reported(tmp_path: Path) -> None:
    """Better a clear message at start-up than a server that answers nothing."""
    settings = Settings(
        database_url="sqlite:///:memory:",
        data_dir=tmp_path,
        tls_mode="custom",
        tls_cert_file=tmp_path / "nowhere.crt",
        tls_key_file=tmp_path / "nowhere.key",
    )

    with pytest.raises(TlsError):
        ensure_certificate(settings)


async def test_a_replaced_certificate_asks_for_a_restart(tmp_path: Path) -> None:
    """A renewed certificate only reaches the clients after a restart — so it restarts."""
    own = Settings(database_url="sqlite:///:memory:", data_dir=tmp_path, tls_mode="self-signed")
    certificate_file, key_file = ensure_certificate(own)
    settings = Settings(
        database_url="sqlite:///:memory:",
        data_dir=tmp_path,
        tls_mode="custom",
        tls_cert_file=certificate_file,
        tls_key_file=key_file,
    )
    restarts: list[bool] = []

    async def replace_after_a_moment() -> None:
        await asyncio.sleep(0.05)
        certificate_file.write_bytes(certificate_file.read_bytes() + b"\n")

    watcher = asyncio.create_task(
        watch_certificate(settings, interval_seconds=0, on_change=lambda: restarts.append(True))
    )
    await replace_after_a_moment()
    await asyncio.sleep(0.05)
    watcher.cancel()
    with suppress(asyncio.CancelledError):
        await watcher

    assert restarts == [True]


async def test_an_untouched_certificate_keeps_the_server_running(tmp_path: Path) -> None:
    own = Settings(database_url="sqlite:///:memory:", data_dir=tmp_path, tls_mode="self-signed")
    certificate_file, key_file = ensure_certificate(own)
    settings = Settings(
        database_url="sqlite:///:memory:",
        data_dir=tmp_path,
        tls_mode="custom",
        tls_cert_file=certificate_file,
        tls_key_file=key_file,
    )
    restarts: list[bool] = []

    watcher = asyncio.create_task(
        watch_certificate(settings, interval_seconds=0, on_change=lambda: restarts.append(True))
    )
    await asyncio.sleep(0.05)
    watcher.cancel()
    with suppress(asyncio.CancelledError):
        await watcher

    assert restarts == []


# --- Security headers -------------------------------------------------------------------


async def test_every_answer_carries_the_security_headers(client: AsyncClient) -> None:
    """Including errors and static files — a header set only sometimes is worthless."""
    for response in (
        await client.get(f"{API_PREFIX}/meta"),
        await client.get(f"{API_PREFIX}/nothing-here"),
        await client.get("/js/main.js"),
        await client.get("/"),
    ):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "same-origin"
        assert "default-src 'self'" in response.headers["content-security-policy"]


async def test_the_policy_leaves_no_room_for_inline_code(client: AsyncClient) -> None:
    """The reason the frontend is written as render functions in the first place."""
    policy = (await client.get("/")).headers["content-security-policy"]

    assert "unsafe-inline" not in policy
    assert "unsafe-eval" not in policy
    assert "script-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy


# --- The API reference ------------------------------------------------------------------


async def test_the_openapi_document_is_for_admins(client: AsyncClient, app: FastAPI) -> None:
    """The reference is for admins of a household, not for the open internet."""
    assert (await client.get(f"{API_PREFIX}/openapi.json")).status_code == 401

    await sign_up(client)
    assert (await client.get(f"{API_PREFIX}/openapi.json")).status_code == 404, "no household"

    await client.post(f"{API_PREFIX}/household", json=HOUSEHOLD)
    document = await client.get(f"{API_PREFIX}/openapi.json")
    assert document.status_code == 200
    assert document.json()["info"]["title"] == "Kehrwoche"


# --- A closed instance ------------------------------------------------------------------


async def test_a_closed_instance_refuses_registration(app: FastAPI, settings: Settings) -> None:
    from httpx import ASGITransport

    from app.main import create_app
    from tests.conftest import CsrfAwareClient

    closed = create_app(settings.model_copy(update={"registration_open": False}))
    closed.dependency_overrides = app.dependency_overrides
    transport = ASGITransport(app=closed, raise_app_exceptions=False)

    async with CsrfAwareClient(transport=transport, base_url="https://testserver") as client:
        meta = await client.get(f"{API_PREFIX}/meta")
        response = await client.post(f"{API_PREFIX}/auth/register", json=REGISTRATION)

    assert meta.json()["registration_open"] is False, "the sign-up form hides itself"
    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.FORBIDDEN
