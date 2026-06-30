"""``genkei etf-flows`` — spot crypto ETF daily activity (B-105 + B-107).

Two query modes, sharing the same ``--asset`` / ``--since`` / ``--until`` /
``--json`` surface:

  * **default (B-105 v1)** — daily dollar-volume per asset, summed across
    every configured ETF in ``etf_tickers``. Data sourced from
    ``yahoo.candles`` via ``SUM(volume x close)``. A *magnitude proxy* for
    institutional activity, NOT signed net flow.
  * **--net-flow (B-107 v2 / B-113)** — signed daily net flow per ETF from
    ``etf.fund_snapshots``: BlackRock IBIT/ETHA/ETHB (iShares product-screener
    feed, B-107) plus Bitwise BITB (product-page scrape, B-113).
    ``net_flow_usd = (shares_today - shares_yesterday) x nav_today`` computed
    via a SQL window function. This is the canonical creations-vs-redemptions
    signal — see ``docs/sources/spot-etf-net-flow.md`` for the investigation.

The default mode is preserved as the cross-issuer breadth view (covers all
~14 spot crypto ETFs); ``--net-flow`` is the deep view over whichever issuers
have landed snapshots in ``etf.fund_snapshots`` (BlackRock + Bitwise today).
The query filters by underlying asset + watchlist ticker, not by issuer, so a
new issuer's collector surfaces here automatically. The two modes are
complementary, not redundant: dollar-volume tells you "is this ETF trading
heavily today?", net flow tells you "are creations exceeding redemptions
today?".

Usage:
  genkei etf-flows --asset BTC                           dollar-volume (default)
  genkei etf-flows --asset BTC --net-flow                signed net flow (BlackRock)
  genkei etf-flows --asset ETH --net-flow --since 2026-04-01
  genkei etf-flows --asset BTC --by-ticker               per-ETF split (default mode)
  genkei etf-flows --asset BTC --json
  genkei etf-flows --list-etfs                           list configured ETFs

Asset aliases accepted: BTC / bitcoin; ETH / ethereum / ether.
"""

import json
from datetime import date, datetime, timezone
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
TickerLaunch = tuple[str, Optional[date]]


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


def _start_of_day_utc(day: date) -> datetime:
    """Return a UTC-aware midnight timestamp for TIMESTAMPTZ comparisons."""
    return datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)


def _end_of_day_utc(day: date) -> datetime:
    """Return a UTC-aware end-of-day timestamp for TIMESTAMPTZ comparisons."""
    return datetime.combine(day, datetime.max.time(), tzinfo=timezone.utc)


def _query_targets(etfs: list[EtfTickerEntry]) -> list[TickerLaunch]:
    """Carry watchlist ETF launch dates through to the query layer."""
    return [
        (entry.ticker, _parse_date(entry.launch_date, label=f"{entry.ticker} launch_date"))
        for entry in etfs
    ]


def _target_sql_params(targets: list[TickerLaunch]) -> tuple[list[str], list[Optional[datetime]]]:
    """Split ticker/date targets into arrays suitable for Postgres unnest."""
    tickers: list[str] = []
    launch_starts: list[Optional[datetime]] = []
    for ticker, launch_date in targets:
        tickers.append(ticker)
        launch_starts.append(_start_of_day_utc(launch_date) if launch_date is not None else None)
    return tickers, launch_starts


