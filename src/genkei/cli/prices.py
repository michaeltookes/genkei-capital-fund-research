"""``genkei prices`` — query crypto market data from the lake (B-039).

Today this only queries ``coingecko.market_data`` (per source schema +
backfill, this is the deepest crypto price history we have). Equity
prices are not yet ingested; ``genkei prices --ticker AAPL`` will fail
loudly with a hint pointing at the relevant backlog item rather than
silently returning empty.

Usage:
  genkei prices --ticker BTC                     latest price
  genkei prices --ticker BTC --since 2024-01-01  daily history since
  genkei prices --ticker BTC --json              machine-readable
  genkei prices --ticker BTC --since 2024-01-01 --until 2024-06-30 --limit 10
"""

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Optional

import typer

from genkei.cli._watchlist import (
    DEFAULT_WATCHLIST_PATH,
    CryptoEntry,
    Watchlist,
    load_watchlist,
)
from genkei.common import db


def _parse_date(raw: Optional[str], *, label: str) -> Optional[date]:
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise typer.BadParameter(f"--{label} must be YYYY-MM-DD: {raw}") from exc


def _query_coingecko_market_data(
    coingecko_id: str,
    *,
    since: Optional[date],
    until: Optional[date],
    limit: int,
) -> list[dict[str, Any]]:
    """Pull rows from coingecko.market_data ordered ts DESC."""
    sql = (
        "SELECT ts, price_usd, market_cap_usd, volume_usd "
        "FROM coingecko.market_data "
        "WHERE coingecko_id = %s"
    )
    params: list[Any] = [coingecko_id]
    if since is not None:
        sql += " AND ts >= %s"
        params.append(datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc))
    if until is not None:
        sql += " AND ts <= %s"
        params.append(datetime.combine(until, datetime.max.time(), tzinfo=timezone.utc))
    sql += " ORDER BY ts DESC LIMIT %s"
    params.append(limit)

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        {
            "ts": ts.isoformat(),
            "price_usd": float(price) if price is not None else None,
            "market_cap_usd": float(mcap) if mcap is not None else None,
            "volume_usd": float(vol) if vol is not None else None,
        }
        for (ts, price, mcap, vol) in rows
    ]


def _format_human(ticker: str, source: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return (
            f"No price rows for {ticker} (source: {source}). "
            "Try a wider --since or run the daily ingest."
        )
    header = f"{ticker} prices (source: {source}, {len(rows)} row{'s' if len(rows) != 1 else ''})"
    lines = [header, "-" * len(header)]
    lines.append(f"  {'timestamp':<25}  {'price (USD)':>14}  {'market cap':>16}  {'volume':>16}")
    for row in rows:
        price = f"{row['price_usd']:,.2f}" if row["price_usd"] is not None else "n/a"
        mcap = f"{row['market_cap_usd']:,.0f}" if row["market_cap_usd"] is not None else "n/a"
        vol = f"{row['volume_usd']:,.0f}" if row["volume_usd"] is not None else "n/a"
        lines.append(f"  {row['ts']:<25}  {price:>14}  {mcap:>16}  {vol:>16}")
    return "\n".join(lines)


def _resolve_ticker(ticker: str, watchlist: Watchlist) -> Optional[tuple[str, CryptoEntry]]:
    """Map a user-facing ticker to (source, crypto entry) — crypto only for now.

    Returns None if the ticker isn't crypto in the watchlist; the caller
    surfaces a friendly error referencing whichever case applied (equity
    not yet supported, ticker unknown).
    """
    crypto = watchlist.find_crypto(ticker)
    if crypto is not None:
        return ("coingecko", crypto)
    return None


def prices_cmd(
    ticker: Annotated[str, typer.Option("--ticker", "-t", help="Asset ticker, e.g. BTC.")],
    since: Annotated[
        Optional[str],
        typer.Option("--since", help="Start date (YYYY-MM-DD)."),
    ] = None,
    until: Annotated[
        Optional[str],
        typer.Option("--until", help="End date (YYYY-MM-DD)."),
    ] = None,
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
    """Show prices for a watchlist asset (crypto today, equities later)."""
    since_d = _parse_date(since, label="since")
    until_d = _parse_date(until, label="until")
    if since_d is not None and until_d is not None and since_d > until_d:
        raise typer.BadParameter("--since must be on or before --until.")

    try:
        watchlist = load_watchlist(config)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    resolved = _resolve_ticker(ticker, watchlist)
    if resolved is None:
        equity = watchlist.find_equity(ticker)
        if equity is not None:
            typer.echo(
                f"{ticker} is an equity in the watchlist (CIK {equity.cik}), but equity prices "
                "are not yet ingested. Track via a future Phase 2 source (e.g. Yahoo Finance / "
                "Alpha Vantage); SEC EDGAR (`genkei filings`) only covers filings + XBRL facts.",
                err=True,
            )
            raise typer.Exit(code=2)
        typer.echo(
            f"Ticker {ticker!r} not found in {config}. Add it under crypto or equities first.",
            err=True,
        )
        raise typer.Exit(code=2)

    source, entry = resolved
    if source == "coingecko":
        rows = _query_coingecko_market_data(
            entry.coingecko_id, since=since_d, until=until_d, limit=limit
        )
    else:  # pragma: no cover — single source today
        typer.echo(f"Unknown source resolution: {source}", err=True)
        raise typer.Exit(code=2)

    if json_out:
        typer.echo(json.dumps(rows, indent=2))
    else:
        typer.echo(_format_human(ticker.upper(), source, rows))
