"""System endpoints and the unified error format."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app import __version__
from app.config import Settings
from app.db import get_db
from app.errors import AppError, ErrorCode
from app.main import API_PREFIX


class _Probe(BaseModel):
    """Payload used only to provoke a validation error in the tests below."""

    title: str = Field(min_length=1)


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    """The shared app plus a few routes that trigger each error handler."""

    @app.post("/probe/validation")
    async def _probe_validation(payload: _Probe) -> dict[str, str]:
        return {"title": payload.title}

    @app.get("/probe/conflict")
    async def _probe_conflict() -> None:
        raise AppError(409, ErrorCode.CONFLICT, "Already there.", field="join_code")

    @app.get("/probe/boom")
    async def _probe_boom() -> None:
        raise RuntimeError("secret internal detail")

    return app


async def test_health_reports_ok(client: AsyncClient) -> None:
    response = await client.get(f"{API_PREFIX}/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_reports_an_unreachable_database(
    app: FastAPI, client: AsyncClient, tmp_path
) -> None:
    unreachable = create_engine(f"sqlite+pysqlite:///{tmp_path}/missing-dir/db.sqlite")
    broken_factory = sessionmaker(bind=unreachable, future=True)

    def broken_db() -> Iterator[Session]:
        with broken_factory() as session:
            yield session

    app.dependency_overrides[get_db] = broken_db

    response = await client.get(f"{API_PREFIX}/health")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == ErrorCode.SERVICE_UNAVAILABLE


async def test_meta_reports_version_languages_and_currency(client: AsyncClient) -> None:
    response = await client.get(f"{API_PREFIX}/meta")

    assert response.status_code == 200
    assert response.json() == {
        "version": __version__,
        "languages": ["de", "en"],
        "default_currency": "EUR",
        # The test instance runs without TLS, and the interface is told so.
        "insecure_transport": True,
        "registration_open": True,
    }


async def test_unknown_route_uses_the_error_format(client: AsyncClient) -> None:
    response = await client.get(f"{API_PREFIX}/does-not-exist")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == ErrorCode.NOT_FOUND
    assert error["message"]


async def test_validation_error_names_the_offending_field(client: AsyncClient) -> None:
    response = await client.post("/probe/validation", json={"title": ""})

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == ErrorCode.VALIDATION_ERROR
    assert error["field"] == "title"
    assert error["message"]


async def test_missing_body_is_a_validation_error(client: AsyncClient) -> None:
    response = await client.post("/probe/validation")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR


async def test_app_error_keeps_code_and_field(client: AsyncClient) -> None:
    response = await client.get("/probe/conflict")

    assert response.status_code == 409
    assert response.json() == {
        "error": {"code": "conflict", "message": "Already there.", "field": "join_code"}
    }


async def test_unexpected_error_does_not_leak_details(client: AsyncClient) -> None:
    response = await client.get("/probe/boom")

    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == ErrorCode.INTERNAL_ERROR
    assert "secret internal detail" not in error["message"]


async def test_wrong_method_uses_the_error_format(client: AsyncClient) -> None:
    response = await client.post(f"{API_PREFIX}/health")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == ErrorCode.METHOD_NOT_ALLOWED


def test_port_default_depends_on_tls_mode() -> None:
    assert Settings(tls_mode="self-signed").port == 8443
    assert Settings(tls_mode="off").port == 8080
    assert Settings(tls_mode="off", port=9000).port == 9000


def test_external_hostnames_are_split() -> None:
    settings = Settings(external_hostnames="kehrwoche.local, 192.168.1.10 ,")

    assert settings.hostnames == ["kehrwoche.local", "192.168.1.10"]
