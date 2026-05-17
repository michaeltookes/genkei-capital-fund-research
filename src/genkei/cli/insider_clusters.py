"""``genkei insider-clusters`` — surface insider buy/sell clusters (B-060).

Thin CLI wrapper over ``genkei.experiments.insider_clusters``. The
detection logic lives in the experiments module so it can be tested
on synthetic data without DB access; this file handles flag parsing,
human / JSON output, and watchlist ticker resolution.

Default: **buy** clusters (open-market purchases by 2+ insiders within
7 days). Buy clusters are the rare, high-signal case — Buffett /
Klarman / Greenblatt all track them. Sell clusters are common and
weaker (10b5-1 plans, tax planning); ``--sell`` flips direction.

Issuer scope:
* No flag — every issuer in the lake (full watchlist).
* ``--ticker AAPL`` — one issuer (resolves to CIK via watchlist).

Usage:
  genkei insider-clusters                                buy clusters across watchlist
  genkei insider-clusters --since 2024-01-01
  genkei insider-clusters --ticker JPM --sell
  genkei insider-clusters --min-reporters 3 --window-days 14
  genkei insider-clusters --json
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Optional

import typer

from genkei.cli._watchlist import (
    DEFAULT_WATCHLIST_PATH,
    EquityEntry,
    Watchlist,
    load_watchlist,
)
from genkei.experiments.insider_clusters import (
    DEFAULT_MIN_REPORTERS,
    DEFAULT_WINDOW_DAYS,
    Cluster,
    detect_clusters,
    query_buy_candidates,
    query_sell_candidates,
)


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _parse_date(raw: Optional[str], *, label: str) -> Optional[date]:
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise typer.BadParameter(f"--{label} must be YYYY-MM-DD: {raw}") from exc


def _resolve_equity_or_exit(ticker: str, config: Path) -> EquityEntry:
    try:
        watchlist: Watchlist = load_watchlist(config)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    equity = watchlist.find_equity(ticker)
    if equity is None:
        crypto = watchlist.find_crypto(ticker)
        if crypto is not None:
            raise typer.BadParameter(
                f"{ticker} is a crypto asset; Form 4 is an SEC equity filing. "
                "Use `genkei prices` for crypto market data."
            )
        raise typer.BadParameter(f"Ticker {ticker!r} not found in the equities watchlist.")
    if equity.cik is None:
        raise typer.BadParameter(
            f"{ticker} has no CIK in the watchlist — add one before querying clusters."
        )
    return equity


def _ticker_for_cik(cik: str, watchlist: Watchlist) -> Optional[str]:
    """Reverse-lookup CIK → ticker for output formatting. Returns None if not in watchlist."""
    for e in watchlist.equities:
        if e.cik == cik:
            return e.symbol
    return None


def _cluster_to_dict(cluster: Cluster, *, ticker: Optional[str]) -> dict[str, Any]:
    return {
        "issuer_cik": cluster.issuer_cik,
        "issuer_ticker": ticker,
        "direction": cluster.direction,
        "window_start": cluster.window_start.isoformat(),
        "window_end": cluster.window_end.isoformat(),
        "span_days": (cluster.window_end - cluster.window_start).days,
        "reporter_count": cluster.reporter_count,
        "total_shares": cluster.total_shares,
        "total_value_usd": cluster.total_value_usd,
        "reporters": [
            {
                "reporter_cik": r.reporter_cik,
                "reporter_name": r.reporter_name,
                "shares": r.shares,
                "value_usd": r.value_usd,
                "is_officer": r.is_officer,
                "is_director": r.is_director,
                "is_ten_percent_owner": r.is_ten_percent_owner,
                "officer_title": r.officer_title,
            }
            for r in cluster.reporters
        ],
    }


def _format_human(
    clusters: list[Cluster],
    *,
    watchlist: Watchlist,
    direction: str,
    min_reporters: int,
    window_days: int,
    since: Optional[date],
    until: Optional[date],
) -> str:
    scope = "watchlist"
    bounds = []
    if since is not None:
        bounds.append(f"since {since.isoformat()}")
    if until is not None:
        bounds.append(f"until {until.isoformat()}")
    bounds_str = " " + " ".join(bounds) if bounds else ""
    header = (
        f"Insider {direction} clusters ({len(clusters)} found, "
        f"≥{min_reporters} reporters within {window_days}d, "
        f"{scope}{bounds_str})"
    )
    if not clusters:
        return (
            f"{header}\n"
            "  No clusters found. Try lowering --min-reporters, widening "
            "--window-days, removing --since, or check "
            "`genkei watchlist health` to confirm sec.form4_transactions has data."
        )
    lines = [header, "-" * len(header)]
    lines.append(
        f"  {'issuer':<8} {'date_range':<23} {'#':>3} {'shares':>14} "
        f"{'$value':>16}  reporters"
    )
    for c in clusters:
        ticker = _ticker_for_cik(c.issuer_cik, watchlist) or c.issuer_cik
        date_range = (
            f"{c.window_start.isoformat()}..{c.window_end.isoformat()}"
            if c.window_end > c.window_start
            else c.window_start.isoformat()
        )
        shares = f"{c.total_shares:,.0f}"
        value = (
            f"${c.total_value_usd:,.0f}" if c.total_value_usd is not None else "n/a"
        )
        reporters = ", ".join(_short_reporter(r) for r in c.reporters[:4])
        if len(c.reporters) > 4:
            reporters += f", +{len(c.reporters) - 4} more"
        lines.append(
            f"  {ticker:<8} {date_range:<23} {c.reporter_count:>3} "
            f"{shares:>14} {value:>16}  {reporters}"
        )
    return "\n".join(lines)


def _short_reporter(r: Any) -> str:
    name = (r.reporter_name or "?").split(",")[0]
    role = ""
    if r.is_officer and r.officer_title:
        role = f" ({r.officer_title})"
    elif r.is_director:
        role = " (dir)"
    elif r.is_ten_percent_owner:
        role = " (10%)"
    return f"{name}{role}"


def insider_clusters_cmd(
    ticker: Annotated[
        Optional[str],
        typer.Option(
            "--ticker",
            "-t",
            help="Scope to one equity ticker. Default: every issuer in the lake.",
        ),
    ] = None,
    sell: Annotated[
        bool,
        typer.Option(
            "--sell",
            help="Look for sell clusters instead of buy clusters (the default).",
        ),
    ] = False,
    min_reporters: Annotated[
        int,
        typer.Option(
            "--min-reporters",
            help="Minimum distinct reporters for a cluster.",
            min=2,
        ),
    ] = DEFAULT_MIN_REPORTERS,
    window_days: Annotated[
        int,
        typer.Option(
            "--window-days",
            help="Maximum span (first to last transaction) in days.",
            min=1,
        ),
    ] = DEFAULT_WINDOW_DAYS,
    since: Annotated[
        Optional[str],
        typer.Option("--since", help="Start transaction_date (YYYY-MM-DD)."),
    ] = None,
    until: Annotated[
        Optional[str],
        typer.Option("--until", help="End transaction_date (YYYY-MM-DD)."),
    ] = None,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
    config: Annotated[
        Path,
        typer.Option("--config", help="Watchlist path.", show_default=True),
    ] = DEFAULT_WATCHLIST_PATH,
) -> None:
    """Detect clusters of insider buys (default) or sells across watchlist issuers."""
    since_d = _parse_date(since, label="since")
    until_d = _parse_date(until, label="until")
    if since_d is not None and until_d is not None and since_d > until_d:
        raise typer.BadParameter("--since must be on or before --until.")

    issuer_ciks: Optional[list[str]] = None
    if ticker is not None:
        equity = _resolve_equity_or_exit(ticker, config)
        assert equity.cik is not None
        issuer_ciks = [equity.cik]

    try:
        watchlist = load_watchlist(config)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    direction = "sell" if sell else "buy"
    fetch = query_sell_candidates if sell else query_buy_candidates
    candidates = fetch(since=since_d, until=until_d, issuer_ciks=issuer_ciks)
    clusters = detect_clusters(
        candidates,
        direction=direction,
        min_reporters=min_reporters,
        window_days=window_days,
    )

    if json_out:
        payload = [
            _cluster_to_dict(c, ticker=_ticker_for_cik(c.issuer_cik, watchlist))
            for c in clusters
        ]
        typer.echo(json.dumps(payload, indent=2, default=_json_default))
    else:
        typer.echo(
            _format_human(
                clusters,
                watchlist=watchlist,
                direction=direction,
                min_reporters=min_reporters,
                window_days=window_days,
                since=since_d,
                until=until_d,
            )
        )
