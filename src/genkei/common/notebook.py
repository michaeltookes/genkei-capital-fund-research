"""Notebook / experiment session helpers (B-054 + B-055).

The reproducible-experiments layer over the data lake. Two concerns:

**Querying (B-055).** ``get_session()`` hands a notebook a lightweight handle
that runs read-only SQL through the same pooled, read-path connection the CLI
uses (``genkei.common.db``), returning either plain ``list[dict]`` rows
(``read_sql_rows``) or a pandas ``DataFrame`` (``read_sql_df``). Module-level
``read_sql_rows`` / ``read_sql_df`` convenience functions cover the one-shot
case. Nothing here writes: SQL is rejected unless it is a single ``SELECT`` /
``WITH`` statement, and real Postgres connections run in read-only
transactions.

**Reproducibility (B-054).** An experiment is only trustworthy if you can say
*exactly which data it ran against* and *rerun it deterministically*. Two
helpers make that cheap:

  * ``write_manifest(..., data=df)`` captures the ``ingest_run_id`` values
    returned by an experiment query, then joins those exact ids back to
    ``meta.ingest_runs``. ``snapshot_manifest()`` still supports a coarse
    latest-run snapshot for legacy/template flows, but exact fact-row
    provenance is the preferred path.
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
import math
import random
import re
import shutil
from collections.abc import Iterable, Mapping
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

_READ_START_TOKENS = {"select", "with"}
_PROHIBITED_SQL_TOKENS = {
    "alter",
    "analyze",
    "call",
    "cluster",
    "copy",
    "create",
    "delete",
    "do",
    "drop",
    "execute",
    "grant",
    "insert",
    "merge",
    "reindex",
    "refresh",
    "revoke",
    "truncate",
    "update",
    "vacuum",
}
_DOLLAR_QUOTE_RE = re.compile(r"\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$")


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
    safe_sql = _validate_read_only_sql(sql)
    if conn is not None:
        return _fetch_dicts(conn, safe_sql, params)
    with db.connection() as owned:
        _set_transaction_read_only(owned)
        return _fetch_dicts(owned, safe_sql, params)


def _strip_trailing_semicolons(sql: str) -> str:
    """Drop trailing whitespace/semicolons so notebooks can paste ``SELECT ...;``."""
    return sql.rstrip().rstrip(";").rstrip()


def _scan_sql_tokens(sql: str) -> tuple[list[str], int | None]:
    """Return SQL word tokens plus first unquoted semicolon position, if any."""
    tokens: list[str] = []
    i = 0
    n = len(sql)
    in_single_quote = False
    in_escape_string = False
    in_identifier = False
    dollar_quote: str | None = None
    block_comment_depth = 0

    while i < n:
        ch = sql[i]
        next_ch = sql[i + 1] if i + 1 < n else ""

        if block_comment_depth:
            if ch == "/" and next_ch == "*":
                block_comment_depth += 1
                i += 2
                continue
            if ch == "*" and next_ch == "/":
                block_comment_depth -= 1
                i += 2
                continue
        elif dollar_quote is not None:
            if sql.startswith(dollar_quote, i):
                i += len(dollar_quote)
                dollar_quote = None
                continue
        elif in_single_quote:
            if ch == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    i += 2
                    continue
                in_single_quote = False
                in_escape_string = False
            elif in_escape_string and ch == "\\":
                i += 2
                continue
        elif in_identifier:
            if ch == '"':
                if next_ch == '"':
                    i += 2
                    continue
                in_identifier = False
        else:
            dollar_match = _DOLLAR_QUOTE_RE.match(sql, i)
            if dollar_match is not None:
                dollar_quote = dollar_match.group(0)
                i = dollar_match.end()
                continue
            if ch == "-" and next_ch == "-":
                newline = sql.find("\n", i + 2)
                if newline == -1:
                    break
                i = newline + 1
                continue
            if ch == "/" and next_ch == "*":
                block_comment_depth = 1
                i += 2
                continue
            if ch == "'":
                in_single_quote = True
                in_escape_string = i > 0 and sql[i - 1] in {"e", "E"}
            elif ch == '"':
                in_identifier = True
            elif ch == ";":
                return tokens, i
            elif ch.isalpha() or ch == "_":
                start = i
                i += 1
                while i < n and (sql[i].isalnum() or sql[i] in {"_", "$"}):
                    i += 1
                tokens.append(sql[start:i].lower())
                continue
        i += 1

    return tokens, None


def _validate_read_only_sql(sql: str) -> str:
    """Reject empty, multi-statement, or write-capable notebook SQL."""
    body = _strip_trailing_semicolons(sql.strip())
    if not body:
        raise ValueError("Notebook SQL is empty.")
    tokens, semicolon_pos = _scan_sql_tokens(body)
    if semicolon_pos is not None:
        raise ValueError(
            "Notebook SQL must be a single read-only statement; found `;` "
            f"at position {semicolon_pos}."
        )
    if not tokens or tokens[0] not in _READ_START_TOKENS:
        found = tokens[0].upper() if tokens else "nothing"
        raise ValueError(
            "Notebook SQL is read-only; expected SELECT or WITH as the first "
            f"statement token, found {found}."
        )
    for token in tokens:
        if token in _PROHIBITED_SQL_TOKENS:
            raise ValueError(
                "Notebook SQL is read-only; prohibited token "
                f"{token.upper()} was found."
            )
    return body


def _set_transaction_read_only(conn: Any) -> None:
    """Ask Postgres to enforce read-only behavior for this transaction."""
    with conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")


def _fetch_dicts(
    conn: Any, sql: str, params: list[Any] | tuple[Any, ...] | None
) -> list[dict[str, Any]]:
    """Execute ``sql`` on ``conn`` and zip each row against the cursor's
    column names into a dict, coercing Decimals to floats."""
    safe_sql = _validate_read_only_sql(sql)
    with conn.cursor() as cur:
        cur.execute(safe_sql, params)
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
        safe_sql = _validate_read_only_sql(sql)
        with c.cursor() as cur:
            cur.execute(safe_sql, params)
            if cur.description is None:
                return []
            return [d[0] for d in cur.description]

    if conn is not None:
        return _cols(conn)
    with db.connection() as owned:
        _set_transaction_read_only(owned)
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
        """Manifest snapshot helper using this session connection."""
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
    _set_transaction_read_only(conn)
    return NotebookSession(_conn=conn, _pool_ctx=ctx)


def _is_ingest_run_id_column(name: Any) -> bool:
    if not isinstance(name, str):
        return False
    return (
        name == "ingest_run_id"
        or name == "ingest_run_ids"
        or name.endswith("_ingest_run_id")
        or name.endswith("_ingest_run_ids")
    )


def _coerce_ingest_run_id(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("ingest_run_id values must be integers, not booleans")
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
    elif isinstance(value, float):
        if math.isnan(value):
            return None
        if value.is_integer():
            return int(value)
    elif isinstance(value, str) and value.strip():
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    raise ValueError(f"invalid ingest_run_id value: {value!r}")


def _dedupe_ingest_run_ids(values: Iterable[Any]) -> list[int]:
    seen: set[int] = set()
    ids: list[int] = []
    for value in values:
        run_id = _coerce_ingest_run_id(value)
        if run_id is None or run_id in seen:
            continue
        seen.add(run_id)
        ids.append(run_id)
    return ids


def _iter_data_row_mappings(data: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(data, Mapping):
        yield data
        return

    to_dict = getattr(data, "to_dict", None)
    rows = to_dict("records") if callable(to_dict) and hasattr(data, "columns") else data

    try:
        iterator = iter(rows)
    except TypeError as exc:
        raise ValueError(
            "manifest data must be a pandas DataFrame, a mapping, or an iterable "
            "of mappings with ingest_run_id columns"
        ) from exc

    for row in iterator:
        if not isinstance(row, Mapping):
            raise ValueError(
                "manifest data must be a pandas DataFrame, a mapping, or an iterable "
                "of mappings with ingest_run_id columns"
            )
        yield row


def ingest_run_ids_from_data(data: Any) -> list[int]:
    """Extract unique ``ingest_run_id`` values from query rows or a DataFrame.

    Columns named ``ingest_run_id``/``ingest_run_ids`` or ending with those
    suffixes are treated as provenance columns. Singular columns may contain
    scalar ids; plural columns may contain iterables of ids.
    """
    columns = getattr(data, "columns", None)
    raw_values: list[Any] = []
    saw_provenance_column = (
        any(_is_ingest_run_id_column(column) for column in columns)
        if columns is not None
        else False
    )
    for row in _iter_data_row_mappings(data):
        for key, value in row.items():
            if not _is_ingest_run_id_column(key):
                continue
            saw_provenance_column = True
            is_plural = key.endswith("_ingest_run_ids") or key == "ingest_run_ids"
            if (
                is_plural
                and isinstance(value, Iterable)
                and not isinstance(value, (str, bytes))
            ):
                raw_values.extend(value)
            else:
                raw_values.append(value)

    if not saw_provenance_column:
        raise ValueError(
            "manifest data must include an ingest_run_id column, e.g. "
            "`fact.ingest_run_id AS source_ingest_run_id`"
        )
    return _dedupe_ingest_run_ids(raw_values)


def snapshot_manifest(
    sources: list[str] | None = None,
    *,
    ingest_run_ids: Iterable[Any] | None = None,
    conn: Any | None = None,
) -> list[dict[str, Any]]:
    """Capture ingest-run metadata for an experiment manifest.

    Prefer ``ingest_run_ids`` when the experiment query selected provenance
    columns from the fact rows it consumed; those exact ids are then joined
    back to ``meta.ingest_runs``. If omitted, this falls back to the latest
    usable run per ``(source, endpoint)`` for legacy/template flows.
    """
    exact_ids = (
        _dedupe_ingest_run_ids(ingest_run_ids)
        if ingest_run_ids is not None
        else None
    )
    if exact_ids is not None and not exact_ids:
        return []

    if exact_ids is not None:
        sql = """
            SELECT source, endpoint, id AS ingest_run_id, status,
                   started_at, finished_at, rows_written, metadata
            FROM meta.ingest_runs
            WHERE id = ANY(%s::bigint[])
        """
        params: list[Any] = [exact_ids]
    else:
        sql = """
            SELECT DISTINCT ON (source, endpoint)
                   source, endpoint, id AS ingest_run_id, status,
                   started_at, finished_at, rows_written, metadata
            FROM meta.ingest_runs
            WHERE status = ANY(%s::text[])
        """
        params = [list(_USABLE_RUN_STATES)]
    if sources:
        sql += " AND source = ANY(%s::text[])"
        params.append(list(sources))
    sql += " ORDER BY source, endpoint, started_at DESC, id DESC"
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
    ingest_run_ids: Iterable[Any] | None = None,
    data: Any | None = None,
    conn: Any | None = None,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    """Assemble (but don't write) the reproducibility manifest for an experiment.

    Bundles the seed, an arbitrary experiment config, and the current
    snapshot manifest. ``captured_at`` is injectable so tests are deterministic;
    it defaults to now (UTC).
    """
    stamp = captured_at or datetime.now(timezone.utc)
    exact_ids: list[int] | None = None
    if ingest_run_ids is not None or data is not None:
        exact_ids = []
        if ingest_run_ids is not None:
            exact_ids.extend(_dedupe_ingest_run_ids(ingest_run_ids))
        if data is not None:
            exact_ids.extend(ingest_run_ids_from_data(data))
        exact_ids = _dedupe_ingest_run_ids(exact_ids)
    return {
        "captured_at": stamp.isoformat(),
        "seed": seed,
        "config": config or {},
        "snapshot_runs": snapshot_manifest(
            sources=sources, ingest_run_ids=exact_ids, conn=conn
        ),
    }


def write_manifest(
    path: Path,
    *,
    seed: int,
    config: dict[str, Any] | None = None,
    sources: list[str] | None = None,
    ingest_run_ids: Iterable[Any] | None = None,
    data: Any | None = None,
    conn: Any | None = None,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    """Write the reproducibility manifest to ``path`` and return it.

    ``path`` is usually ``manifest.json`` inside the experiment folder. Call
    this once near the top of a notebook after ``set_seeds`` so the run's data
    provenance is captured before any analysis.
    """
    manifest = build_manifest(
        seed=seed,
        config=config,
        sources=sources,
        ingest_run_ids=ingest_run_ids,
        data=data,
        conn=conn,
        captured_at=captured_at,
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
