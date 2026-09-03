"""Alembic environment: migrations always follow ``DATABASE_URL`` and the models."""

from logging.config import fileConfig

from sqlalchemy import event

import app.models  # noqa: F401  — registers every model on Base.metadata
from alembic import context
from app.config import get_settings
from app.db import Base, create_db_engine, is_sqlite
from app.models.base import UtcDateTime

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def render_item(type_: str, obj: object, autogen_context: object) -> str | bool:
    """Keep generated migrations independent of application code.

    ``UtcDateTime`` is a thin wrapper around ``DateTime(timezone=True)`` and emits the
    very same DDL — rendering the plain type means a migration never breaks when the
    wrapper moves or changes.
    """
    if type_ == "type" and isinstance(obj, UtcDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def _database_url() -> str:
    """Which database to migrate.

    Order: the command line (``-x db_url=…``), then what the caller put into the
    configuration (the start-up sequence does, so it migrates exactly the database it
    just inspected), then the environment.
    """
    return (
        context.get_x_argument(as_dictionary=True).get("db_url")
        or context.config.get_main_option("db_url", None)
        or get_settings().database_url
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing it (``alembic upgrade head --sql``)."""
    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_item=render_item,
        render_as_batch=is_sqlite(url),
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    settings = get_settings().model_copy(update={"database_url": _database_url()})
    engine = create_db_engine(settings)

    if is_sqlite(settings.database_url):
        # SQLite cannot ALTER a column: batch mode rebuilds the table and drops the old
        # one. With foreign keys enforced that DROP is refused as soon as sessions,
        # chores or expenses point at it — so enforcement pauses for the migration.
        # The application connects with foreign keys on, as always.
        @event.listens_for(engine, "connect")
        def _pause_foreign_keys(dbapi_connection: object, _: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=OFF")
            cursor.close()

    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                render_item=render_item,
                # SQLite cannot ALTER columns; batch mode rebuilds the table instead.
                render_as_batch=is_sqlite(settings.database_url),
            )

            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
