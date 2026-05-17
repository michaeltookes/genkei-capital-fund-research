"""``genkei query`` — ad-hoc SQL escape hatch (B-045).

The typed subcommands (`prices`, `filings`, `macro`, `tvl`, `insiders`,
`insider-clusters`, `watchlist`) cover the common questions. ``query``
is for everything else: arbitrary SELECTs the typed surface doesn't
express. The "on-demand AI researcher" use case from CLAUDE.md hinges
on this — once the agent runs out of typed subcommands, ad-hoc SQL is
the only way to keep going without waiting for a new code path to
ship.

Three safety guards (per B-045 acceptance criteria):

1. **Read-only Postgres transaction.** Every query runs inside
   ``BEGIN READ ONLY ... ROLLBACK``. The Postgres engine itself
   rejects writes (INSERT / UPDATE / DELETE / DDL) — no schema-aware
   parsing of the user's SQL required. Defense in depth: even if a
   misbehaving query somehow slipped past, we never COMMIT.

2. **Server-side query timeout.** ``SET LOCAL statement_timeout`` per
   query (default 30 s, max 300 s). The server cancels long-running
   queries; the client gets a ``QueryCanceled`` error.

3. **Result-row cap.** The user's SQL is wrapped in
   ``SELECT * FROM (<user_sql>) AS q LIMIT %s`` so the cap is enforced
   server-side (no pulling 10M rows over the wire). Default 100, max
   100 000. If the user's own SQL has a tighter LIMIT, that wins; if
   looser, the wrap clips it.

Additional defenses:

* Multi-statement input (``;`` outside literals) is rejected at parse
  time. Keeps the surface unambiguous and prevents
  injection-shaped query bundling.
* Only positional SQL or ``--file`` input — no stdin pipe today.

Output formats: ``table`` (default, human), ``json``, ``csv``.

Usage:
  genkei query "SELECT count(*) FROM sec.form4_transactions"
  genkei query --file analyses/insider_buys_2024.sql --json
  genkei query "SELECT * FROM sec.facts LIMIT 5" --format csv
  genkei query "SELECT 1" --timeout-seconds 5
  genkei query "SELECT * FROM big" --limit 5000
"""

import csv
import io
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
from psycopg import errors as pg_errors

from genkei.common import db

DEFAULT_LIMIT = 100
MAX_LIMIT = 100_000
DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 300


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _strip_trailing_semicolons(sql: str) -> str:
    """Drop trailing whitespace/semicolons so the wrap is syntactically clean."""
    return sql.rstrip().rstrip(";").rstrip()


_DOLLAR_QUOTE_RE = re.compile(r"\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$")


def find_multi_statement_position(sql: str) -> Optional[int]:
    """Return the 0-based index of the first ``;`` outside literals/comments."""
    i = 0
    n = len(sql)
    in_single_quote = False
    in_escape_string = False
    in_identifier = False
    # pyproject requires Python >=3.10, but CLI modules keep
    # Optional[...] annotations for Typer/local harness compatibility.
    dollar_quote: Optional[str] = None
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
                # Postgres escapes ' inside string by doubling it ('')
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
                # Postgres escapes " inside quoted identifiers by doubling it ("")
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
                    return None
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
                return i
        i += 1
    return None


def wrap_query_for_safety(sql: str, *, limit: int) -> str:
    """Wrap ``sql`` so a server-side ``LIMIT`` clips the result set."""
    inner = _strip_trailing_semicolons(sql)
    return f"SELECT * FROM ({inner}) AS genkei_query_q LIMIT {limit}"


def _read_sql(sql_arg: Optional[str], file_arg: Optional[Path]) -> str:
    if sql_arg is not None and file_arg is not None:
        raise typer.BadParameter("Pass positional SQL OR --file, not both.")
    if sql_arg is None and file_arg is None:
        raise typer.BadParameter("Pass either a SQL string or --file path.")
    if file_arg is not None:
        try:
            return file_arg.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise typer.BadParameter(f"--file not found: {file_arg}") from exc
    assert sql_arg is not None
    return sql_arg


