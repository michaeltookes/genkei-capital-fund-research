"""``genkei crowding`` — 13F crowding monitor (B-061).

Thin CLI wrapper over ``genkei.experiments.crowding_monitor``. Surfaces
the most-crowded watchlist names per quarter and (more importantly)
the *delta* vs the prior quarter — new entrants and exits.

Scope flags (mutually exclusive):

* No flag — every CUSIP in the lake, latest available period.
* ``--ticker AAPL`` — one equity (resolves to CUSIP via watchlist).
* ``--cusip 037833100`` — one CUSIP directly.

Period framing (also mutually exclusive):

* No flag — latest ``period_of_report`` available.
* ``--period YYYY-MM-DD`` — that quarter only.
* ``--since / --until`` — every period in the inclusive range.
* ``--all-periods`` — full history present in the lake.

Other knobs:

* ``--min-holders N`` — only rows with ≥N holders at the *current*
  period (default 2). Note: this filters output rows, not the delta
  computation — a row showing exits at holder_count=1 is still
  computed against its prior period and surfaces if `--min-holders 1`.
* ``--top N`` — cap on rendered rows (default 25). Sort order is the
  detector's default: latest period first, most crowded first.
* ``--by-delta`` — sort by ``net_change`` desc instead of holder_count.
  Surfaces the biggest *adds* this quarter — closer to the actionable
  signal.

Usage:
  genkei crowding                                      latest period, all CUSIPs
  genkei crowding --period 2025-03-31
  genkei crowding --ticker AAPL --all-periods           AAPL crowding history
  genkei crowding --cusip 037833100 --since 2023-01-01
  genkei crowding --by-delta --top 10                   biggest adds this quarter
  genkei crowding --min-holders 3
  genkei crowding --json
"""

import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Optional

import typer

from genkei.cli._helpers import json_default as _json_default
from genkei.cli._helpers import parse_date as _parse_date
from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    EquityEntry,
    Watchlist,
    load_watchlist,
)
from genkei.experiments.crowding_monitor import (
    DEFAULT_MIN_HOLDERS,
    CrowdingRow,
    available_periods,
    compute_crowding,
    load_positions,
)


def _load_watchlist_or_exit(config: Path) -> Watchlist:
    try:
        return load_watchlist(config)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc


def _resolve_ticker_to_cusip(ticker: str, watchlist: Watchlist) -> tuple[str, EquityEntry]:
    equity = watchlist.find_equity(ticker)
    if equity is None:
        crypto = watchlist.find_crypto(ticker)
        if crypto is not None:
            raise typer.BadParameter(
                f"{ticker} is a crypto asset; 13F is an SEC equity filing. "
                "Use `genkei prices` for crypto market data."
            )
        raise typer.BadParameter(
            f"Ticker {ticker!r} not found in the equities watchlist."
        )
    if not equity.cusip:
        raise typer.BadParameter(
            f"{ticker} has no CUSIP in the watchlist — add `cusip:` to its entry "
            "in config/watchlists.yml (9-char SEC CUSIP) before running "
            f"`genkei crowding --ticker {ticker}`."
        )
    return equity.cusip, equity


def _ticker_for_cusip(cusip: str, watchlist: Watchlist) -> Optional[str]:
    entry = watchlist.find_equity_by_cusip(cusip)
    return entry.symbol if entry else None


def _horizon_tag(entry: EquityEntry) -> str:
    return f"equity:{entry.sleeve}:{entry.tier}"


def _horizon_for_cusip(cusip: str, watchlist: Watchlist) -> str:
    entry = watchlist.find_equity_by_cusip(cusip)
    return _horizon_tag(entry) if entry else "equity:unknown"


def _row_to_dict(row: CrowdingRow, *, ticker: Optional[str]) -> dict[str, Any]:
    return {
        "period_of_report": row.period_of_report.isoformat(),
        "cusip": row.cusip,
        "issuer_name": row.issuer_name,
        "ticker": ticker,
        "horizon_tag": row.horizon,
        "holder_count": row.holder_count,
        "holder_ciks": list(row.holder_ciks),
        "holder_names": list(row.holder_names),
        "total_value_usd": row.total_value_usd,
        "total_shares": row.total_shares,
        "prior_holder_count": row.prior_holder_count,
        "new_entrants": list(row.new_entrants),
        "exits": list(row.exits),
        "net_change": row.net_change,
    }


def _fmt_value(value: Optional[Decimal]) -> str:
    if value is None:
        return "n/a"
    return f"${float(value):,.0f}"


