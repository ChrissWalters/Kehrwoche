"""Language catalogues: the shipped ones plus anything mounted at runtime.

Self-hosters can add or override translations without rebuilding the image: a JSON file
in the extra directory wins over the bundled one, key by key, and a file for a language
that is not shipped simply adds that language.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.config import Settings

logger = logging.getLogger(__name__)

BUNDLED_LOCALES_DIR = Path(__file__).parent / "locales"
#: Every catalogue falls back to this one, key by key, on the client.
FALLBACK_LOCALE = "en"

_cache: dict[tuple[str, str], dict[str, Any]] = {}


def clear_cache() -> None:
    """Drop the parsed catalogues — used by the tests and after a locale change."""
    _cache.clear()


def merge_catalogues(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursive merge in which ``override`` wins.

    Nested sections are merged rather than replaced, so an extra file may override a
    single key without having to repeat the whole section.
    """
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = merge_catalogues(current, value)
        else:
            merged[key] = value
    return merged


def _read_catalogue(path: Path) -> dict[str, Any]:
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Ignoring unreadable language file %s", path)
        return {}
    return content if isinstance(content, dict) else {}


def _locale_codes(directory: Path) -> set[str]:
    if not directory.is_dir():
        return set()
    return {path.stem for path in directory.glob("*.json") if path.is_file()}


def available_locales(settings: Settings) -> list[str]:
    """All language codes on offer, bundled and mounted."""
    return sorted(_locale_codes(BUNDLED_LOCALES_DIR) | _locale_codes(settings.locales_extra_dir))


def load_catalogue(code: str, settings: Settings) -> dict[str, Any] | None:
    """The catalogue of one language, or ``None`` if that language does not exist."""
    cache_key = (code, str(settings.locales_extra_dir))
    if cache_key in _cache:
        return _cache[cache_key]

    bundled = BUNDLED_LOCALES_DIR / f"{code}.json"
    extra = settings.locales_extra_dir / f"{code}.json"
    if not bundled.is_file() and not extra.is_file():
        return None

    catalogue: dict[str, Any] = {}
    if bundled.is_file():
        catalogue = _read_catalogue(bundled)
    if extra.is_file():
        # The volume wins — that is the whole point of mounting it.
        catalogue = merge_catalogues(catalogue, _read_catalogue(extra))

    _cache[cache_key] = catalogue
    return catalogue
