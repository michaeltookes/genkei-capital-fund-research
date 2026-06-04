"""``genkei etf-flows`` — spot crypto ETF daily activity (B-105).

Returns per-day aggregated daily-dollar-volume for the spot BTC and
ETH ETFs configured under ``etf_tickers:`` in watchlists.yml. The
underlying OHLCV lives in ``yahoo.candles`` (B-092 / B-102's existing
schema); this subcommand joins the watchlist's per-ticker asset tag
to filter the right set, then sums ``volume x close`` per day per
asset.

**Honest labeling note.** The original B-105 spec was built around
Farside's published daily *net flow* numbers (creations minus
redemptions). Farside + SoSoValue both Cloudflare-walled scripted
access on 2026-06-03; the pivot to Yahoo gives us volume + close
but NOT shares-outstanding deltas. So the output column is
``dollar_volume_usd_mm`` (= volume x close, in USD millions) — a
*magnitude proxy* for institutional ETF activity, NOT signed net
flow. Reading the column as "net flow" would be a lie. True signed
net flow needs SEC EDGAR primary-source filings; that's filed
separately.

Usage:
  genkei etf-flows --asset BTC                           latest N days
  genkei etf-flows --asset BTC --since 2025-01-01
  genkei etf-flows --asset ETH --by-ticker               per-ETF split
  genkei etf-flows --asset BTC --json
  genkei etf-flows --list-etfs                           list configured ETFs

Asset aliases accepted: BTC / bitcoin; ETH / ethereum / ether.
"""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, Optional

import typer

from genkei.cli._helpers import (
    json_default as _json_default,
)
from genkei.cli._helpers import (
    parse_date as _parse_date,
)
from genkei.common import db
from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    EtfTickerEntry,
    Watchlist,
    load_watchlist,
)

_ASSET_ALIASES: dict[str, str] = {
    "btc": "BTC",
    "bitcoin": "BTC",
    "eth": "ETH",
    "ethereum": "ETH",
    "ether": "ETH",
}


def _resolve_asset(raw: str) -> str:
    """Return the canonical BTC/ETH asset code for a user-provided alias."""
    key = raw.strip().lower()
    if key in _ASSET_ALIASES:
        return _ASSET_ALIASES[key]
    raise typer.BadParameter(
        f"Unknown asset {raw!r}. Valid options: BTC (or bitcoin) / ETH (or ethereum)."
    )


def _format_etf_list_human(watchlist: Watchlist) -> str:
    """Render configured spot ETF tickers grouped by underlying asset."""
    if not watchlist.etf_tickers:
        return "No etf_tickers configured. Edit watchlists.yml."
    lines = ["Configured spot crypto ETFs:", "-" * 28]
    by_asset: dict[str, list[EtfTickerEntry]] = {}
    for entry in watchlist.etf_tickers:
        by_asset.setdefault(entry.asset.upper(), []).append(entry)
    for asset in sorted(by_asset):
        lines.append(f"\n{asset}:")
        for e in by_asset[asset]:
            launch = f" launched={e.launch_date}" if e.launch_date else ""
            lines.append(f"  {e.ticker:<6} {e.issuer:<22}{launch}  {e.name}")
    return "\n".join(lines)


def _query_asset_aggregate(
    asset: str,
    tickers: list[str],
    *,
    since: Optional[date],
    until: Optional[date],
    limit: int,
) -> list[dict[str, Any]]:
    """Sum volume x close per day across all configured tickers for one asset."""
    if not tickers:
        return []
    sql = (
        "SELECT date_trunc('day', ts) AS flow_date, "
        "SUM(volume * close) / 1e6 AS dollar_volume_usd_mm, "
        "SUM(volume) AS total_share_volume, "
        "COUNT(DISTINCT ticker) AS reporting_etfs "
        "FROM yahoo.candles "
        "WHERE ticker = ANY(%s)"
    )
    params: list[Any] = [tickers]
    if since is not None:
        sql += " AND ts >= %s"
        params.append(datetime.combine(since, datetime.min.time()))
    if until is not None:
        sql += " AND ts <= %s"
        params.append(datetime.combine(until, datetime.max.time()))
    sql += " GROUP BY flow_date ORDER BY flow_date DESC LIMIT %s"
    params.append(limit)
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for flow_date, dollar_vol, share_vol, reporting in rows:
        out.append(
            {
                "asset": asset,
                "flow_date": flow_date.date().isoformat()
                if isinstance(flow_date, datetime)
                else (flow_date.isoformat() if isinstance(flow_date, date) else None),
                "dollar_volume_usd_mm": float(dollar_vol) if dollar_vol is not None else None,
                "total_share_volume": int(share_vol) if share_vol is not None else None,
                "reporting_etfs": int(reporting) if reporting is not None else None,
            }
        )
    return out