def _fmt_delta(net_change: Optional[int], prior: Optional[int]) -> str:
    if net_change is None or prior is None:
        return "new"
    sign = "+" if net_change >= 0 else ""
    return f"{sign}{net_change} ({prior}→{prior + net_change})"


def _format_human(
    rows: list[CrowdingRow],
    *,
    watchlist: Watchlist,
    period_label: str,
    by_delta: bool,
    min_holders: int,
) -> str:
    header_descriptor = "by net_change desc" if by_delta else "by holder_count desc"
    header = (
        f"13F crowding ({len(rows)} row(s), ≥{min_holders} holders, "
        f"{period_label}, {header_descriptor})"
    )
    if not rows:
        return (
            f"{header}\n"
            "  No crowded names. Lower --min-holders, widen --since/--until, "
            "or run `genkei watchlist health` to confirm sec.form13f_holdings has data."
        )
    lines = [header, "-" * len(header)]
    lines.append(
        f"  {'period':<12} {'tkr':<6} {'horizon':<20} {'cusip':<12} "
        f"{'#':>3} {'Δvs prior':<18} {'$value':>20}  top holders"
    )
    for r in rows:
        period = r.period_of_report.isoformat()
        tkr = _ticker_for_cusip(r.cusip, watchlist) or "-"
        value = f"{_fmt_value(r.total_value_usd):>20}"
        delta = _fmt_delta(r.net_change, r.prior_holder_count)
        names = ", ".join(n.split(",")[0] for n in r.holder_names[:3])
        if len(r.holder_names) > 3:
            names += f", +{len(r.holder_names) - 3} more"
        lines.append(
            f"  {period:<12} {tkr:<6} {r.horizon:<20} {r.cusip:<12} "
            f"{r.holder_count:>3} {delta:<18} {value}  {names}"
        )
    return "\n".join(lines)


def _resolve_period_scope(
    *,
    period: Optional[date],
    since: Optional[date],
    until: Optional[date],
    all_periods: bool,
    cusips_filter: Optional[list[str]],
) -> tuple[Optional[date], Optional[date], str]:
    """Pick the (since, until, label) tuple for the lake query.

    When no period flag is given AND ``--all-periods`` is false, default
    to the lake's latest available period. Avoids dumping the entire
    history just because the user asked the default question.
    """
    if period is not None:
        return period, period, f"period {period.isoformat()}"
    if all_periods:
        return None, None, "all periods"
    if since is not None or until is not None:
        bounds = f"{since or 'earliest'}..{until or 'latest'}"
        return since, until, f"range {bounds}"
    # No scope flag: pick the most recent period that has data.
    latest = _latest_period_available(cusips_filter)
    if latest is None:
        return None, None, "no periods available"
    return latest, latest, f"latest period {latest.isoformat()}"


def _latest_period_available(cusips_filter: Optional[list[str]]) -> Optional[date]:
    # Cheap dispatch — `available_periods` returns DESC-sorted dates.
    periods = available_periods()
    if cusips_filter and periods:
        # When the user passed --ticker / --cusip, restrict to periods
        # that actually have rows for that CUSIP. This avoids surfacing
        # "no data" when the latest period exists but the named CUSIP
        # hasn't been filed yet.
        from genkei.experiments.crowding_monitor import load_positions as _lp

        for p in periods:
            rows = _lp(since=p, until=p, cusips=cusips_filter)
            if rows:
                return p
        return None
    return periods[0] if periods else None


