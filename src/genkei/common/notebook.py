"""Notebook / experiment session helpers (B-054 + B-055).

The reproducible-experiments layer over the data lake. Two concerns:

**Querying (B-055).** ``get_session()`` hands a notebook a lightweight handle
that runs read-only SQL through the same pooled, read-path connection the CLI
uses (``genkei.common.db``), returning either plain ``list[dict]`` rows
(``read_sql_rows``) or a pandas ``DataFrame`` (``read_sql_df``). Module-level
``read_sql_rows`` / ``read_sql_df`` convenience functions cover the one-shot
case. Nothing here writes — every path is a plain ``SELECT`` cursor.

**Reproducibility (B-054).** An experiment is only trustworthy if you can say
*exactly which data it ran against* and *rerun it deterministically*. Two
helpers make that cheap:

  * ``snapshot_manifest()`` captures the latest successful ``meta.ingest_runs``
    id per ``(source, endpoint)`` — the precise snapshot of the lake an
    experiment consumed. Pinning these ids means a later reader can tell
    whether a re-run saw the same data or newer data.
  * ``set_seeds()`` seeds ``random`` (and NumPy, if installed) from one seed
    so any sampling / shuffling is deterministic.

``write_manifest()`` bundles the seed, an arbitrary config dict, and the
snapshot manifest into a ``manifest.json`` next to the notebook, and
``new_experiment()`` scaffolds a dated experiment folder from the template so
every study has the same shape (this is also most of B-063's one-command
bootstrap).

**Why pandas is imported lazily.** The core package + offline test suite must
install and run without pandas/jupyter (they're the ``[notebooks]`` extra). So
``read_sql_rows`` / ``snapshot_manifest`` / ``set_seeds`` are pandas-free and
fully usable core-only; only ``read_sql_df`` pulls pandas in, with a clear
install hint if it's missing.
"""

from __future__ import annotations

import json
import random
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from genkei.common import db

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    import pandas as pd

# Repo-root-relative home for experiment folders. Resolved from this file's
# location (src/genkei/common/notebook.py -> repo root is four parents up) so
# it's correct regardless of the caller's working directory.
_REPO_ROOT = Path(__file__).resolve().parents[3]
NOTEBOOKS_ROOT = _REPO_ROOT / "notebooks" / "experiments"
TEMPLATE_DIR = NOTEBOOKS_ROOT / "_template"

# Runs in these states produced usable data an experiment could have read;
# 'running' (in-flight) and 'failed' are excluded from a snapshot pin.
_USABLE_RUN_STATES = ("success", "partial")


def _row_to_jsonable(value: Any) -> Any:
    """Coerce a psycopg-returned cell into a JSON/DataFrame-friendly Python
    value. Decimals become floats (analysis wants numerics, not Decimal), and
    dates/datetimes pass through — pandas and json both handle them well
    enough, and ``write_manifest`` isoformats them via ``_json_default``."""
    if isinstance(value, Decimal):
        return float(value)
    return value


def read_sql_rows(
    sql: str,
    params: list[Any] | tuple[Any, ...] | None = None,
    *,
    conn: Any | None = None,
) -> list[dict[str, Any]]:
    """Run a read-only query and return a list of column-keyed dict rows.

    Pandas-free — usable in the core install. Pass ``conn`` to reuse an open
    connection (as ``NotebookSession`` does); omit it for a one-shot query
    that borrows and returns a pooled connection.
    """
    if conn is not None:
        return _fetch_dicts(conn, sql, params)
    with db.connection() as owned:
        return _fetch_dicts(owned, sql, params)


def _fetch_dicts(
    conn: Any, sql: str, params: list[Any] | tuple[Any, ...] | None
) -> list[dict[str, Any]]:
    """Execute ``sql`` on ``conn`` and zip each row against the cursor's
    column names into a dict, coercing Decimals to floats."""
    with conn.cursor() as cur:
        cur.execute(sql, params)
        if cur.description is None:
            return []
        columns = [d[0] for d in cur.description]
        # Index-based rather than zip(..., strict=) — the runtime venv is
        # Python 3.9, where zip() has no strict kwarg. Column count and row
        # width always agree (both come from the same cursor).
        return [
            {columns[i]: _row_to_jsonable(val) for i, val in enumerate(row)}
            for row in cur.fetchall()
        ]


