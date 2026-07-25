"""Postgres helpers shared by every ingester and the CLI.

Reads connection URL from ``GENKEI_DATABASE_URL`` or, when that is absent,
builds one from non-secret host/port specs in the local ``server-info`` skill
plus credential environment variables. Exposes:

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
import logging
import os
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import psycopg
from psycopg import sql
from psycopg_pool import ConnectionPool

_POOL: ConnectionPool | None = None
_LOGGER = logging.getLogger(__name__)
_DEFAULT_SERVER_INFO_PATH = Path.home() / ".claude" / "skills" / "server-info" / "SKILL.md"


def _resolve_url(url: str | None) -> str:
    """Return a libpq-compatible connection string."""
    if url is None:
        url = os.environ.get("GENKEI_DATABASE_URL") or _server_info_database_url()
    if not url:
        raise RuntimeError(
            "GENKEI_DATABASE_URL is not set and server-info-derived Postgres "
            "settings are incomplete. Define GENKEI_DATABASE_URL or set "
            "GENKEI_DATABASE_USER, GENKEI_DATABASE_PASSWORD, and "
            "GENKEI_DATABASE_NAME before invoking Postgres helpers."
        )
    # Alembic / SQLAlchemy use the postgresql+psycopg:// driver prefix; psycopg
    # itself wants a plain libpq URL.
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _server_info_database_url() -> str | None:
    """Build a Postgres URL from server-info host/port plus env credentials."""
    user = os.environ.get("GENKEI_DATABASE_USER")
    password = os.environ.get("GENKEI_DATABASE_PASSWORD")
    database = os.environ.get("GENKEI_DATABASE_NAME")
    if not user or not password or not database:
        return None

    server_info_path = Path(
        os.environ.get("GENKEI_SERVER_INFO_PATH", str(_DEFAULT_SERVER_INFO_PATH))
    ).expanduser()
    try:
        server_info = server_info_path.read_text(encoding="utf-8")
    except OSError:
        return None

    host = _server_info_development_host(server_info)
    port = _server_info_database_port(server_info)
    if not host or not port:
        return None
    return "postgresql://{user}:{password}@{host}:{port}/{database}".format(
        user=quote(user, safe=""),
        password=quote(password, safe=""),
        host=host,
        port=port,
        database=quote(database, safe=""),
    )


def _server_info_development_host(server_info: str) -> str | None:
    """Extract the Beelink development host from server-info text."""
    match = re.search(r"## Development Server.*?\*\*Host:\*\*\s*([^\s]+)", server_info, re.DOTALL)
    return match.group(1) if match else None


def _server_info_database_port(server_info: str) -> str | None:
    """Extract the Genkei Capital Postgres host port from server-info text."""
    match = re.search(r"\|\s*genkeicapital-postgres\s*\|\s*(\d+)\s*\|", server_info)
    return match.group(1) if match else None


def get_pool(
    url: str | None = None,
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


# Default statement timeout for read-only queries, in seconds. Callers that
# need a different ceiling pass ``timeout_seconds`` explicitly (``genkei
# query`` exposes it as a flag up to its own MAX_TIMEOUT_SECONDS; the read
# API pins a short server-side default so a slow query can't tie up one of
# its few pool slots).
DEFAULT_READONLY_TIMEOUT_SECONDS = 30


def run_readonly(
    sql_text: str,
    params: Sequence[Any] | None = None,
    *,
    timeout_seconds: int = DEFAULT_READONLY_TIMEOUT_SECONDS,
) -> tuple[list[str], list[tuple[Any, ...]]]:
    """Execute ``sql_text`` in a READ ONLY transaction with a statement timeout.

    The single enforcement point for read-only SELECTs shared by ``genkei
    query`` (B-045) and the FastAPI read layer (B-131). Every query routed
    through here runs with two engine-level guards that a caller cannot weaken:

    1. ``SET TRANSACTION READ ONLY`` — Postgres itself rejects any write
       (INSERT / UPDATE / DELETE / DDL). No SQL parsing required, and the
       ``connection()`` context manager never COMMITs anything anyway.
    2. ``SET LOCAL statement_timeout`` — the server cancels a query that runs
       longer than ``timeout_seconds``; a runaway can't pin a pool slot open.

    Returns ``(column_names, rows)``. ``params`` is passed straight through to
    psycopg for server-side parameter binding — callers should never
    string-format user input into ``sql_text``. Row capping is the caller's
    job: pass a query that already carries a ``LIMIT`` (``genkei query`` wraps
    the user SQL; the API endpoints append their own capped ``LIMIT``).

    ``SET LOCAL statement_timeout`` needs a literal (bind params aren't
    allowed for it), so ``timeout_seconds`` must be a trusted int — every
    call site range-validates it before reaching here.
    """
    timeout_ms = int(timeout_seconds) * 1000
    with connection() as conn, conn.cursor() as cur:
        # Both settings are transaction-scoped: they don't leak past the
        # connection-pool return, so a later writer reusing the slot is
        # unaffected.
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute(f"SET LOCAL statement_timeout = {timeout_ms}")
        cur.execute(sql_text, params)
        cols = [d.name for d in cur.description] if cur.description else []
        rows = list(cur.fetchall())
        # No COMMIT needed — READ ONLY means there's nothing to commit, and
        # connection() commits the (empty) transaction on clean exit.
    return cols, rows


def bulk_upsert(
    conn: psycopg.Connection,
    table: str,
    rows: Sequence[Mapping[str, Any]],
    conflict_keys: Sequence[str],
    update_cols: Sequence[str] | None = None,
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
    if not conflict_keys:
        raise ValueError("conflict_keys must be provided")

    cols = list(rows[0].keys())
    col_set = set(cols)
    for row in rows:
        if set(row.keys()) != col_set:
            raise ValueError("All rows must have the same keys")
    if update_cols is not None and not set(update_cols).issubset(col_set):
        raise ValueError("update_cols must be a subset of row columns")
    if update_cols is None:
        update_cols = [c for c in cols if c not in conflict_keys]

    table_ident = sql.SQL(".").join(sql.Identifier(p) for p in table.split("."))
    col_idents = sql.SQL(", ").join(sql.Identifier(c) for c in cols)
    placeholders = sql.SQL(", ").join(sql.Placeholder() for _ in cols)
    conflict_idents = sql.SQL(", ").join(sql.Identifier(c) for c in conflict_keys)

    if update_cols:
        update_clause = sql.SQL(", ").join(
            sql.SQL("{col} = EXCLUDED.{col}").format(col=sql.Identifier(c)) for c in update_cols
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


_RAW_BLOBS_INSERT = (
    "INSERT INTO meta.raw_blobs (ingest_run_id, endpoint_name, url, payload) "
    "VALUES (%s, %s, %s, %s::jsonb) "
    "ON CONFLICT (ingest_run_id, endpoint_name) DO NOTHING"
)
_RAW_BLOBS_COPY_INSERT = (
    "INSERT INTO meta.raw_blobs (ingest_run_id, endpoint_name, url, payload, fetched_at) "
    "VALUES (%s, %s, %s, %s::jsonb, %s) "
    "ON CONFLICT (ingest_run_id, endpoint_name) DO NOTHING"
)


def store_raw_blob(
    ingest_run_id: int,
    endpoint_name: str,
    url: str,
    payload: Any,
) -> None:
    """Insert one ``meta.raw_blobs`` row.

    ``payload`` is JSON-serialized here; pass a dict, list, or pre-formed
    JSON-shaped value. For non-JSON sources (e.g. XML), wrap the text in a
    single-key object before calling.
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(_RAW_BLOBS_INSERT, [ingest_run_id, endpoint_name, url, json.dumps(payload)])


