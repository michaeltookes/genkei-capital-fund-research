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
  genkei macro --series DGS10 --since 2024-01-01 --regime  regime per date
  genkei macro --series DGS10 --json
"""

import json
from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Optional

import typer

from genkei.cli._helpers import emit_freshness_warning
from genkei.cli._helpers import json_default as _json_default
from genkei.cli._helpers import parse_date as _parse_date
from genkei.common import db
from genkei.common.freshness import (
    DEFAULT_MAX_SNAPSHOT_AGE_HOURS,
    ingest_run_freshness,
)
from genkei.common.macro_regime import load_macro_regime_labels
from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    MacroEntry,
    Watchlist,
    load_watchlist,
)


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


# The regime view is anchored on DGS10 business days, so the prevailing regime
# for a weekend / holiday / monthly-print observation date sits a few days
# back. Load this many days before the earliest observation so even the first
# row has a prior regime to carry forward.
_REGIME_BACKFILL_DAYS = 10


def _load_regime_calendar(
    min_date: date, max_date: date
) -> list[tuple[date, str]]:
    """Load ``(ts, regime)`` from the regime view over a bounded window, ascending.

    One view evaluation for the whole annotation — the view fully materializes
    per call (~1s), so we must NOT re-query it per observation date.
    """
    return load_macro_regime_labels(
        since=min_date - timedelta(days=_REGIME_BACKFILL_DAYS),
        until=max_date,
        ascending=True,
    )


def _annotate_with_regime(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach each observation's prevailing macro regime (B-066).

    Resolves every row's observation date against ``analytics.macro_regime_per_date``
    (the B-059/B-096 view) *as-of* that date — the most recent regime label on
    or before it. The as-of lookup (not exact-date match) is what makes this
    correct for mixed-cadence FRED series: a monthly CPI print dated the 1st,
    or a daily yield on a market holiday, still resolves to the regime that was
    in force. Rows before the regime view's 2006 start get ``regime=None``.

    Each row gains ``regime`` and ``regime_as_of`` (the date the label is from,
    which lags the observation date whenever the regime is carried forward).
    """
    obs_dates = [
        date.fromisoformat(r["ts"][:10]) for r in rows if r.get("ts")
    ]
    if not obs_dates:
        return [{**r, "regime": None, "regime_as_of": None} for r in rows]
    calendar = _load_regime_calendar(min(obs_dates), max(obs_dates))
    calendar_ts = [ts for ts, _ in calendar]

    def _asof(obs: date) -> tuple[Optional[str], Optional[date]]:
        # Rightmost calendar entry with ts <= obs.
        idx = bisect_right(calendar_ts, obs) - 1
        if idx < 0:
            return None, None
        ts, regime = calendar[idx]
        return regime, ts

    annotated: list[dict[str, Any]] = []
    for row in rows:
        if not row.get("ts"):
            annotated.append({**row, "regime": None, "regime_as_of": None})
            continue
        regime, regime_ts = _asof(date.fromisoformat(row["ts"][:10]))
        annotated.append(
            {
                **row,
                "regime": regime,
                "regime_as_of": regime_ts.isoformat() if regime_ts is not None else None,
            }
        )
    return annotated


def _format_human(
    series_id: str,
    rows: list[dict[str, Any]],
    *,
    as_of: Optional[date],
    all_vintages: bool,
    horizon_tag: Optional[str] = None,
    with_regime: bool = False,
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
    regime_col = f"  {'regime':<18}" if with_regime else ""
    lines.append(
        f"  {'ts':<12} {'realtime_start':<16} {'realtime_end':<16} {'value':>16}{regime_col}"
    )
    for r in rows:
        ts = r["ts"][:10] if r["ts"] else "-"  # date portion
        rs = r["realtime_start"] or "-"
        re = r["realtime_end"] or "-"
        val = f"{r['value']:>16,.4f}" if r["value"] is not None else f"{'n/a':>16}"
        regime_cell = ""
        if with_regime:
            label = r.get("regime") or "n/a"
            # Flag when the label is carried forward from an earlier date (the
            # regime view is anchored on business days; a holiday/weekend or a
            # monthly print resolves to the prior in-force regime).
            carried = r.get("regime_as_of") and r["regime_as_of"] != ts
            suffix = f" (as of {r['regime_as_of']})" if carried else ""
            regime_cell = f"  {label + suffix:<18}"
        lines.append(f"  {ts:<12} {rs:<16} {re:<16} {val}{regime_cell}")
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
    regime: Annotated[
        bool,
        typer.Option(
            "--regime",
            help=(
                "Annotate each observation with the prevailing macro regime "
                "(risk_on / risk_off / easing / tightening_stress / mixed) "
                "as-of that date, from analytics.macro_regime_per_date."
            ),
        ),
    ] = False,
    limit: Annotated[int, typer.Option("--limit", help="Max rows.", min=1)] = 30,
    max_snapshot_age_hours: Annotated[
        float,
        typer.Option(
            "--max-snapshot-age-hours",
            help=(
                "Warn on stderr when the FRED pipeline's last successful run "
                "is older than this many hours (default 36h). Judged on the "
                "ingest run, not the observation date — FRED series have mixed "
                "cadence (daily/monthly/quarterly), so a weeks-old monthly "
                "observation is not stale. The --json stdout is never altered."
            ),
            min=1,
        ),
    ] = DEFAULT_MAX_SNAPSHOT_AGE_HOURS,
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
    since_d = _parse_date(since, label="since")
    until_d = _parse_date(until, label="until")
    as_of_d = _parse_date(as_of, label="as-of")
    if since_d is not None and until_d is not None and since_d > until_d:
        raise typer.BadParameter("--since must be on or before --until.")
    if all_vintages and as_of_d is not None:
        raise typer.BadParameter("--all-vintages and --as-of are mutually exclusive.")
    if regime and as_of_d is not None:
        raise typer.BadParameter(
            "--regime cannot be combined with --as-of until regime labels are "
            "vintage-aware."
        )
    if regime and all_vintages:
        raise typer.BadParameter(
            "--regime cannot be combined with --all-vintages until regime labels "
            "are vintage-aware."
        )

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
    if regime:
        rows = _annotate_with_regime(rows)

    # Freshness check on the FRED ingest pipeline (B-023). Observation ts is
    # the wrong signal here — a monthly series' freshest observation is
    # legitimately weeks old — so judge on the last successful fred normalize
    # run instead. Warning goes to stderr only.
    freshness = ingest_run_freshness(
        "fred", "normalize", max_age_hours=max_snapshot_age_hours
    )

    if json_out:
        typer.echo(json.dumps(rows, indent=2, default=_json_default))
    else:
        typer.echo(
            _format_human(
                series_id,
                rows,
                as_of=as_of_d,
                all_vintages=all_vintages,
                horizon_tag=horizon_tag,
                with_regime=regime,
            )
        )
    emit_freshness_warning(freshness, json_out=json_out)