def read_sql_df(
    sql: str,
    params: list[Any] | tuple[Any, ...] | None = None,
    *,
    conn: Any | None = None,
) -> pd.DataFrame:
    """Run a read-only query and return a pandas ``DataFrame``.

    Requires the ``[notebooks]`` extra (pandas). Builds the frame from
    ``read_sql_rows`` so the column set is preserved even for an empty result.
    """
    pd = _require_pandas()
    rows = read_sql_rows(sql, params, conn=conn)
    if not rows:
        # Preserve column names on an empty result so downstream .empty checks
        # and column references don't KeyError. psycopg gives us the columns
        # via a describe-only pass.
        return pd.DataFrame(columns=_describe_columns(sql, params, conn=conn))
    return pd.DataFrame(rows)


def _describe_columns(
    sql: str, params: list[Any] | tuple[Any, ...] | None, *, conn: Any | None
) -> list[str]:
    """Return the column names a query would produce, without materializing
    rows — used to give an empty DataFrame the right (named) columns."""

    def _cols(c: Any) -> list[str]:
        with c.cursor() as cur:
            cur.execute(sql, params)
            if cur.description is None:
                return []
            return [d[0] for d in cur.description]

    if conn is not None:
        return _cols(conn)
    with db.connection() as owned:
        return _cols(owned)


def _require_pandas() -> Any:
    """Import pandas, raising a clear install hint if the extra isn't present."""
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise ImportError(
            "read_sql_df / DataFrame output needs pandas. Install the notebooks "
            'extra: pip install -e ".[notebooks]"'
        ) from exc
    return pd