def _query_per_ticker(
    asset: str,
    tickers: list[str],
    *,
    since: Optional[date],
    until: Optional[date],
    limit: int,
) -> list[dict[str, Any]]:
    """Return per-ticker per-day activity rows for --by-ticker mode."""
    if not tickers:
        return []
    sql = (
        "SELECT ticker, date_trunc('day', ts) AS flow_date, "
        "volume * close / 1e6 AS dollar_volume_usd_mm, "
        "volume, close "
        "FROM yahoo.candles "
        "WHERE ticker = ANY(%s)"
    )
    params: list[Any] = [tickers]
    if since is not None:
        sql += " AND ts >= %s"
        params.append(datetime.combine(since, datetime.min.time()))
    if until is not None:
        sql += " AND ts <= %s"
        params.append(datetime.combine(until, datetime.max.time()))
    sql += " ORDER BY flow_date DESC, ticker LIMIT %s"
    params.append(limit)
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for ticker, flow_date, dollar_vol, vol, close in rows:
        out.append(
            {
                "asset": asset,
                "ticker": ticker,
                "flow_date": flow_date.date().isoformat()
                if isinstance(flow_date, datetime)
                else (flow_date.isoformat() if isinstance(flow_date, date) else None),
                "dollar_volume_usd_mm": float(dollar_vol) if dollar_vol is not None else None,
                "share_volume": int(vol) if vol is not None else None,
                "close": float(close) if close is not None else None,
            }
        )
    return out


def _format_aggregate_human(asset: str, rows: list[dict[str, Any]], horizon_tag: str) -> str:
    """Render aggregate asset-level ETF activity as a human-readable table."""
    if not rows:
        return (
            f"No yahoo.candles rows for the {asset} ETF basket. "
            "Has the yahoo collector run since the etf_tickers section was added? "
            "Try `python3 -m genkei.ingest.yahoo --backfill` first."
        )
    header = (
        f"{asset} spot ETF basket | aggregate | "
        f"horizon={horizon_tag} | {len(rows)} day{'s' if len(rows) != 1 else ''}"
    )
    lines = [header, "-" * len(header)]
    lines.append(
        f"  {'flow_date':<12} {'$_volume_mm':>14} {'share_volume':>16} {'reporting_etfs':>15}"
    )
    for row in rows:
        date_str = row["flow_date"] or "-"
        dv = (
            f"{row['dollar_volume_usd_mm']:>14,.1f}"
            if row["dollar_volume_usd_mm"] is not None
            else f"{'-':>14}"
        )
        sv = (
            f"{row['total_share_volume']:>16,}"
            if row["total_share_volume"] is not None
            else f"{'-':>16}"
        )
        rc = (
            f"{row['reporting_etfs']:>15}"
            if row["reporting_etfs"] is not None
            else f"{'-':>15}"
        )
        lines.append(f"  {date_str:<12} {dv} {sv} {rc}")
    lines.append("")
    lines.append(
        "  $_volume_mm = sum across configured ETFs of (volume x close) / 1e6. "
        "Magnitude proxy for institutional activity, NOT signed net flow."
    )
    return "\n".join(lines)


