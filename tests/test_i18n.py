"""Language catalogues: bundled files, the extra volume and the endpoint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.config import Settings
from app.errors import ErrorCode
from app.i18n import (
    BUNDLED_LOCALES_DIR,
    FALLBACK_LOCALE,
    available_locales,
    clear_cache,
    load_catalogue,
    merge_catalogues,
)
from app.main import API_PREFIX
from app.security import MIN_PASSWORD_LENGTH


@pytest.fixture(autouse=True)
def _fresh_catalogue_cache() -> None:
    clear_cache()


@pytest.fixture
def extra_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "locales-extra"
    directory.mkdir()
    return directory


def write_locale(directory: Path, code: str, content: dict) -> None:
    (directory / f"{code}.json").write_text(json.dumps(content), encoding="utf-8")


def test_merge_is_recursive_and_the_override_wins() -> None:
    base = {"a": "base", "section": {"keep": "yes", "replace": "old"}}
    override = {"a": "over", "section": {"replace": "new"}, "added": "x"}

    merged = merge_catalogues(base, override)

    assert merged == {
        "a": "over",
        "section": {"keep": "yes", "replace": "new"},
        "added": "x",
    }
    assert base["a"] == "base", "the input must not be modified"


def test_bundled_languages_are_offered(tmp_path: Path) -> None:
    settings = Settings(locales_extra_dir=tmp_path / "missing")

    assert available_locales(settings) == ["de", "en"]


def test_extra_file_overrides_a_single_key(extra_dir: Path) -> None:
    write_locale(extra_dir, "de", {"nav.chores": "Putzdienst"})
    settings = Settings(locales_extra_dir=extra_dir)

    catalogue = load_catalogue("de", settings)

    assert catalogue is not None
    assert catalogue["nav.chores"] == "Putzdienst"
    # Everything not mentioned stays as shipped.
    assert catalogue["nav.shopping"] == "Einkauf"


def test_extra_file_adds_a_whole_language(extra_dir: Path) -> None:
    write_locale(extra_dir, "fr", {"nav.chores": "Ménage"})
    settings = Settings(locales_extra_dir=extra_dir)

    assert available_locales(settings) == ["de", "en", "fr"]
    catalogue = load_catalogue("fr", settings)
    assert catalogue == {"nav.chores": "Ménage"}


def test_unknown_language_has_no_catalogue(tmp_path: Path) -> None:
    settings = Settings(locales_extra_dir=tmp_path)

    assert load_catalogue("xx", settings) is None


def test_broken_language_file_is_ignored(extra_dir: Path) -> None:
    (extra_dir / "de.json").write_text("{not json", encoding="utf-8")
    settings = Settings(locales_extra_dir=extra_dir)

    catalogue = load_catalogue("de", settings)

    assert catalogue is not None
    assert catalogue["nav.chores"] == "Putzplan"


def test_shipped_languages_have_the_same_keys() -> None:
    catalogues = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in BUNDLED_LOCALES_DIR.glob("*.json")
    }
    reference = set(catalogues[FALLBACK_LOCALE])

    for code, catalogue in catalogues.items():
        assert set(catalogue) == reference, f"{code} differs from {FALLBACK_LOCALE}"


async def test_endpoint_serves_a_catalogue(client: AsyncClient) -> None:
    response = await client.get(f"{API_PREFIX}/locales/de")

    assert response.status_code == 200
    assert response.json()["nav.chores"] == "Putzplan"


async def test_endpoint_needs_no_session(client: AsyncClient) -> None:
    """The sign-in view has to be readable before anybody is signed in."""
    response = await client.get(f"{API_PREFIX}/locales/en")

    assert response.status_code == 200
    assert response.json()["auth.login.submit"] == "Sign in"


async def test_endpoint_reports_an_unknown_language(client: AsyncClient) -> None:
    response = await client.get(f"{API_PREFIX}/locales/xx")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == ErrorCode.NOT_FOUND
    assert response.json()["error"]["field"] == "code"


async def test_meta_lists_the_languages(client: AsyncClient) -> None:
    response = await client.get(f"{API_PREFIX}/meta")

    assert response.json()["languages"] == ["de", "en"]


async def test_a_third_language_is_offered_and_falls_back(
    settings: Settings, extra_dir: Path
) -> None:
    """The volume promise of AP12, checked the way an operator would use it.

    Five French keys in the volume: French appears in the picker and carries exactly
    those five. Everything else is not the server's job — the client falls back to
    English (see `i18n.js`), which is why nothing else has to be delivered here.
    """
    (extra_dir / "fr.json").write_text(
        json.dumps(
            {
                "language.name": "Français",
                "nav.chores": "Ménage",
                "nav.shopping": "Courses",
                "nav.expenses": "Caisse",
                "app.name": "Kehrwoche",
            }
        ),
        encoding="utf-8",
    )

    from httpx import ASGITransport

    from app.main import create_app

    instance = create_app(settings.model_copy(update={"locales_extra_dir": extra_dir}))
    transport = ASGITransport(app=instance, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://testserver") as client:
        offered = (await client.get(f"{API_PREFIX}/meta")).json()["languages"]
        french = (await client.get(f"{API_PREFIX}/locales/fr")).json()

    assert offered == ["de", "en", "fr"]
    assert french["nav.chores"] == "Ménage"
    assert len(french) == 5, "a partial language stays partial; the client fills the rest"
    assert "nav.feed" not in french


async def test_an_error_carries_the_key_that_translates_it(client: AsyncClient) -> None:
    """The wire is what the client works from — the key has to survive the trip."""
    response = await client.get(f"{API_PREFIX}/locales/xx")

    assert response.json()["error"]["message_key"] == "error.locale.unknown"


async def test_a_key_can_bring_its_own_placeholders(client: AsyncClient) -> None:
    """`params` travels alongside so the sentence is built in the reader's language."""
    response = await client.post(
        f"{API_PREFIX}/auth/register",
        json={"username": "mira", "password": "short", "first_name": "Mira"},
    )

    error = response.json()["error"]
    assert error["message_key"] == "error.password.too_short"
    assert error["params"] == {"count": MIN_PASSWORD_LENGTH}


async def test_a_plain_failure_travels_without_a_key(client: AsyncClient) -> None:
    """Where the code already says everything, no second wording is invented."""
    await client.post(
        f"{API_PREFIX}/auth/register",
        json={"username": "mira", "password": "correct horse battery", "first_name": "Mira"},
    )
    response = await client.post(
        f"{API_PREFIX}/auth/register",
        json={"username": "mira", "password": "correct horse battery", "first_name": "Mira"},
    )

    assert response.json()["error"]["code"] == ErrorCode.USERNAME_TAKEN
    assert "message_key" not in response.json()["error"]