def crowding_cmd(
    ticker: Annotated[
        Optional[str],
        typer.Option(
            "--ticker",
            "-t",
            help="Equity ticker (resolves to CUSIP via the watchlist).",
        ),
    ] = None,
    cusip: Annotated[
        Optional[str],
        typer.Option("--cusip", help="9-char SEC CUSIP."),
    ] = None,
    period: Annotated[
        Optional[str],
        typer.Option(
            "--period", help="Single quarter-end (YYYY-MM-DD). Default: latest available."
        ),
    ] = None,
    since: Annotated[
        Optional[str],
        typer.Option("--since", help="Earliest period_of_report (YYYY-MM-DD)."),
    ] = None,
    until: Annotated[
        Optional[str],
        typer.Option("--until", help="Latest period_of_report (YYYY-MM-DD)."),
    ] = None,
    all_periods: Annotated[
        bool,
        typer.Option(
            "--all-periods",
            help="Return rows from every period in the lake (no period filter).",
        ),
    ] = False,
    min_holders: Annotated[
        int,
        typer.Option(
            "--min-holders",
            help="Render only rows with ≥N holders at the current period.",
            min=1,
        ),
    ] = DEFAULT_MIN_HOLDERS,
    by_delta: Annotated[
        bool,
        typer.Option(
            "--by-delta",
            help="Sort by net_change desc (biggest adds first) instead of holder_count.",
        ),
    ] = False,
    top: Annotated[
        int,
        typer.Option("--top", help="Max rows.", min=1),
    ] = 25,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
    config: Annotated[
        Path,
        typer.Option("--config", help="Watchlist path.", show_default=True),
    ] = DEFAULT_WATCHLIST_PATH,
) -> None:
    """Show the most-crowded watchlist names per quarter — and the deltas vs the prior quarter."""
    if ticker is not None and cusip is not None:
        raise typer.BadParameter("--ticker and --cusip are mutually exclusive.")
    if period is not None and all_periods:
        raise typer.BadParameter("--period and --all-periods are mutually exclusive.")
    if period is not None and (since is not None or until is not None):
        raise typer.BadParameter("--period and --since/--until are mutually exclusive.")
    if all_periods and (since is not None or until is not None):
        raise typer.BadParameter(
            "--all-periods and --since/--until are mutually exclusive."
        )

    period_d = _parse_date(period, label="period")
    since_d = _parse_date(since, label="since")
    until_d = _parse_date(until, label="until")
    if since_d is not None and until_d is not None and since_d > until_d:
        raise typer.BadParameter("--since must be on or before --until.")

    watchlist = _load_watchlist_or_exit(config)

    cusips_filter: Optional[list[str]] = None
    if ticker is not None:
        resolved_cusip, _entry = _resolve_ticker_to_cusip(ticker, watchlist)
        cusips_filter = [resolved_cusip]
    elif cusip is not None:
        cusips_filter = [cusip.strip().upper()]

    effective_since, effective_until, period_label = _resolve_period_scope(
        period=period_d,
        since=since_d,
        until=until_d,
        all_periods=all_periods,
        cusips_filter=cusips_filter,
    )

    # When asking about a single CUSIP across all periods (or a range),
    # we still want the prior-period delta — so we always pull *at least
    # one* prior period beyond ``effective_since`` for the detector to
    # diff against. For "latest period only" requests this becomes a
    # two-period query so the delta is meaningful.
    detector_since = _expand_since_for_delta(effective_since, cusips_filter=cusips_filter)

    positions = load_positions(
        since=detector_since,
        until=effective_until,
        cusips=cusips_filter,
    )
    rows = compute_crowding(positions)

    # Restrict to the *requested* period window for output, even though
    # the loader pulled an extra prior period for delta computation.
    visible_rows: list[CrowdingRow] = list(rows)
    if effective_since is not None:
        visible_rows = [r for r in visible_rows if r.period_of_report >= effective_since]
    if effective_until is not None:
        visible_rows = [r for r in visible_rows if r.period_of_report <= effective_until]
    visible_rows = [r for r in visible_rows if r.holder_count >= min_holders]
    if by_delta:
        visible_rows = sorted(
            visible_rows,
            key=lambda r: (
                -(r.net_change if r.net_change is not None else -10**9),
                -r.period_of_report.toordinal(),
                r.cusip,
            ),
        )
    visible_rows = [
        replace(r, horizon=_horizon_for_cusip(r.cusip, watchlist))
        for r in visible_rows
    ]
    visible_rows = visible_rows[:top]

    if json_out:
        out = [
            _row_to_dict(r, ticker=_ticker_for_cusip(r.cusip, watchlist))
            for r in visible_rows
        ]
        typer.echo(json.dumps(out, indent=2, default=_json_default))
    else:
        typer.echo(
            _format_human(
                visible_rows,
                watchlist=watchlist,
                period_label=period_label,
                by_delta=by_delta,
                min_holders=min_holders,
            )
        )


def _expand_since_for_delta(
    effective_since: Optional[date], *, cusips_filter: Optional[list[str]]
) -> Optional[date]:
    """Roll ``effective_since`` back one period so the detector sees prior state.

    If ``effective_since`` is None (caller already wants the whole
    history), no expansion is needed. Otherwise we find the
    most-recent period strictly before ``effective_since`` in the lake
    and use that as the loader's since-bound.
    """
    if effective_since is None:
        return None
    earlier_periods = [p for p in available_periods() if p < effective_since]
    if not earlier_periods:
        return effective_since
    if not cusips_filter:
        return earlier_periods[0]
    for period in earlier_periods:
        if load_positions(since=period, until=period, cusips=cusips_filter):
            return period
    return effective_since
