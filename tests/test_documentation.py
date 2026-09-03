"""Documentation against the code.

Prose rots quietly. A setting that gets renamed leaves a reference page describing a
variable nobody reads any more, and the person following it wonders for an hour why their
instance ignores them. These checks are cheap and they run in CI, so the documentation
either follows the code or the build says so.

They deliberately check facts, not wording: names, links, files and versions.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import pytest

from app import __version__
from app.config import Settings

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
CONFIGURATION = DOCS / "configuration.md"

#: Read by the application server rather than by us, but part of a working deployment
#: behind a proxy — documented on purpose, and not a field of `Settings`.
PASSED_THROUGH = {"FORWARDED_ALLOW_IPS"}


def documented_variables() -> set[str]:
    """The variables named in the first column of the reference table."""
    rows = re.findall(r"^\| `([A-Z_]+)` \|", CONFIGURATION.read_text(encoding="utf-8"), re.M)
    return set(rows)


def test_every_setting_is_documented() -> None:
    """A variable nobody can look up may as well not exist."""
    expected = {name.upper() for name in Settings.model_fields}

    missing = sorted(expected - documented_variables())

    assert missing == [], "settings without a row in docs/configuration.md"


def test_no_variable_is_documented_that_does_not_exist() -> None:
    """The other direction: a renamed setting leaves a ghost behind."""
    known = {name.upper() for name in Settings.model_fields} | PASSED_THROUGH

    ghosts = sorted(documented_variables() - known)

    assert ghosts == [], "documented in docs/configuration.md but not a real setting"


def test_the_documented_defaults_are_the_real_ones() -> None:
    """A wrong default is worse than none: it is believed."""
    settings = Settings()
    table = CONFIGURATION.read_text(encoding="utf-8")

    for name in ("SESSION_MAX_AGE_DAYS", "LOG_LEVEL", "TLS_MODE", "EXTERNAL_HOSTNAMES"):
        value = getattr(settings, name.lower())
        row = re.search(rf"^\| `{name}` \| `([^`]+)` \|", table, re.M)
        assert row, f"{name} has no default in the table"
        assert row.group(1) == str(value), name


@pytest.mark.parametrize("document", ["README.md", "README.de.md"])
def test_the_readme_links_point_at_files_that_exist(document: str) -> None:
    """A broken link in the first thing anybody reads is a bad first impression."""
    text = (ROOT / document).read_text(encoding="utf-8")
    targets = re.findall(r"\]\((?!https?://|#)([^)#]+)", text)

    missing = sorted({target for target in targets if not (ROOT / target).exists()})

    assert missing == [], f"{document} links to files that are not there"


def test_the_documentation_links_to_itself_correctly() -> None:
    """The pages under docs/ cross-reference each other and point back at the README."""
    missing: list[str] = []
    for page in DOCS.rglob("*.md"):
        for target in re.findall(r"\]\((?!https?://|#)([^)#]+)", page.read_text(encoding="utf-8")):
            if not (page.parent / target).resolve().exists():
                missing.append(f"{page.name} -> {target}")

    assert missing == []


def test_the_screenshots_the_readme_shows_are_there() -> None:
    """Placeholders count — a broken image does not."""
    for name in ("mobile-chores", "mobile-shopping", "mobile-expenses", "desktop-feed"):
        assert (DOCS / "images" / f"{name}.png").is_file(), name


def test_one_version_everywhere() -> None:
    """The package, the distribution and the changelog have to agree."""
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert metadata["project"]["version"] == __version__
    assert f"## [{__version__}]" in changelog, "the current version has no changelog entry"


def test_the_release_workflow_publishes_only_from_a_tag() -> None:
    """Publishing from a branch would make a release something that happens by accident."""
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert 'tags: ["v*"]' in workflow
    assert "branches:" not in workflow


# --- What must never be published -----------------------------------------------------

#: File names and suffixes that have no business in a public repository. Secret scanning
#: at the forge catches known token formats; it knows nothing about a database somebody
#: dropped in by accident, and that is the likelier mistake here.
FORBIDDEN = (
    ".intern",  # specification and plan — working documents
    ".claude",  # working instructions and progress notes
    ".venv",
    "dev-data",
    ".env",
)
FORBIDDEN_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".key", ".pem", ".crt", ".p12", ".pfx")


def tracked_files() -> list[str]:
    """Everything git would publish, straight from git rather than from a guess."""
    listing = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return listing.stdout.splitlines()


def test_nothing_private_is_tracked() -> None:
    """One commit is enough to put a secret in the history for good."""
    offenders = [
        path
        for path in tracked_files()
        if any(part in FORBIDDEN for part in Path(path).parts)
        or Path(path).suffix.lower() in FORBIDDEN_SUFFIXES
    ]

    assert offenders == [], "these files must not be in a public repository"


def test_no_tracked_file_carries_a_credential() -> None:
    """A rough net for the shapes a leaked secret usually has."""
    patterns = re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
        r"|ghp_[A-Za-z0-9]{36}"
        r"|github_pat_[A-Za-z0-9_]{22,}"
        r"|AKIA[0-9A-Z]{16}"
        r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    )

    offenders = []
    for path in tracked_files():
        full = ROOT / path
        try:
            text = full.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary, or gone in a dirty tree — neither is a credential
        if patterns.search(text):
            offenders.append(path)

    assert offenders == [], "these files look like they carry a credential"


def test_no_tracked_file_names_the_author_personally() -> None:
    """Home directories and private addresses travel further than people expect."""
    personal = re.compile(r"/home/[a-z]+/|chris@graf-sapp\.de")

    offenders = []
    for path in tracked_files():
        try:
            text = (ROOT / path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if personal.search(text):
            offenders.append(path)

    assert offenders == [], "these files carry a personal path or address"