@dataclass
class NotebookSession:
    """A read-only lake handle for a notebook or experiment script.

    Holds one pooled connection for the life of the session so a notebook
    doesn't borrow/return a connection per cell. Use as a context manager
    (``with get_session() as s:``) or call :meth:`close` when done. Every
    query method is read-only.
    """

    _conn: Any
    _pool_ctx: Any = None

    def read_sql_rows(
        self, sql: str, params: list[Any] | tuple[Any, ...] | None = None
    ) -> list[dict[str, Any]]:
        """Query, returning column-keyed dict rows (pandas-free)."""
        return read_sql_rows(sql, params, conn=self._conn)

    def read_sql_df(
        self, sql: str, params: list[Any] | tuple[Any, ...] | None = None
    ) -> pd.DataFrame:
        """Query, returning a pandas ``DataFrame`` (needs the notebooks extra)."""
        return read_sql_df(sql, params, conn=self._conn)

    def snapshot_manifest(
        self, sources: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Latest usable ingest-run id per (source, endpoint) — see the
        module-level :func:`snapshot_manifest`."""
        return snapshot_manifest(sources=sources, conn=self._conn)

    def close(self) -> None:
        """Return the underlying connection to the pool."""
        if self._pool_ctx is not None:
            self._pool_ctx.__exit__(None, None, None)
            self._pool_ctx = None

    def __enter__(self) -> NotebookSession:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def get_session() -> NotebookSession:
    """Open a :class:`NotebookSession` holding one pooled connection.

    ``with get_session() as s: s.read_sql_df("select ...")`` is the canonical
    notebook entry point. The connection is read-only in practice — the helper
    exposes only SELECT paths.
    """
    pool = db.get_pool()
    ctx = pool.connection()
    conn = ctx.__enter__()
    return NotebookSession(_conn=conn, _pool_ctx=ctx)


def snapshot_manifest(
    sources: list[str] | None = None,
    *,
    conn: Any | None = None,
) -> list[dict[str, Any]]:
    """Capture the latest usable ingest-run id per ``(source, endpoint)``.

    Returns one row per ``(source, endpoint)`` pair that has at least one
    ``success``/``partial`` run, carrying the run ``id`` and its timestamps —
    the exact snapshot of the lake an experiment consumed. Recording these
    ids in ``manifest.json`` lets a later reader pin (or diff) the data the
    result was computed from. Pass ``sources`` to restrict to the sources an
    experiment actually reads.
    """
    sql = """
        SELECT DISTINCT ON (source, endpoint)
               source, endpoint, id AS ingest_run_id, status,
               started_at, finished_at, rows_written
        FROM meta.ingest_runs
        WHERE status = ANY(%s::text[])
    """
    params: list[Any] = [list(_USABLE_RUN_STATES)]
    if sources:
        sql += " AND source = ANY(%s::text[])"
        params.append(list(sources))
    sql += " ORDER BY source, endpoint, started_at DESC"
    rows = read_sql_rows(sql, params, conn=conn)
    for row in rows:
        for key in ("started_at", "finished_at"):
            val = row.get(key)
            if isinstance(val, datetime):
                row[key] = val.isoformat()
    return rows


def set_seeds(seed: int = 0) -> int:
    """Seed ``random`` (and NumPy if installed) for deterministic sampling.

    Returns the seed so a notebook can record it in one line:
    ``seed = set_seeds(20260701)``. NumPy is seeded only when importable —
    the core install has no NumPy, and an experiment that doesn't sample
    doesn't need it.
    """
    random.seed(seed)
    try:  # pragma: no cover - env-dependent
        import numpy as np

        np.random.seed(seed)
    except ImportError:  # pragma: no cover - core install has no numpy
        pass
    return seed


def _json_default(value: Any) -> str:
    """JSON encoder fallback: isoformat dates/datetimes, str() Decimals."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def build_manifest(
    *,
    seed: int,
    config: dict[str, Any] | None = None,
    sources: list[str] | None = None,
    conn: Any | None = None,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    """Assemble (but don't write) the reproducibility manifest for an experiment.

    Bundles the seed, an arbitrary experiment config, and the current
    snapshot manifest. ``captured_at`` is injectable so tests are deterministic;
    it defaults to now (UTC).
    """
    stamp = captured_at or datetime.now(timezone.utc)
    return {
        "captured_at": stamp.isoformat(),
        "seed": seed,
        "config": config or {},
        "snapshot_runs": snapshot_manifest(sources=sources, conn=conn),
    }


def write_manifest(
    path: Path,
    *,
    seed: int,
    config: dict[str, Any] | None = None,
    sources: list[str] | None = None,
    conn: Any | None = None,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    """Write the reproducibility manifest to ``path`` and return it.

    ``path`` is usually ``manifest.json`` inside the experiment folder. Call
    this once near the top of a notebook after ``set_seeds`` so the run's data
    provenance is captured before any analysis.
    """
    manifest = build_manifest(
        seed=seed, config=config, sources=sources, conn=conn, captured_at=captured_at
    )
    path.write_text(
        json.dumps(manifest, indent=2, default=_json_default) + "\n", encoding="utf-8"
    )
    return manifest


def new_experiment(
    slug: str,
    *,
    on: date | None = None,
    root: Path | None = None,
    template_dir: Path | None = None,
) -> Path:
    """Scaffold a dated experiment folder from the template. Returns its path.

    Creates ``notebooks/experiments/<YYYY-MM-DD>-<slug>/`` by copying the
    ``_template`` folder, so every experiment starts with the same
    ``experiment.md`` + ``experiment.ipynb`` shape (most of B-063). Refuses to
    overwrite an existing folder. ``on`` and ``root`` are injectable for
    testing.
    """
    root = root or NOTEBOOKS_ROOT
    template = template_dir or (root / "_template")
    if not template.is_dir():
        raise FileNotFoundError(
            f"experiment template not found at {template} — expected the "
            "_template folder shipped with B-054."
        )
    if on is None:
        on = datetime.now(timezone.utc).date()
    folder = root / f"{on.isoformat()}-{slug}"
    if folder.exists():
        raise FileExistsError(f"experiment folder already exists: {folder}")
    shutil.copytree(template, folder)
    return folder
