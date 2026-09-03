"""What happens between `docker run` and the first request.

The container start is a fixed sequence: check the configuration, wait for the database,
make sure the data directory can be written to, compare what the database holds against
what this code knows, take a copy, migrate, serve. Each step fails loudly and early — a
half-started instance that answers some requests and not others is worse than one that
refuses to come up and says why.

Everything here runs *before* the server accepts anything, which is what makes the
promise cheap to keep: a failed start cannot have changed a chore, an expense or a
password, because nobody was able to send one.

The steps live here rather than in the shell script so they can be tested; the script
only calls them in order.
"""

from __future__ import annotations

import logging
import shutil
import ssl
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from app.config import Settings, configure_logging, get_settings
from app.db import create_db_engine, is_sqlite, sqlite_file

logger = logging.getLogger(__name__)

#: How long the container waits for an external database before giving up. A database
#: container started next to this one is usually ready in a few seconds; two minutes
#: leaves room for a slow first initialisation.
DATABASE_ATTEMPTS = 60
DATABASE_DELAY_SECONDS = 2.0

#: Timeout of the container health probe.
HEALTHCHECK_TIMEOUT_SECONDS = 5

#: Where the copies taken before a migration go, inside the data volume.
BACKUP_DIR_NAME = "backups"
#: How many of them to keep. Three is enough to step back over a bad update without the
#: volume quietly filling up with copies of a database nobody will ever read again.
BACKUPS_KEPT = 3

#: The dump command to recommend, by dialect — the application cannot run either itself.
DUMP_COMMANDS = {
    "mysql": "mariadb-dump -u <user> -p <database> > kehrwoche.sql",
    "postgresql": "pg_dump -U <user> <database> > kehrwoche.sql",
}