def _format_per_ticker_human(asset: str, rows: list[dict[str, Any]], horizon_tag: str) -> str:
    """Render per-ETF activity rows as a human-readable table."""
    if not rows:
        return f"No yahoo.candles rows for the {asset} ETF basket."
    header = (
        f"{asset} spot ETF basket | per-ticker | "
        f"horizon={horizon_tag} | {len(rows)} row{'s' if len(rows) != 1 else ''}"
    )
    lines = [header, "-" * len(header)]
    lines.append(
        f"  {'flow_date':<12} {'ticker':<7} {'$_volume_mm':>14} {'share_volume':>16} {'close':>10}"
    )
    for row in rows:
        date_str = row["flow_date"] or "-"
        ticker = row["ticker"] or "-"
        dv = (
            f"{row['dollar_volume_usd_mm']:>14,.1f}"
            if row["dollar_volume_usd_mm"] is not None
            else f"{'-':>14}"
        )
        sv = (
            f"{row['share_volume']:>16,}"
            if row["share_volume"] is not None
            else f"{'-':>16}"
        )
        cl = f"{row['close']:>10,.2f}" if row["close"] is not None else f"{'-':>10}"
        lines.append(f"  {date_str:<12} {ticker:<7} {dv} {sv} {cl}")
    return "\n".join(lines)


def _horizon_tag(asset: str) -> str:
    """Return the canonical horizon tag for an ETF activity asset bucket."""
    return f"etf:crypto:{asset.lower()}"


def _tag_rows(rows: list[dict[str, Any]], horizon_tag: str) -> list[dict[str, Any]]:
    """Copy rows while attaching the ETF activity horizon tag."""
    return [{**row, "horizon_tag": horizon_tag} for row in rows]


def etf_flows_cmd(
    asset: Annotated[
        Optional[str],
        typer.Option(
            "--asset",
            "-a",
            help="Underlying asset: BTC (or bitcoin) or ETH (or ethereum).",
        ),
    ] = None,
    since: Annotated[
        Optional[str],
        typer.Option("--since", help="Earliest flow_date (YYYY-MM-DD)."),
    ] = None,
    until: Annotated[
        Optional[str],
        typer.Option("--until", help="Latest flow_date (YYYY-MM-DD)."),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="Max rows.", min=1)] = 60,
    by_ticker: Annotated[
        bool,
        typer.Option("--by-ticker", help="Per-ETF rows instead of asset-level aggregate."),
    ] = False,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON instead of human table."),
    ] = False,
    list_etfs: Annotated[
        bool,
        typer.Option(
            "--list-etfs",
            help="List configured spot crypto ETFs and exit.",
        ),
    ] = False,
    config: Annotated[
        Path,
        typer.Option("--config", help="Watchlist path.", show_default=True),
    ] = DEFAULT_WATCHLIST_PATH,
) -> None:
    """Show spot crypto ETF daily activity as volume x close per asset."""
    try:
        watchlist = load_watchlist(config)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    if list_etfs:
        if json_out:
            typer.echo(
                json.dumps(
                    [
                        {
                            "ticker": e.ticker,
                            "name": e.name,
                            "asset": e.asset,
                            "issuer": e.issuer,
                            "launch_date": e.launch_date,
                            "sleeve": e.sleeve,
                        }
                        for e in watchlist.etf_tickers
                    ],
                    indent=2,
                    default=_json_default,
                )
            )
        else:
            typer.echo(_format_etf_list_human(watchlist))
        return

    if asset is None:
        raise typer.BadParameter(
            "--asset is required (or use --list-etfs to enumerate configured ETFs)."
        )

    canonical_asset = _resolve_asset(asset)
    tickers = [e.ticker for e in watchlist.etfs_for_asset(canonical_asset)]
    if not tickers:
        raise typer.BadParameter(
            f"No ETFs configured for asset {canonical_asset}. "
            "Add entries under `etf_tickers:` in watchlists.yml."
        )

    since_d = _parse_date(since, label="since")
    until_d = _parse_date(until, label="until")
    if since_d is not None and until_d is not None and since_d > until_d:
        raise typer.BadParameter("--since must be on or before --until.")

    horizon_tag = _horizon_tag(canonical_asset)
    if by_ticker:
        rows = _query_per_ticker(
            canonical_asset, tickers, since=since_d, until=until_d, limit=limit
        )
    else:
        rows = _query_asset_aggregate(
            canonical_asset, tickers, since=since_d, until=until_d, limit=limit
        )
    rows = _tag_rows(rows, horizon_tag)
    if json_out:
        typer.echo(json.dumps(rows, indent=2, default=_json_default))
    elif by_ticker:
        typer.echo(_format_per_ticker_human(canonical_asset, rows, horizon_tag))
    else:
        typer.echo(_format_aggregate_human(canonical_asset, rows, horizon_tag))