def _validate_sql(sql: str) -> str:
    """Strip leading/trailing whitespace, reject empty + multi-statement."""
    stripped = sql.strip()
    if not stripped:
        raise typer.BadParameter("SQL is empty.")
    # Inspect the body sans trailing ;s — a single trailing ; is fine
    # (we strip it before wrapping); a `;` *inside* the body indicates a
    # multi-statement payload.
    body = _strip_trailing_semicolons(stripped)
    if not body:
        raise typer.BadParameter("SQL is empty.")
    pos = find_multi_statement_position(body)
    if pos is not None:
        raise typer.BadParameter(
            "Multi-statement SQL not allowed (found `;` at position "
            f"{pos}). Pass one SELECT per invocation."
        )
    return stripped


# Postgres errors that surface as user-facing query problems rather
# than infra failures. We want to render these cleanly without a stack
# trace so the agent (or human) can iterate on the query.
USER_ERROR_TYPES: tuple[type[Exception], ...] = (
    pg_errors.QueryCanceled,
    pg_errors.SyntaxError,
    pg_errors.UndefinedColumn,
    pg_errors.UndefinedTable,
    pg_errors.UndefinedFunction,
    pg_errors.InsufficientPrivilege,
    pg_errors.ReadOnlySqlTransaction,
    pg_errors.GroupingError,
    pg_errors.DatatypeMismatch,
    pg_errors.InvalidTextRepresentation,
)