def _query_asset_aggregate(
    asset: str,
    targets: list[TickerLaunch],
    *,
    since: Optional[date],
    until: Optional[date],
    limit: int,
) -> list[dict[str, Any]]:
    """Sum volume x close per day across all configured tickers for one asset."""
    if not targets:
        return []
    tickers, launch_starts = _target_sql_params(targets)
    sql = (
        "SELECT (c.ts AT TIME ZONE 'UTC')::date AS flow_date, "
        "SUM(c.volume * c.close) / 1e6 AS dollar_volume_usd_mm, "
        "SUM(c.volume) AS total_share_volume, "
        "COUNT(DISTINCT c.ticker) AS reporting_etfs "
        "FROM yahoo.candles c "
        "JOIN unnest(%s::text[], %s::timestamptz[]) AS target(ticker, launch_ts) "
        "ON c.ticker = target.ticker "
        "WHERE (target.launch_ts IS NULL OR c.ts >= target.launch_ts)"
    )
    params: list[Any] = [tickers, launch_starts]
    if since is not None:
        sql += " AND c.ts >= %s"
        params.append(_start_of_day_utc(since))
    if until is not None:
        sql += " AND c.ts <= %s"
        params.append(_end_of_day_utc(until))
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
    targets: list[TickerLaunch],
    *,
    since: Optional[date],
    until: Optional[date],
    limit: int,
) -> list[dict[str, Any]]:
    """Return per-ticker per-day activity rows for --by-ticker mode."""
    if not targets:
        return []
    tickers, launch_starts = _target_sql_params(targets)
    sql = (
        "SELECT c.ticker, (c.ts AT TIME ZONE 'UTC')::date AS flow_date, "
        "c.volume * c.close / 1e6 AS dollar_volume_usd_mm, "
        "c.volume, c.close "
        "FROM yahoo.candles c "
        "JOIN unnest(%s::text[], %s::timestamptz[]) AS target(ticker, launch_ts) "
        "ON c.ticker = target.ticker "
        "WHERE (target.launch_ts IS NULL OR c.ts >= target.launch_ts)"
    )
    params: list[Any] = [tickers, launch_starts]
    if since is not None:
        sql += " AND c.ts >= %s"
        params.append(_start_of_day_utc(since))
    if until is not None:
        sql += " AND c.ts <= %s"
        params.append(_end_of_day_utc(until))
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


def _query_net_flow(
    asset: str,
    targets: list[TickerLaunch],
    *,
    since: Optional[date],
    until: Optional[date],
    limit: int,
) -> list[dict[str, Any]]:
    """Return signed-net-flow rows per BlackRock ETF for one underlying asset.

    Net flow is derived at query time via ``(shares - LAG(shares)) x nav``.
    Using ``LAG(shares) OVER (PARTITION BY ticker ORDER BY snapshot_date)``
    means the very first snapshot per ticker has a NULL ``shares_prev`` and
    therefore a NULL ``net_flow_usd`` row — that's intentional: net flow
    needs *two* snapshots to exist.

    The window unconditionally walks every snapshot for the ticker in the
    DB, not just rows in the ``since`` filter — otherwise the first row in
    the filter window would lose its predecessor and report NULL even when
    the predecessor snapshot exists. The outer ``WHERE`` clause filters the
    output rows after the window has resolved.
    """
    tickers = sorted({ticker.strip().upper() for ticker, _ in targets if ticker.strip()})
    if not tickers:
        return []
    sql = (
        "SELECT * FROM ("
        "  SELECT ticker, snapshot_date, asset, issuer, "
        "    nav_per_share_usd, total_net_assets_usd, shares_outstanding, "
        "    LAG(shares_outstanding) OVER (PARTITION BY ticker ORDER BY snapshot_date) "
        "      AS shares_prev, "
        "    (shares_outstanding "
        "     - LAG(shares_outstanding) OVER (PARTITION BY ticker ORDER BY snapshot_date)) "
        "    * nav_per_share_usd AS net_flow_usd "
        "  FROM etf.fund_snapshots "
        "  WHERE asset = %s AND ticker = ANY(%s::text[])"
        ") s "
        "WHERE 1=1"
    )
    params: list[Any] = [asset, tickers]
    if since is not None:
        sql += " AND snapshot_date >= %s"
        params.append(since)
    if until is not None:
        sql += " AND snapshot_date <= %s"
        params.append(until)
    sql += " ORDER BY snapshot_date DESC, ticker LIMIT %s"
    params.append(limit)
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for (
        ticker,
        snap_date,
        snap_asset,
        issuer,
        nav,
        tna,
        shares,
        _shares_prev,
        net_flow,
    ) in rows:
        out.append(
            {
                "asset": snap_asset,
                "ticker": ticker,
                "snapshot_date": snap_date.isoformat()
                if isinstance(snap_date, date)
                else None,
                "issuer": issuer,
                "nav_per_share_usd": float(nav) if nav is not None else None,
                "total_net_assets_usd": float(tna) if tna is not None else None,
                "shares_outstanding": float(shares) if shares is not None else None,
                "net_flow_usd": float(net_flow) if net_flow is not None else None,
            }
        )
    return out


