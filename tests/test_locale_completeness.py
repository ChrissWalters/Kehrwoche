"""The language files against the code: nothing missing, nothing left over.

Texts are the one part of this application that no other test can reach — a missing key
shows up as a raw key on somebody's phone, a leftover key rots quietly until a translator
wastes an evening on it. Both directions are checked here, and both are in CI.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.main import STATIC_DIR

LOCALES_DIR = STATIC_DIR.parent / "locales"
SHIPPED_LANGUAGES = ("de", "en")

#: Not text but structure: the template and suggestion lists, and the language's own name.
STRUCTURAL_KEYS = {"chore_templates", "shopping_suggestions", "language.name"}

#: Anything dotted in quotes. Broad on purpose — used only to ask "is this catalogue key
#: mentioned anywhere at all", where a stray file name cannot cause a false alarm.
KEY_LITERAL = re.compile(r"""["'`]([a-z][\w]*(?:\.[\w]+)+)["'`]""")
#: The argument list of a `t(...)` call, allowing one level of nesting inside it. This is
#: the narrow view: only what really reaches the translator counts as a key.
T_CALL = re.compile(r"\bt\(((?:[^()]|\([^()]*\))*)\)", re.S)
#: Keys handed to `t()` elsewhere: route labels and the link targets of the pinboard.
LABEL = re.compile(r"""\b(?:labelKey|label)\s*:\s*["']([a-z][\w]*(?:\.[\w]+)+)["']""")
#: A key assembled at runtime — `feed.event.${type}` or `chores.interval.every_${unit}`.
KEY_PREFIX = re.compile(r"""["'`]([a-z][\w]*(?:\.[\w]+)*[\w.]*?)\$\{""")
#: The same on the Python side, where notification keys are built with f-strings.
PYTHON_PREFIX = re.compile(r"""f["']([a-z][\w]*(?:\.[\w]+)*\.)\{""")


def catalogue(code: str) -> dict[str, object]:
    return json.loads((LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))


def sources() -> list[Path]:
    javascript = [
        path
        for path in STATIC_DIR.rglob("*.js")
        # The vendored library is none of our business, and `i18n.js` documents the very
        # call this test looks for — its example is not a usage.
        if "vendor" not in path.parts and path.name != "i18n.js"
    ]
    return javascript + list(Path("app").rglob("*.py"))


def referenced() -> tuple[set[str], set[str]]:
    """Every key the code hands to `t()`, and every prefix it builds keys from."""
    keys: set[str] = set()
    prefixes: set[str] = set()
    for path in sources():
        source = path.read_text(encoding="utf-8")
        for arguments in T_CALL.findall(source):
            keys |= set(KEY_LITERAL.findall(arguments))
        keys |= set(LABEL.findall(source))
        prefixes |= set(KEY_PREFIX.findall(source))
        prefixes |= set(PYTHON_PREFIX.findall(source))
    return keys, prefixes


def mentioned() -> set[str]:
    """Every dotted literal anywhere in the code — the wide net for orphan hunting."""
    found: set[str] = set()
    for path in sources():
        found |= set(KEY_LITERAL.findall(path.read_text(encoding="utf-8")))
    return found


def test_the_shipped_languages_carry_the_same_keys() -> None:
    german, english = catalogue("de"), catalogue("en")

    assert set(german) - set(english) == set(), "only in de"
    assert set(english) - set(german) == set(), "only in en"


@pytest.mark.parametrize("code", SHIPPED_LANGUAGES)
def test_no_text_is_left_empty(code: str) -> None:
    """A key with an empty value is worse than a missing one: nothing shows, no warning."""
    empty = [
        key
        for key, value in catalogue(code).items()
        if isinstance(value, str) and not value.strip()
    ]

    assert empty == []


@pytest.mark.parametrize("code", SHIPPED_LANGUAGES)
def test_every_key_the_code_uses_exists(code: str) -> None:
    literals, _ = referenced()
    known = catalogue(code)

    missing = sorted(key for key in literals if key not in known)

    assert missing == [], f"used in the code but missing from {code}.json"


def test_no_key_is_left_over() -> None:
    """A key nobody names any more is dead weight — and a trap for translators."""
    _, prefixes = referenced()
    anywhere = mentioned()
    known = catalogue("de")

    orphans = sorted(
        key
        for key in known
        if key not in STRUCTURAL_KEYS
        and key not in anywhere
        and not any(key.startswith(prefix) for prefix in prefixes)
    )

    assert orphans == [], "in the language files but referenced nowhere"


def test_the_lists_are_translated_as_completely_as_the_texts() -> None:
    """Templates and suggestions are content, not decoration: both languages, same size."""
    german, english = catalogue("de"), catalogue("en")

    for key in ("chore_templates", "shopping_suggestions"):
        assert len(german[key]) == len(english[key]), key  # type: ignore[arg-type]
        assert len(german[key]) > 0, key  # type: ignore[arg-type]

    titles = {template["title"] for template in german["chore_templates"]}  # type: ignore[index]
    assert len(titles) == len(german["chore_templates"]), "no duplicate template titles"  # type: ignore[arg-type]


@pytest.mark.parametrize("code", SHIPPED_LANGUAGES)
def test_placeholders_match_between_the_languages(code: str) -> None:
    """`{name}` in one language and `{naem}` in the other renders as literal braces."""
    reference = catalogue("de")
    other = catalogue(code)
    placeholder = re.compile(r"\{(\w+)\}")

    for key, value in reference.items():
        if not isinstance(value, str) or not isinstance(other.get(key), str):
            continue
        assert placeholder.findall(value) == placeholder.findall(other[key]), key  # type: ignore[index]
