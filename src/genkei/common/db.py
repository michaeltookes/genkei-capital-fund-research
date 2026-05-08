"""Postgres helpers shared by every ingester and the CLI.

Reads connection URL from ``GENKEI_DATABASE_URL``. Exposes:

- a lazily-initialized connection pool (:func:`get_pool`, :func:`reset_pool`),
- a :func:`connection` context manager that commits on success and rolls back
  on exception,
- :func:`bulk_upsert` for ``INSERT ... ON CONFLICT`` batched writes,
- :func:`ingest_run` — the context manager every ingester wraps its work in,
  which records a row in ``meta.ingest_runs``.

Per ``docs/storage.md``, every fact table carries provenance columns and joins
back to ``meta.ingest_runs`` via ``ingest_run_id``. The :class:`IngestRun`
returned from the context manager exposes ``id`` for that purpose.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Mapping, Optional, Sequence

import psycopg
from psycopg import sql
from psycopg_pool import ConnectionPool

_POOL: Optional[ConnectionPool] = None


def _resolve_url(url: Optional[str]) -> str:
    """Return a libpq-compatible connection string."""
    if url is None:
        url = os.environ.get("GENKEI_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "GENKEI_DATABASE_URL is not set. Define it in your environment "
            "(see .env.example) before invoking Postgres helpers."
        )
    # Alembic / SQLAlchemy use the postgresql+psycopg:// driver prefix; psycopg
    # itself wants a plain libpq URL.
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def get_pool(
    url: Optional[str] = None,
    *,
    min_size: int = 1,
    max_size: int = 10,
) -> ConnectionPool:
    """Return the shared connection pool, creating it on first call.

    Subsequent calls return the same pool regardless of arguments — this is
    a process-wide singleton. Tests should call :func:`reset_pool` first to
    swap connection details.
    """
    global _POOL
    if _POOL is None:
        _POOL = ConnectionPool(
            conninfo=_resolve_url(url),
            min_size=min_size,
            max_size=max_size,
            open=True,
        )
    return _POOL


def reset_pool() -> None:
    """Tear down the shared pool. Used by tests and on process shutdown."""
    global _POOL
    if _POOL is not None:
        _POOL.close()
        _POOL = None


def set_pool(pool: ConnectionPool) -> None:
    """Inject a pool (testing). Caller is responsible for closing it."""
    global _POOL
    _POOL = pool


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """Yield a connection from the pool.

    Commits on clean exit. Rolls back and re-raises on exception. The
    connection is returned to the pool either way.
    """
    pool = get_pool()
    with pool.connection() as conn:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def bulk_upsert(
    conn: psycopg.Connection,
    table: str,
    rows: Sequence[Mapping[str, Any]],
    conflict_keys: Sequence[str],
    update_cols: Optional[Sequence[str]] = None,
) -> int:
    """``INSERT ... ON CONFLICT DO UPDATE`` for a batch of rows.

    ``table`` may be schema-qualified (e.g. ``"defillama.protocols"``).
    ``conflict_keys`` form the unique constraint that triggers the upsert.
    ``update_cols`` defaults to all columns not in ``conflict_keys``; pass
    an empty sequence to fall back to ``DO NOTHING``.

    Returns the number of rows affected (sum of per-row ``cur.rowcount``).
    """
    if not rows:
        return 0

    cols = list(rows[0].keys())
    if update_cols is None:
        update_cols = [c for c in cols if c not in conflict_keys]

    table_ident = sql.SQL(".").join(sql.Identifier(p) for p in table.split("."))
    col_idents = sql.SQL(", ").join(sql.Identifier(c) for c in cols)
    placeholders = sql.SQL(", ").join(sql.Placeholder() for _ in cols)
    conflict_idents = sql.SQL(", ").join(sql.Identifier(c) for c in conflict_keys)

    if update_cols:
        update_clause = sql.SQL(", ").join(
            sql.SQL("{col} = EXCLUDED.{col}").format(col=sql.Identifier(c))
            for c in update_cols
        )
        query = sql.SQL(
            "INSERT INTO {table} ({cols}) VALUES ({placeholders}) "
            "ON CONFLICT ({conflict}) DO UPDATE SET {updates}"
        ).format(
            table=table_ident,
            cols=col_idents,
            placeholders=placeholders,
            conflict=conflict_idents,
            updates=update_clause,
        )
    else:
        query = sql.SQL(
            "INSERT INTO {table} ({cols}) VALUES ({placeholders}) "
            "ON CONFLICT ({conflict}) DO NOTHING"
        ).format(
            table=table_ident,
            cols=col_idents,
            placeholders=placeholders,
            conflict=conflict_idents,
        )

    affected = 0
    with conn.cursor() as cur:
        cur.executemany(query, [[row[c] for c in cols] for row in rows])
        affected = cur.rowcount or 0
    return affected


@dataclass
class IngestRun:
    """Handle for a row in ``meta.ingest_runs``.

    Yielded by :func:`ingest_run`. Ingester code should call :meth:`add_rows`
    as it writes so the row count is captured in the audit trail. The
    ``id`` field is meant to be stamped onto every fact row inserted during
    the run via the ``ingest_run_id`` provenance column.
    """

    id: int
    rows_written: int = 0

    def add_rows(self, n: int) -> None:
        self.rows_written += n


_INGEST_RUN_INSERT = (
    "INSERT INTO meta.ingest_runs (source, endpoint, status, metadata) "
    "VALUES (%s, %s, 'running', %s) RETURNING id"
)
_INGEST_RUN_FAIL = (
    "UPDATE meta.ingest_runs "
    "SET status='failed', finished_at=now(), error=%s, rows_written=%s "
    "WHERE id=%s"
)
_INGEST_RUN_SUCCESS = (
    "UPDATE meta.ingest_runs "
    "SET status='success', finished_at=now(), rows_written=%s "
    "WHERE id=%s"
)
_ERROR_FIELD_LIMIT = 8000


@contextmanager
def ingest_run(
    source: str,
    endpoint: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> Iterator[IngestRun]:
    """Record a pipeline execution to ``meta.ingest_runs``.

    On entry, inserts a row with ``status='running'`` and yields an
    :class:`IngestRun` handle. On clean exit, updates to ``status='success'``
    with the row count the caller accumulated. On exception, updates to
    ``status='failed'`` with the truncated error message and re-raises.

    Three short transactions (insert, then either success or fail update) so
    a long-running ingest doesn't tie up a pool slot.
    """
    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                _INGEST_RUN_INSERT,
                [
                    source,
                    endpoint,
                    json.dumps(dict(metadata)) if metadata is not None else None,
                ],
            )
            row = cur.fetchone()
        conn.commit()

    if row is None:  # pragma: no cover — RETURNING always returns a row
        raise RuntimeError("Failed to insert into meta.ingest_runs")
    handle = IngestRun(id=row[0])

    try:
        yield handle
    except Exception as exc:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    _INGEST_RUN_FAIL,
                    [str(exc)[:_ERROR_FIELD_LIMIT], handle.rows_written, handle.id],
                )
            conn.commit()
        raise
    else:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_INGEST_RUN_SUCCESS, [handle.rows_written, handle.id])
            conn.commit()