def wait_for_database(
    settings: Settings,
    *,
    attempts: int = DATABASE_ATTEMPTS,
    delay_seconds: float = DATABASE_DELAY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Block until the database answers.

    Only for server-backed databases: a SQLite file has nothing to wait for. Compose
    already orders the start with `depends_on`, but "the container is healthy" and "the
    database accepts connections" are not the same moment.
    """
    if is_sqlite(settings.database_url):
        return

    engine = create_db_engine(settings)
    last_error: Exception | None = None
    try:
        for attempt in range(1, attempts + 1):
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                return
            except Exception as error:  # noqa: BLE001 — any driver error means "not yet"
                last_error = error
                logger.info("waiting for the database (%s/%s)", attempt, attempts)
                sleep(delay_seconds)
    finally:
        engine.dispose()

    raise SystemExit(f"Database not reachable after {attempts} attempts: {last_error}")


def ensure_writable_data_dir(settings: Settings) -> None:
    """Create the data directory and prove we can write into it.

    Left unchecked this surfaces much later and much more confusingly: the instance
    starts, shows everything, and fails the moment somebody ticks a chore off. The usual
    cause is a directory from the host mounted over ``/data`` — it arrives owned by
    whoever created it, and this process is not root.
    """
    directory = settings.data_dir
    probe = directory / ".write-probe"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as error:
        raise SystemExit(
            f"The data directory {directory} cannot be written to ({error.strerror}).\n"
            "This process runs unprivileged, as uid 10001, and never as root. If you "
            "mounted a directory of your own, either hand it over once with\n"
            f"    chown -R 10001:10001 <the directory behind {directory}>\n"
            "or run the container as whoever owns it (`user:` in the compose file). A "
            "named volume needs neither."
        ) from error


def _alembic_config() -> object:
    """The migration configuration, wherever this instance happens to be started from.

    In the image the package lives in the virtual environment while ``alembic.ini`` and
    the revision scripts sit in the working directory; in a checkout both are in the
    repository root. Looking in the working directory first covers the container, the
    fallback covers everything else.
    """
    from alembic.config import Config

    candidates = [
        Path.cwd() / "alembic.ini",
        Path(__file__).resolve().parent.parent / "alembic.ini",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return Config(str(candidate))
    raise SystemExit(f"alembic.ini not found (looked in {', '.join(str(c) for c in candidates)}).")


def database_revision(settings: Settings) -> str | None:
    """The revision the database says it is at, or ``None`` for an empty one."""
    from alembic.runtime.migration import MigrationContext

    engine = create_db_engine(settings)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def code_revisions() -> tuple[set[str], str]:
    """Every revision this code ships, and the newest of them."""
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_config())  # type: ignore[arg-type]
    return {revision.revision for revision in script.walk_revisions()}, script.get_current_head()


def pending_migration(settings: Settings) -> tuple[str | None, str] | None:
    """``(from, to)`` when the database has to move, ``None`` when it is up to date.

    A revision this code has never heard of means the database was written by a newer
    version — somebody stepped back to an older image. Guessing would be the one truly
    destructive thing to do here, so it refuses instead.
    """
    current = database_revision(settings)
    known, head = code_revisions()

    if current is not None and current not in known:
        raise SystemExit(
            f"This database is at revision {current}, which this version does not know.\n"
            "It was almost certainly written by a newer version of Kehrwoche. Nothing has "
            "been changed.\n"
            "Either start the newer image again, or restore a backup taken before the "
            "update. Backups of a SQLite instance are in the data volume under "
            f"{BACKUP_DIR_NAME}/."
        )
    return None if current == head else (current, head)


def _backup_dir(settings: Settings) -> Path:
    return settings.data_dir / BACKUP_DIR_NAME


def safety_copy(settings: Settings, current: str | None, head: str) -> Path | None:
    """A copy of the database taken before it is migrated — SQLite only.

    An external database cannot be copied from in here: the image ships no client tools,
    the server is not ours, and it may be shared and large. The caller says so out loud
    instead of writing something that only looks like a backup.
    """
    if not is_sqlite(settings.database_url):
        return None

    source = sqlite_file(settings.database_url)
    if source is None or not source.exists():
        return None  # nothing to protect yet — this is a first start

    from app.services.admin import backup_database

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = _backup_dir(settings) / f"pre-upgrade-{current or 'empty'}-to-{head}-{stamp}.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    return backup_database(settings, target)


def restore(copy: Path, settings: Settings) -> None:
    """Put the copy back where the database belongs.

    The write-ahead log and the shared-memory file go first: they describe the *failed*
    state, and a restored file paired with a stale log is worse than either alone.
    """
    target = sqlite_file(settings.database_url)
    if target is None:
        return
    for leftover in (Path(f"{target}-wal"), Path(f"{target}-shm")):
        leftover.unlink(missing_ok=True)
    shutil.copy2(copy, target)


def prune_backups(settings: Settings, *, keep: int = BACKUPS_KEPT) -> list[Path]:
    """Drop all but the newest copies; returns what was removed."""
    directory = _backup_dir(settings)
    if not directory.is_dir():
        return []
    copies = sorted(directory.glob("pre-upgrade-*.db"), key=lambda path: path.stat().st_mtime)
    removed = copies[: max(0, len(copies) - keep)]
    for path in removed:
        path.unlink(missing_ok=True)
    return removed


def _warn_about_external_database(settings: Settings, current: str | None, head: str) -> None:
    """Say what we cannot do, before doing the thing that might need it."""
    dialect = settings.database_url.split("+")[0].split(":")[0]
    logger.warning(
        "Migration %s -> %s is due. This instance uses an external database, which "
        "cannot be backed up from in here — please make sure you have a dump: %s",
        current or "empty",
        head,
        DUMP_COMMANDS.get(dialect, "the dump tool of your database"),
    )
    if dialect == "mysql":
        logger.warning(
            "MariaDB/MySQL does not roll a failed migration back: it applies each step "
            "as it goes. If this one fails, the schema may sit between two versions and "
            "a dump is the only way back."
        )


def run_migrations(settings: Settings) -> None:
    """Bring the database up to date, in this process so failures can be caught.

    The database is named explicitly rather than left to the environment: this has to be
    the very database the steps above inspected and copied, not whatever a stray
    variable happens to point at.
    """
    from alembic import command

    config = _alembic_config()
    config.set_main_option("db_url", settings.database_url)  # type: ignore[attr-defined]
    command.upgrade(config, "head")  # type: ignore[arg-type]


def prepare(settings: Settings) -> None:
    """Everything that has to be true before the first request is served."""
    wait_for_database(settings)
    ensure_writable_data_dir(settings)

    move = pending_migration(settings)
    if move is None:
        logger.info("Database is up to date.")
        return

    current, head = move
    copy: Path | None = None
    # A first start has nothing to protect: connecting to SQLite creates the file, so
    # without this the volume would collect a copy of an empty database.
    if is_sqlite(settings.database_url) and current is not None:
        copy = safety_copy(settings, current, head)
        if copy is not None:
            logger.info("Migration %s -> %s is due; copied the database to %s", current, head, copy)
    else:
        _warn_about_external_database(settings, current, head)

    try:
        run_migrations(settings)
    except Exception as error:
        if copy is None:
            raise SystemExit(
                f"The migration to {head} failed and nothing here could undo it: "
                f"{error}\n"
                "The database has not been touched by this process beyond the migration "
                "itself. Restore your dump before starting again."
            ) from error
        restore(copy, settings)
        raise SystemExit(
            f"The migration to {head} failed: {error}\n"
            f"The database was put back the way it was, from {copy}. "
            "Nothing was lost; start the previous version again, or report this."
        ) from error

    logger.info("Database migrated to %s.", head)
    for removed in prune_backups(settings):
        logger.info("Removed an old copy: %s", removed.name)


def health_url(settings: Settings) -> str:
    """Where the container probes itself."""
    scheme = "https" if settings.tls_enabled else "http"
    return f"{scheme}://127.0.0.1:{settings.port}/api/v1/health"


def check_health(settings: Settings, *, timeout: int = HEALTHCHECK_TIMEOUT_SECONDS) -> bool:
    """True when the instance answers its own health endpoint.

    Certificate verification is off on purpose: the probe talks to itself over the
    loopback interface, and in the default mode the certificate is self-signed anyway.
    """
    context = ssl._create_unverified_context() if settings.tls_enabled else None
    try:
        with urllib.request.urlopen(  # noqa: S310 — fixed loopback URL built above
            health_url(settings), timeout=timeout, context=context
        ) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def serve(settings: Settings) -> None:
    """Start the application server with the transport the configuration asks for."""
    import uvicorn

    from app.security import ensure_certificate

    material = ensure_certificate(settings)
    certificate_file, key_file = material if material else (None, None)

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",  # noqa: S104 — a container serves on every interface it has
        port=settings.port or 8443,
        ssl_certfile=str(certificate_file) if certificate_file else None,
        ssl_keyfile=str(key_file) if key_file else None,
        log_level=settings.log_level.value,
        access_log=True,
    )


def main(argv: list[str] | None = None) -> int:
    """`check`, `prepare`, `healthcheck` or `serve` — what the container needs."""
    arguments = sys.argv[1:] if argv is None else argv
    command = arguments[0] if arguments else "serve"
    settings = get_settings()  # validates the configuration; a bad value stops here
    configure_logging(settings)

    if command == "check":
        wait_for_database(settings)
        return 0
    if command == "prepare":
        prepare(settings)
        return 0
    if command == "healthcheck":
        return 0 if check_health(settings) else 1
    if command == "serve":
        serve(settings)
        return 0

    print(f"Unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover — container entry point
    raise SystemExit(main())
