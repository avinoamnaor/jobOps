"""Alembic migration environment.

Two edits were made to the file Alembic generates:

1. The database URL comes from `app.config.settings`, not from alembic.ini.
   One source of truth for the connection string, and no credentials in a file
   that gets committed to git.

2. `target_metadata` points at our ORM `Base.metadata`. This is what makes
   `alembic revision --autogenerate` work: Alembic inspects the real database,
   compares it to this in-memory description of the tables, and writes a
   migration containing the difference.

Autogenerate is a very good assistant and a very bad authority. Always read the
generated migration before applying it — it misses some changes (renames become
drop + add, which loses data) and occasionally invents others.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Importing the models package registers every ORM class on Base.metadata.
# Without this import, autogenerate sees an empty model set and cheerfully
# generates a migration that drops all your tables.
import app.models  # noqa: F401
from app.config import settings
from app.db import Base

config = context.config

# Inject the URL from our own configuration — but only if the caller has not
# already supplied one. The test suite sets it explicitly so that migrations run
# against jobops_test; without this guard, tests would silently migrate (and
# later truncate) the real development database.
if not config.get_main_option("sqlalchemy.url", None):
    config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing it.

    Useful when a DBA has to review or apply the SQL by hand. We do not use it,
    but it costs nothing to keep working.
    """
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect to the database and run migrations against it."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        # NullPool: a migration run is a single short-lived process. Pooling
        # connections it will never reuse is pointless, and holding them open can
        # block DDL.
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Also detect column type changes, not just added/removed columns.
            compare_type=True,
        )

        # PostgreSQL supports transactional DDL: if any statement in the
        # migration fails, the whole migration rolls back and the database is
        # left exactly as it was. Not every database can do this — it is one of
        # the quieter reasons Postgres is pleasant to run migrations against.
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