def copy_raw_blob_for_run(
    ingest_run_id: int,
    endpoint_name: str,
    url: str,
    payload: Any,
    fetched_at: Any,
) -> None:
    """Copy a previously-fetched raw blob into the current run (preserves ``fetched_at``).

    Used by resumable backfills: when a prior run already fetched a URL, the
    new run links the same data without an HTTP round-trip but keeps the
    original fetch timestamp for provenance.
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            _RAW_BLOBS_COPY_INSERT,
            [ingest_run_id, endpoint_name, url, json.dumps(payload), fetched_at],
        )


def record_partial_endpoints(
    ingest_run_id: int,
    partial: Sequence[Mapping[str, str]],
) -> None:
    """Attach per-endpoint partial-failure metadata to an ingest run."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE meta.ingest_runs SET metadata = "
            "COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('partial_endpoints', %s::jsonb) "
            "WHERE id = %s",
            [json.dumps(list(partial)), ingest_run_id],
        )


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
        if isinstance(n, bool) or not isinstance(n, int):
            raise TypeError("rows_written increment must be an integer")
        if n < 0:
            raise ValueError("rows_written increment must be non-negative")
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
    "UPDATE meta.ingest_runs SET status='success', finished_at=now(), rows_written=%s WHERE id=%s"
)
_ERROR_FIELD_LIMIT = 8000


@contextmanager
def ingest_run(
    source: str,
    endpoint: str | None = None,
    metadata: Mapping[str, Any] | None = None,
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
    except BaseException as exc:
        original_exc = exc
        try:
            with pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        _INGEST_RUN_FAIL,
                        [str(original_exc)[:_ERROR_FIELD_LIMIT], handle.rows_written, handle.id],
                    )
                conn.commit()
        except Exception:
            _LOGGER.exception("Failed to mark ingest run %s as failed", handle.id)
        raise
    else:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_INGEST_RUN_SUCCESS, [handle.rows_written, handle.id])
            conn.commit()
