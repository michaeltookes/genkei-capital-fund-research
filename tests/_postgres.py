"""Postgres integration-test harness (B-024).

Spins up an ephemeral TimescaleDB container via testcontainers, applies the
project's Alembic migrations, and exposes:

- :func:`postgres_required` — decorator/skip wrapper for tests that need a
  live database. Skips cleanly when Docker or testcontainers isn't usable
  so the offline mock-based suite still runs everywhere.
- :func:`get_harness` — lazy singleton; the same container is reused across
  tests in a process for speed (image pull + startup ≈ 10–20 s).
- :class:`PostgresHarness` — exposes ``url``, ``connection()`` (auto-rolling
  back), and ``truncate_all()`` for cleanup between tests that go through
  ``genkei.common.db`` helpers (which manage their own commits and so cannot
  be isolated with a transaction).

Image pinned to the same Timescale build the homelab runs (R-014). On a
fresh data dir the image's entrypoint sets ``shared_preload_libraries`` to
``timescaledb`` automatically, so ``CREATE EXTENSION timescaledb`` in the
migration chain succeeds without extra wiring.
"""

from __future__ import annotations

import atexit
import os
import shutil
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

try:
    import psycopg
    from testcontainers.postgres import PostgresContainer

    _IMPORTS_OK = True
except ImportError:
    _IMPORTS_OK = False

TIMESCALE_IMAGE = "timescale/timescaledb:2.26.4-pg16"
USER_TABLE_SCHEMAS = ("defillama", "meta")

_HARNESS: PostgresHarness | None = None


def _docker_available() -> bool:
    """Return True if a Docker CLI is on PATH and the daemon responds."""
    if shutil.which("docker") is None:
        return False
    # `docker info` is the canonical "is the daemon up" probe.
    return os.system("docker info > /dev/null 2>&1") == 0


def postgres_required(obj: Any) -> Any:
    """Skip the wrapped test/class when Docker + testcontainers aren't usable."""
    return unittest.skipUnless(
        _IMPORTS_OK and _docker_available(),
        "Docker + testcontainers required for Postgres integration tests",
    )(obj)


class PostgresHarness:
    """Owns a running TimescaleDB container with migrations applied."""

    def __init__(self) -> None:
        self._container = PostgresContainer(TIMESCALE_IMAGE, driver=None)
        self._container.start()
        self.url = self._container.get_connection_url()
        self._apply_migrations()

    def _apply_migrations(self) -> None:
        # Imported lazily so harness import doesn't pull alembic into every
        # offline test run.
        from alembic import command
        from alembic.config import Config

        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", self.url)
        command.upgrade(cfg, "head")

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        """Yield a raw psycopg connection that always rolls back on exit.

        Use this for tests that exercise SQL directly. Tests that go through
        ``genkei.common.db`` helpers (which commit on their own) should call
        :meth:`truncate_all` in tearDown instead.
        """
        with psycopg.connect(self.url) as conn:
            try:
                yield conn
            finally:
                conn.rollback()

    def truncate_all(self) -> None:
        """Truncate every user table so the next test starts clean.

        ``RESTART IDENTITY CASCADE`` resets ``BIGSERIAL`` counters (so
        ``meta.ingest_runs.id`` predictability holds) and follows FKs.
        """
        with psycopg.connect(self.url) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT format('%I.%I', table_schema, table_name)
                FROM information_schema.tables
                WHERE table_schema = ANY(%s)
                  AND table_type = 'BASE TABLE'
                """,
                [list(USER_TABLE_SCHEMAS)],
            )
            tables = [row[0] for row in cur.fetchall()]
            if tables:
                cur.execute(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")
            conn.commit()

    def stop(self) -> None:
        self._container.stop()


def get_harness() -> PostgresHarness:
    """Return the process-wide harness, building it on first call."""
    global _HARNESS
    if _HARNESS is None:
        _HARNESS = PostgresHarness()
        atexit.register(_HARNESS.stop)
    return _HARNESS
