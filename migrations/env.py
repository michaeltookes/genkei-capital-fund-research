"""Alembic environment.

Reads the database URL from the GENKEI_DATABASE_URL environment variable so
the same connection string is used by ingesters, the CLI, and migrations.
Falls back to the value in alembic.ini if the env var is not set, which is
useful for offline rendering of SQL.

Per docs/storage.md, migrations are hand-written only — autogenerate is
intentionally not configured (target_metadata stays None).
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

env_url = os.environ.get("GENKEI_DATABASE_URL")
if env_url:
    config.set_main_option("sqlalchemy.url", env_url)

# Hand-written migrations only; autogenerate is disabled by leaving this None.
target_metadata = None


def run_migrations_offline() -> None:
    """Render migrations as SQL without a live connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
