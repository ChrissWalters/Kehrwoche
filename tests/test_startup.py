"""The container start: configuration, database, health probe."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from app.config import Settings
from app.startup import (
    check_health,
    ensure_writable_data_dir,
    health_url,
    main,
    pending_migration,
    prepare,
    prune_backups,
    wait_for_database,
)


def settings_for(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": f"sqlite+pysqlite:///{tmp_path / 'kehrwoche.db'}",
        "data_dir": tmp_path,
        "tls_mode": "off",
    }
    values.update(overrides)
    return Settings(**values)


# --- Waiting for the database ---------------------------------------------------------


def test_sqlite_has_nothing_to_wait_for(tmp_path: Path) -> None:
    """A file is there or it is not — no server to come up."""
    slept: list[float] = []

    wait_for_database(settings_for(tmp_path), sleep=slept.append)

    assert slept == []


def test_an_unreachable_database_gives_up_with_a_message(tmp_path: Path) -> None:
    """Better a container that stops and says why than one that answers half the time."""
    settings = Settings(
        # Nothing listens on this port; every attempt fails at once.
        database_url="postgresql+psycopg://kehrwoche:secret@127.0.0.1:1/kehrwoche",
        data_dir=tmp_path,
        tls_mode="off",
    )
    slept: list[float] = []

    with pytest.raises(SystemExit) as stopped:
        wait_for_database(settings, attempts=3, delay_seconds=0.01, sleep=slept.append)

    assert "not reachable after 3 attempts" in str(stopped.value)
    assert len(slept) == 3, "it waited between the attempts instead of hammering"


# --- The health probe -----------------------------------------------------------------


def test_the_probe_follows_the_transport_mode(tmp_path: Path) -> None:
    plain = settings_for(tmp_path)
    secure = settings_for(tmp_path, tls_mode="self-signed")

    assert health_url(plain) == "http://127.0.0.1:8080/api/v1/health"
    assert health_url(secure) == "https://127.0.0.1:8443/api/v1/health"


def test_the_probe_fails_while_nothing_answers(tmp_path: Path) -> None:
    """Docker reads this as "unhealthy" — which is exactly right before the first start."""
    assert check_health(settings_for(tmp_path, port=1), timeout=1) is False


def test_the_probe_reports_a_healthy_instance(tmp_path: Path) -> None:
    """A real request against a real server, over the loopback interface."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — name comes from the base class
            healthy = self.path == "/api/v1/health"
            self.send_response(200 if healthy else 404)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, *_: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        settings = settings_for(tmp_path, port=server.server_address[1])
        assert check_health(settings, timeout=2) is True
    finally:
        server.shutdown()
        server.server_close()


# --- The command line the entrypoint uses ---------------------------------------------


def test_check_passes_for_a_sqlite_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'kehrwoche.db'}")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TLS_MODE", "off")
    get_settings.cache_clear()
    try:
        assert main(["check"]) == 0
    finally:
        get_settings.cache_clear()


def test_an_unknown_command_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'kehrwoche.db'}")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TLS_MODE", "off")
    get_settings.cache_clear()
    try:
        assert main(["nonsense"]) == 2
    finally:
        get_settings.cache_clear()


# --- What ships -----------------------------------------------------------------------


def test_the_image_leaves_internal_papers_outside() -> None:
    """`.intern/` and `.claude/` are working documents of this repository, not product."""
    ignored = Path(".dockerignore").read_text(encoding="utf-8")

    for entry in (".intern", ".claude", ".git", "tests", "dev-data", ".venv"):
        assert entry in ignored, entry


#: The specification is a working document and is deliberately not published, so this
#: check only has something to compare against on a machine that has it.
SPECIFICATION = Path(".intern/spec.md")


@pytest.mark.skipif(not SPECIFICATION.is_file(), reason="the specification is not published")
def test_the_compose_file_matches_the_specification() -> None:
    """The specification prints the file; ours has to be that file."""
    import re

    blocks = re.findall(r"```yaml\n(.*?)```", SPECIFICATION.read_text(encoding="utf-8"), re.S)
    shipped = Path("docker-compose.yml").read_text(encoding="utf-8").strip()

    assert any(block.strip() == shipped for block in blocks), (
        "docker-compose.yml differs from the version printed in the specification"
    )


def test_the_compose_file_starts_a_working_instance_unedited() -> None:
    """Somebody's first three minutes: download, `up -d`, done.

    Everything optional in that file is a comment. If a variant were ever left active by
    accident — an external database, TLS off — the quick start in the README would send
    people into a broken instance.
    """
    import yaml

    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["kehrwoche"]

    assert list(compose["services"]) == ["kehrwoche"], "the database services stay comments"
    assert service["environment"]["TLS_MODE"] == "self-signed"
    assert "DATABASE_URL" not in service["environment"], "SQLite is the unedited default"
    assert service["ports"] == ["8443:8443"], "the port has to match the TLS mode"
    assert service["volumes"] == ["kehrwoche-data:/data"]
    assert list(compose["volumes"]) == ["kehrwoche-data"]


# --- The guarded start (AP34) ---------------------------------------------------------


def household_count(database: Path) -> int:
    connection = sqlite3.connect(database)
    try:
        return connection.execute("SELECT count(*) FROM households").fetchone()[0]
    finally:
        connection.close()


def revision_of(database: Path) -> str:
    connection = sqlite3.connect(database)
    try:
        return connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        connection.close()