def execute_readonly(
    sql: str, *, limit: int, timeout_seconds: int
) -> tuple[list[str], list[tuple[Any, ...]]]:
    """Run ``sql`` in a READ ONLY transaction with a timeout + LIMIT wrap.

    Returns ``(column_names, rows)``. Raises the underlying psycopg
    error on failure; callers map known cases to clean stderr output.
    """
    wrapped = wrap_query_for_safety(sql, limit=limit)
    # SET LOCAL doesn't accept bind parameters — it needs a literal in
    # the SQL text. Safe because timeout_seconds is range-validated
    # int (1..MAX_TIMEOUT_SECONDS) before reaching this function.
    timeout_ms = int(timeout_seconds) * 1000
    with db.connection() as conn, conn.cursor() as cur:
        # READ ONLY + statement_timeout are scoped to this transaction
        # only — they don't leak past the connection-pool return.
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute(f"SET LOCAL statement_timeout = {timeout_ms}")
        cur.execute(wrapped)
        cols = [d.name for d in cur.description] if cur.description else []
        rows = list(cur.fetchall())
        # No COMMIT — db.connection()'s context manager handles it. The
        # READ ONLY guarantee means there's nothing to commit anyway.
    return cols, rows


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def _format_cell_for_table(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def format_table(
    cols: list[str], rows: list[tuple[Any, ...]], *, limit: int, capped: bool
) -> str:
    if not cols:
        return "(no result columns)"
    if not rows:
        return f"({', '.join(cols)}) — 0 rows"
    # Compute column widths from header + data
    rendered: list[list[str]] = [[_format_cell_for_table(v) for v in row] for row in rows]
    widths = [len(c) for c in cols]
    for row in rendered:
        for i, cell in enumerate(row):
            if i < len(widths) and len(cell) > widths[i]:
                widths[i] = len(cell)
    # Cap each column at 60 chars so a single huge JSONB doesn't blow
    # up the table layout.
    widths = [min(w, 60) for w in widths]
    sep = "  "
    out: list[str] = []
    header = sep.join(c[: widths[i]].ljust(widths[i]) for i, c in enumerate(cols))
    out.append(header)
    out.append(sep.join("-" * w for w in widths))
    for row in rendered:
        out.append(
            sep.join((cell[: widths[i]]).ljust(widths[i]) for i, cell in enumerate(row))
        )
    suffix = "(row cap)" if capped else ""
    out.append(f"({len(rows)} row{'s' if len(rows) != 1 else ''}, limit={limit}) {suffix}".strip())
    return "\n".join(out)


def format_json(cols: list[str], rows: list[tuple[Any, ...]]) -> str:
    # pyproject requires Python >=3.10, but the local harness may still
    # run under G-002's 3.9 venv. The psycopg cursor.description
    # guarantees cols and row have equal length, so strict=True would
    # be a no-op anyway. noqa keeps ruff B905 quiet on this intentional
    # omission.
    payload = [dict(zip(cols, row)) for row in rows]  # noqa: B905
    return json.dumps(payload, indent=2, default=_json_default)


def format_csv(cols: list[str], rows: list[tuple[Any, ...]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    writer.writerow(cols)
    for row in rows:
        writer.writerow(
            [
                ""
                if v is None
                else (v.isoformat() if hasattr(v, "isoformat") else str(v))
                for v in row
            ]
        )
    return buf.getvalue().rstrip("\n")


VALID_FORMATS = {"table", "json", "csv"}


def query_cmd(
    sql: Annotated[
        Optional[str],
        typer.Argument(help="Positional SQL string. Mutually exclusive with --file."),
    ] = None,
    file: Annotated[
        Optional[Path],
        typer.Option("--file", help="Read SQL from this path instead of positional arg."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option(
            "--limit",
            help=(
                "Server-side row cap wrapped around your query. "
                f"Default {DEFAULT_LIMIT}, max {MAX_LIMIT}."
            ),
            min=1,
        ),
    ] = DEFAULT_LIMIT,
    timeout_seconds: Annotated[
        int,
        typer.Option(
            "--timeout-seconds",
            help=(
                "Postgres statement_timeout in seconds. "
                f"Default {DEFAULT_TIMEOUT_SECONDS}, max {MAX_TIMEOUT_SECONDS}."
            ),
            min=1,
        ),
    ] = DEFAULT_TIMEOUT_SECONDS,
    fmt: Annotated[
        str,
        typer.Option(
            "--format",
            help="Output format: table (default) | json | csv.",
        ),
    ] = "table",
    json_out: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Shortcut for --format json (matches the convention in other subcommands).",
        ),
    ] = False,
) -> None:
    """Run an ad-hoc SELECT against the data lake (read-only, capped, timeouted)."""
    if limit > MAX_LIMIT:
        raise typer.BadParameter(f"--limit cannot exceed {MAX_LIMIT}.")
    if timeout_seconds > MAX_TIMEOUT_SECONDS:
        raise typer.BadParameter(
            f"--timeout-seconds cannot exceed {MAX_TIMEOUT_SECONDS}."
        )
    output_format = "json" if json_out else fmt
    if output_format not in VALID_FORMATS:
        raise typer.BadParameter(
            f"--format must be one of {sorted(VALID_FORMATS)}; got {fmt!r}."
        )

    raw_sql = _read_sql(sql, file)
    cleaned = _validate_sql(raw_sql)

    try:
        cols, rows = execute_readonly(
            cleaned, limit=limit + 1, timeout_seconds=timeout_seconds
        )
    except USER_ERROR_TYPES as exc:
        # Clean, agent-readable error line — no stack trace.
        kind = type(exc).__name__
        msg = str(exc).strip().splitlines()[0] if str(exc).strip() else "(no message)"
        # Pg messages sometimes include "DETAIL:" / "HINT:" on later
        # lines; the first line is the canonical error.
        msg = re.sub(r"\s+", " ", msg)
        typer.echo(f"query error [{kind}]: {msg}", err=True)
        raise typer.Exit(code=1) from exc

    capped = len(rows) > limit
    rows = rows[:limit]
    if output_format == "json":
        typer.echo(format_json(cols, rows))
    elif output_format == "csv":
        typer.echo(format_csv(cols, rows))
    else:
        typer.echo(format_table(cols, rows, limit=limit, capped=capped))
