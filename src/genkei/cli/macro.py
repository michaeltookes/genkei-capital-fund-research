"""``genkei macro`` — query FRED macro series from the lake (B-042).

Returns observations from ``fred.observations``. The schema is
vintage-aware (D-013): the same (series_id, ts) can appear multiple
times with different ``realtime_start`` rows representing each
revision. Default behavior collapses to the *latest vintage* per ts
(what we'd believe today). ``--as-of YYYY-MM-DD`` pins to vintages
known on that date instead. ``--all-vintages`` returns every row,
including superseded revisions.

Usage:
  genkei macro --series DGS10                            latest value
  genkei macro --series DGS10 --since 2024-01-01         daily history
  genkei macro --series CPIAUCSL --since 2020 --until 2024
  genkei macro --series GDPC1 --as-of 2024-06-15         vintage as known
  genkei macro --series DGS10 --all-vintages --since 2024-01-01
  genkei macro --series DGS10 --json
"""

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Optional

import typer

from genkei.cli._helpers import json_default, parse_date
from genkei.common import db
from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    MacroEntry,
    Watchlist,
    load_watchlist,
)

_parse_date = parse_date


def _utc_start(value: date) -> datetime:
    return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)


def _utc_end(value: date) -> datetime:
    return datetime.combine(value, datetime.max.time(), tzinfo=timezone.utc)


def _query_observations(
    series_id: str,
    *,
    since: Optional[date],
    until: Optional[date],
    as_of: Optional[date],
    all_vintages: bool,
    limit: int,
) -> list[dict[str, Any]]:
    """Query fred.observations with vintage handling.

    The vintage-aware schema lets us answer two distinct questions:
    "what do we believe today" (default) and "what was believed on a
    given as-of date". --all-vintages exposes every revision row.
    """
    params: list[Any] = [series_id]
    where = "series_id = %s"
    if since is not None:
        where += " AND ts >= %s"
        params.append(_utc_start(since))
    if until is not None:
        where += " AND ts <= %s"
        params.append(_utc_end(until))
    if as_of is not None:
        # Only consider vintages that existed on or before --as-of.
        where += " AND realtime_start <= %s"
        params.append(as_of)

    if all_vintages:
        sql = (
            f"SELECT ts, realtime_start, realtime_end, value "
            f"FROM fred.observations WHERE {where} "
            f"ORDER BY ts DESC, realtime_start DESC LIMIT %s"
        )
        params.append(limit)
    else:
        # Collapse to one row per ts — the latest realtime_start that
        # satisfies the as-of constraint (if any). DISTINCT ON keeps
        # the first row per ts after the ORDER BY.
        sql = (
            f"SELECT DISTINCT ON (ts) ts, realtime_start, realtime_end, value "
            f"FROM fred.observations WHERE {where} "
            f"ORDER BY ts DESC, realtime_start DESC LIMIT %s"
        )
        params.append(limit)

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        {
            "ts": ts.isoformat() if ts is not None else None,
            "realtime_start": rs.isoformat() if rs is not None else None,
            "realtime_end": re.isoformat() if re is not None else None,
            "value": float(val) if val is not None else None,
        }
        for (ts, rs, re, val) in rows
    ]


def _format_human(
    series_id: str,
    rows: list[dict[str, Any]],
    *,
    as_of: Optional[date],
    all_vintages: bool,
    horizon_tag: Optional[str] = None,
) -> str:
    if not rows:
        hint = (
            "Widen --since or drop --as-of."
            if as_of is not None
            else "Widen --since, or check the series_id is in the watchlist."
        )
        tag = f" [horizon={horizon_tag}]" if horizon_tag is not None else ""
        return f"No observations for {series_id} (fred.observations).{tag} {hint}"
    vintage_tag = (
        "all-vintages"
        if all_vintages
        else (f"as-of {as_of.isoformat()}" if as_of is not None else "latest-vintage")
    )
    header = (
        f"{series_id} ({len(rows)} row{'s' if len(rows) != 1 else ''}, "
        f"{vintage_tag}{', horizon=' + horizon_tag if horizon_tag is not None else ''})"
    )
    lines = [header, "-" * len(header)]
    lines.append(
        f"  {'ts':<12} {'realtime_start':<16} {'realtime_end':<16} {'value':>16}"
    )
    for r in rows:
        ts = r["ts"][:10] if r["ts"] else "-"  # date portion
        rs = r["realtime_start"] or "-"
        re = r["realtime_end"] or "-"
        val = f"{r['value']:>16,.4f}" if r["value"] is not None else f"{'n/a':>16}"
        lines.append(f"  {ts:<12} {rs:<16} {re:<16} {val}")
    return "\n".join(lines)


def _resolve_series(series_id: str, watchlist: Watchlist) -> MacroEntry:
    """Validate the series_id is in the watchlist. Returns the canonical entry."""
    entry = watchlist.find_macro(series_id)
    if entry is None:
        raise typer.BadParameter(
            f"Series {series_id!r} not found in the macro watchlist. "
            "Add it under `macro_series:` in watchlists.yml first."
        )
    return entry


def _horizon_tag(entry: MacroEntry) -> str:
    return f"macro:{entry.sleeve}:{entry.tier}"


def _tag_rows(rows: list[dict[str, Any]], horizon_tag: str) -> list[dict[str, Any]]:
    return [{**row, "horizon_tag": horizon_tag} for row in rows]


def macro_cmd(
    series: Annotated[
        str, typer.Option("--series", "-s", help="FRED series id, e.g. DGS10.")
    ],
    since: Annotated[
        Optional[str],
        typer.Option("--since", help="Start observation date (YYYY-MM-DD)."),
    ] = None,
    until: Annotated[
        Optional[str],
        typer.Option("--until", help="End observation date (YYYY-MM-DD)."),
    ] = None,
    as_of: Annotated[
        Optional[str],
        typer.Option(
            "--as-of",
            help="Pin to vintages known on this date (YYYY-MM-DD). "
            "Defaults to latest known vintage.",
        ),
    ] = None,
    all_vintages: Annotated[
        bool,
        typer.Option(
            "--all-vintages",
            help="Return every revision row (no per-ts dedupe).",
        ),
    ] = False,
    limit: Annotated[int, typer.Option("--limit", help="Max rows.", min=1)] = 30,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON instead of human table."),
    ] = False,
    config: Annotated[
        Path,
        typer.Option("--config", help="Watchlist path.", show_default=True),
    ] = DEFAULT_WATCHLIST_PATH,
) -> None:
    """Show macro observations (FRED) for a watchlist series."""
    since_d = parse_date(since, label="since")
    until_d = parse_date(until, label="until")
    as_of_d = parse_date(as_of, label="as-of")
    if since_d is not None and until_d is not None and since_d > until_d:
        raise typer.BadParameter("--since must be on or before --until.")
    if all_vintages and as_of_d is not None:
        raise typer.BadParameter("--all-vintages and --as-of are mutually exclusive.")

    try:
        watchlist = load_watchlist(config)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    series_entry = _resolve_series(series, watchlist)
    series_id = series_entry.series_id
    horizon_tag = _horizon_tag(series_entry)

    rows = _query_observations(
        series_id,
        since=since_d,
        until=until_d,
        as_of=as_of_d,
        all_vintages=all_vintages,
        limit=limit,
    )
    rows = _tag_rows(rows, horizon_tag)
    if json_out:
        typer.echo(json.dumps(rows, indent=2, default=json_default))
    else:
        typer.echo(
            _format_human(
                series_id,
                rows,
                as_of=as_of_d,
                all_vintages=all_vintages,
                horizon_tag=horizon_tag,
            )
        )