def a_household_in(database: Path) -> None:
    """One row that must survive whatever happens next."""
    connection = sqlite3.connect(database)
    try:
        with connection:
            connection.execute(
                "INSERT INTO households (name, type, join_code, currency,"
                " takeover_keeps_turn, created_at, updated_at) VALUES"
                " ('WG', 'wg', 'ABCDEFGH1234', 'EUR', 0, '2026-01-01', '2026-01-01')"
            )
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()


def test_the_data_directory_is_created_if_it_is_missing(tmp_path: Path) -> None:
    settings = settings_for(tmp_path / "fresh")

    ensure_writable_data_dir(settings)

    assert (tmp_path / "fresh").is_dir()
    assert not (tmp_path / "fresh" / ".write-probe").exists(), "the probe cleans up after itself"


def test_a_data_directory_it_cannot_write_to_stops_the_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The uid trap, caught at the door instead of at the first tick-off."""

    def refuse(*_: object, **__: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "write_bytes", refuse)

    with pytest.raises(SystemExit) as stopped:
        ensure_writable_data_dir(settings_for(tmp_path))

    message = str(stopped.value)
    assert "cannot be written to" in message
    assert "chown -R 10001:10001" in message, "the message has to say what to do"


def test_an_up_to_date_database_has_nothing_pending(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    prepare(settings)

    assert pending_migration(settings) is None


def test_a_database_from_the_future_refuses_to_be_guessed_at(tmp_path: Path) -> None:
    """Somebody stepped back to an older image. Anything but stopping loses data."""
    settings = settings_for(tmp_path)
    prepare(settings)
    database = Path(settings.database_url.split("///")[-1])
    a_household_in(database)
    connection = sqlite3.connect(database)
    with connection:
        connection.execute("UPDATE alembic_version SET version_num = '9999'")
    connection.close()

    with pytest.raises(SystemExit) as stopped:
        prepare(settings)

    assert "9999" in str(stopped.value)
    assert "Nothing has been changed" in str(stopped.value)
    assert household_count(database) == 1
    assert revision_of(database) == "9999", "not even the revision was touched"


def test_a_failed_migration_puts_the_database_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of the copy: a bad update costs a restart, not the household."""
    settings = settings_for(tmp_path)
    prepare(settings)
    database = Path(settings.database_url.split("///")[-1])
    a_household_in(database)
    # Rewind the marker so a migration is due again, then make it fail.
    connection = sqlite3.connect(database)
    with connection:
        connection.execute("UPDATE alembic_version SET version_num = '0007'")
    connection.close()

    def explode(_: Settings) -> None:
        raise RuntimeError("pretend the disk filled up")

    monkeypatch.setattr("app.startup.run_migrations", explode)

    with pytest.raises(SystemExit) as stopped:
        prepare(settings)

    assert "was put back the way it was" in str(stopped.value)
    assert household_count(database) == 1, "the household survived"
    assert revision_of(database) == "0007", "and so did the marker it started from"


def test_an_external_database_is_never_restored_from_a_copy_that_does_not_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No dump tools in here, so the honest answer is to say so and change nothing."""
    settings = settings_for(
        tmp_path, database_url="postgresql+psycopg://kehrwoche:secret@db/kehrwoche"
    )
    monkeypatch.setattr("app.startup.wait_for_database", lambda *_, **__: None)
    monkeypatch.setattr("app.startup.pending_migration", lambda _: ("0007", "0008"))

    def explode(_: Settings) -> None:
        raise RuntimeError("the server said no")

    monkeypatch.setattr("app.startup.run_migrations", explode)
    restored: list[object] = []
    monkeypatch.setattr("app.startup.restore", lambda *args: restored.append(args))

    with pytest.raises(SystemExit) as stopped:
        prepare(settings)

    assert restored == [], "nothing of ours to put back"
    assert "Restore your dump" in str(stopped.value)
    assert not (tmp_path / "backups").exists(), "and no copy was attempted"


def test_only_the_newest_copies_are_kept(tmp_path: Path) -> None:
    """Otherwise the data volume fills up with databases nobody will ever read."""
    settings = settings_for(tmp_path)
    directory = tmp_path / "backups"
    directory.mkdir()
    for index in range(6):
        copy = directory / f"pre-upgrade-000{index}-to-0008-2026010{index}-000000.db"
        copy.write_bytes(b"x")
        os.utime(copy, (index, index))

    removed = prune_backups(settings, keep=3)

    assert len(removed) == 3
    assert sorted(path.name for path in directory.glob("*.db")) == [
        "pre-upgrade-0003-to-0008-20260103-000000.db",
        "pre-upgrade-0004-to-0008-20260104-000000.db",
        "pre-upgrade-0005-to-0008-20260105-000000.db",
    ]


def test_a_first_start_does_not_copy_an_empty_database(tmp_path: Path) -> None:
    """Connecting to SQLite creates the file — copying that would be pure clutter."""
    settings = settings_for(tmp_path)

    prepare(settings)

    assert not (tmp_path / "backups").exists()


def test_a_real_migration_is_copied_first(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    prepare(settings)
    database = Path(settings.database_url.split("///")[-1])
    a_household_in(database)
    connection = sqlite3.connect(database)
    with connection:
        connection.execute("UPDATE alembic_version SET version_num = '0007'")
    connection.close()

    prepare(settings)

    copies = list((tmp_path / "backups").glob("pre-upgrade-0007-to-*.db"))
    assert len(copies) == 1
    assert household_count(copies[0]) == 1, "the copy holds the data, not an empty shell"