def _format_net_flow_human(asset: str, rows: list[dict[str, Any]], horizon_tag: str) -> str:
    """Render signed-net-flow rows per BlackRock ETF as a human table."""
    if not rows:
        return (
            f"No etf.fund_snapshots rows for {asset}. "
            "Has an ETF snapshot collector run yet? "
            "Try `python3 -m genkei.ingest.ishares` (IBIT/ETHA/ETHB) or "
            "`python3 -m genkei.ingest.bitwise` (BITB) to populate."
        )
    header = (
        f"{asset} BlackRock ETF | signed net flow | "
        f"horizon={horizon_tag} | {len(rows)} row{'s' if len(rows) != 1 else ''}"
    )
    lines = [header, "-" * len(header)]
    lines.append(
        f"  {'snapshot_date':<14} {'ticker':<7} "
        f"{'nav_usd':>10} {'tna_usd_mm':>14} {'shares_mm':>14} {'net_flow_mm':>14}"
    )
    for row in rows:
        date_str = row["snapshot_date"] or "-"
        ticker = row["ticker"] or "-"
        nav = (
            f"{row['nav_per_share_usd']:>10,.2f}"
            if row["nav_per_share_usd"] is not None
            else f"{'-':>10}"
        )
        tna_mm = (
            f"{row['total_net_assets_usd'] / 1e6:>14,.1f}"
            if row["total_net_assets_usd"] is not None
            else f"{'-':>14}"
        )
        shares_mm = (
            f"{row['shares_outstanding'] / 1e6:>14,.2f}"
            if row["shares_outstanding"] is not None
            else f"{'-':>14}"
        )
        flow_mm = (
            f"{row['net_flow_usd'] / 1e6:>+14,.1f}"
            if row["net_flow_usd"] is not None
            else f"{'(first day)':>14}"
        )
        lines.append(
            f"  {date_str:<14} {ticker:<7} {nav} {tna_mm} {shares_mm} {flow_mm}"
        )
    lines.append("")
    lines.append(
        "  net_flow_mm = (shares_outstanding - shares_outstanding_prev) x nav, "
        "in USD millions. Signed: positive = net creations, negative = net redemptions."
    )
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
    net_flow: Annotated[
        bool,
        typer.Option(
            "--net-flow",
            help=(
                "Signed daily net flow per ETF from etf.fund_snapshots "
                "(BlackRock IBIT/ETHA/ETHB + Bitwise BITB). Default mode is "
                "dollar-volume from yahoo.candles."
            ),
        ),
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
    targets = _query_targets(watchlist.etfs_for_asset(canonical_asset))
    if not targets:
        raise typer.BadParameter(
            f"No ETFs configured for asset {canonical_asset}. "
            "Add entries under `etf_tickers:` in watchlists.yml."
        )

    since_d = _parse_date(since, label="since")
    until_d = _parse_date(until, label="until")
    if since_d is not None and until_d is not None and since_d > until_d:
        raise typer.BadParameter("--since must be on or before --until.")

    horizon_tag = _horizon_tag(canonical_asset)
    if net_flow:
        if by_ticker:
            raise typer.BadParameter(
                "--by-ticker is incompatible with --net-flow (the net-flow query is already "
                "per-ticker by design — every row is one ETF on one day)."
            )
        rows = _query_net_flow(
            canonical_asset,
            targets,
            since=since_d,
            until=until_d,
            limit=limit,
        )
    elif by_ticker:
        rows = _query_per_ticker(
            canonical_asset, targets, since=since_d, until=until_d, limit=limit
        )
    else:
        rows = _query_asset_aggregate(
            canonical_asset, targets, since=since_d, until=until_d, limit=limit
        )
    rows = _tag_rows(rows, horizon_tag)
    if json_out:
        typer.echo(json.dumps(rows, indent=2, default=_json_default))
    elif net_flow:
        typer.echo(_format_net_flow_human(canonical_asset, rows, horizon_tag))
    elif by_ticker:
        typer.echo(_format_per_ticker_human(canonical_asset, rows, horizon_tag))
    else:
        typer.echo(_format_aggregate_human(canonical_asset, rows, horizon_tag))
