"""``genkei prices`` — query crypto and equity market data from the lake.

Crypto prices come from ``coingecko.market_data`` (B-039) or
``coinbase.candles`` (B-035). Equity prices come from
``yahoo.candles`` (B-092). The ``--source`` flag controls the data
source; when omitted, crypto tickers default to CoinGecko and equity
tickers default to Yahoo.

Usage:
  genkei prices --ticker BTC                     latest price
  genkei prices --ticker AAPL --source yahoo     equity price
  genkei prices --ticker BTC --since 2024-01-01  daily history since
  genkei prices --ticker BTC --json              machine-readable
  genkei prices --ticker BTC --since 2024-01-01 --until 2024-06-30 --limit 10
"""

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Optional

import typer

from genkei.cli._helpers import emit_freshness_warning
from genkei.cli._helpers import json_default as _json_default
from genkei.cli._helpers import parse_date as _parse_date
from genkei.common import db
from genkei.common.freshness import (
    DEFAULT_MAX_SNAPSHOT_AGE_HOURS,
    snapshot_freshness,
)
from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    CryptoEntry,
    Watchlist,
    load_watchlist,
)


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


def _query_coinbase_candles(
    product: str,
    *,
    since: Optional[date],
    until: Optional[date],
    limit: int,
) -> list[dict[str, Any]]:
    """Pull rows from coinbase.candles ordered ts DESC.

    Returned shape mirrors the coingecko reader (ts/price_usd/…) so
    the renderer doesn't need to branch — market_cap is reported as
    None because exchanges don't carry market-cap data, and volume is
    the base-asset volume from the exchange (BTC for BTC-USD).
    """
    sql = (
        "SELECT ts, close, volume_base "
        "FROM coinbase.candles "
        "WHERE product = %s"
    )
    params: list[Any] = [product]
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
            "price_usd": float(close) if close is not None else None,
            "market_cap_usd": None,
            "volume_usd": float(vol) if vol is not None else None,
        }
        for (ts, close, vol) in rows
    ]


def _query_yahoo_candles(
    ticker: str,
    *,
    since: Optional[date],
    until: Optional[date],
    limit: int,
) -> list[dict[str, Any]]:
    """Pull rows from yahoo.candles ordered ts DESC.

    Returns the split-and-dividend-adjusted ``adj_close`` under
    ``price_usd`` (the right price for return calculations); the
    unadjusted ``close`` is preserved as ``close_unadjusted`` so a
    caller that cares about the tape price can still see it. Market
    cap isn't carried by Yahoo's chart endpoint — None.
    """
    sql = (
        "SELECT ts, close, adj_close, volume "
        "FROM yahoo.candles "
        "WHERE ticker = %s"
    )
    params: list[Any] = [ticker]
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
    out: list[dict[str, Any]] = []
    for ts, close, adj_close, vol in rows:
        # Prefer adj_close — split/dividend-adjusted is the right
        # input for return work. Fall back to close when adj_close is
        # NULL (very-new tickers, rare).
        price = adj_close if adj_close is not None else close
        out.append(
            {
                "ts": ts.isoformat(),
                "price_usd": float(price) if price is not None else None,
                "market_cap_usd": None,
                "volume_usd": float(vol) if vol is not None else None,
                "close_unadjusted": float(close) if close is not None else None,
            }
        )
    return out


def _fetch_latest_ts(sql: str, params: list[Any]) -> Optional[datetime]:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return row[0] if row and row[0] is not None else None


def _query_coingecko_latest_ts(coingecko_id: str) -> Optional[datetime]:
    return _fetch_latest_ts(
        "SELECT ts FROM coingecko.market_data "
        "WHERE coingecko_id = %s ORDER BY ts DESC LIMIT 1",
        [coingecko_id],
    )


def _query_coinbase_latest_ts(product: str) -> Optional[datetime]:
    return _fetch_latest_ts(
        "SELECT ts FROM coinbase.candles WHERE product = %s ORDER BY ts DESC LIMIT 1",
        [product],
    )


def _query_yahoo_latest_ts(ticker: str) -> Optional[datetime]:
    return _fetch_latest_ts(
        "SELECT ts FROM yahoo.candles WHERE ticker = %s ORDER BY ts DESC LIMIT 1",
        [ticker],
    )


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
    source: Annotated[
        str,
        typer.Option(
            "--source",
            help=(
                "Price source. `coingecko` (crypto, 365d window), `coinbase` "
                "(crypto, long history per B-035), or `yahoo` (equities, long "
                "history per B-092). Equity tickers default to `yahoo`; crypto "
                "tickers default to `coingecko`."
            ),
        ),
    ] = "",
    since: Annotated[
        Optional[str],
        typer.Option("--since", help="Start date (YYYY-MM-DD)."),
    ] = None,
    until: Annotated[
        Optional[str],
        typer.Option("--until", help="End date (YYYY-MM-DD)."),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="Max rows.", min=1)] = 30,
    max_snapshot_age_hours: Annotated[
        float,
        typer.Option(
            "--max-snapshot-age-hours",
            help=(
                "Warn on stderr when the freshest returned row is older than "
                "this many hours (default 36h, a daily-cadence cutoff). The "
                "--json row list on stdout is never altered."
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
    """Show prices for a watchlist asset (crypto via CoinGecko/Coinbase, equities via Yahoo)."""
    valid_sources = ("coingecko", "coinbase", "yahoo")
    if source and source not in valid_sources:
        raise typer.BadParameter(
            f"--source must be one of: {', '.join(valid_sources)}."
        )
    since_d = _parse_date(since, label="since")
    until_d = _parse_date(until, label="until")
    if since_d is not None and until_d is not None and since_d > until_d:
        raise typer.BadParameter("--since must be on or before --until.")

    try:
        watchlist = load_watchlist(config)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    crypto = watchlist.find_crypto(ticker)
    equity = watchlist.find_equity(ticker)
    benchmark = watchlist.find_benchmark(ticker)
    price_target = watchlist.find_yahoo_price_target(ticker)
    # Benchmarks and price-only targets share Yahoo storage with equities —
    # treat them as equity-shaped for routing purposes.
    yahoo_target = (
        equity is not None or benchmark is not None or price_target is not None
    )
    if crypto is None and not yahoo_target:
        typer.echo(
            f"Ticker {ticker!r} not found in {config}. "
            "Add it under crypto, equities, benchmarks, or yahoo_price_targets first.",
            err=True,
        )
        raise typer.Exit(code=2)

    # Default-source-by-asset-class: crypto → coingecko, equity/benchmark → yahoo.
    if not source:
        if crypto is not None and yahoo_target:
            typer.echo(
                f"Ticker {ticker!r} appears under both crypto and Yahoo-backed targets "
                f"in {config}. Pass --source coingecko, --source coinbase, "
                "or --source yahoo.",
                err=True,
            )
            raise typer.Exit(code=2)
        source = "yahoo" if yahoo_target else "coingecko"

    # Reject source/asset-class mismatches loudly. The previous "equity
    # has no prices yet" message rotted with B-092; replace with
    # actionable routing errors.
    if crypto is None and source != "yahoo":
        kind = (
            "benchmark"
            if benchmark is not None
            else "price-only target"
            if price_target is not None
            else "equity"
        )
        typer.echo(
            f"{ticker} is a {kind}; Yahoo-backed prices live in `yahoo.candles` "
            "(B-092). Use --source yahoo (or omit --source).",
            err=True,
        )
        raise typer.Exit(code=2)
    if not yahoo_target and source == "yahoo":
        typer.echo(
            f"{ticker} is crypto; Yahoo carries equity and benchmark prices only. "
            "Use --source coingecko (default) or --source coinbase.",
            err=True,
        )
        raise typer.Exit(code=2)

    if source == "coingecko":
        assert crypto is not None  # narrowed by class check above
        rows = _query_coingecko_market_data(
            crypto.coingecko_id, since=since_d, until=until_d, limit=limit
        )
    elif source == "coinbase":
        assert crypto is not None
        if not crypto.coinbase_product:
            typer.echo(
                f"{ticker} has no coinbase_product set in the watchlist. "
                "Add it under crypto: in watchlists.yml or use --source coingecko.",
                err=True,
            )
            raise typer.Exit(code=2)
        rows = _query_coinbase_candles(
            crypto.coinbase_product, since=since_d, until=until_d, limit=limit
        )
    else:  # source == "yahoo"
        # Yahoo serves watchlist equities, benchmarks, and price-only targets.
        if equity is not None:
            yahoo_symbol = equity.symbol
        elif benchmark is not None:
            yahoo_symbol = benchmark.symbol
        else:
            assert price_target is not None
            yahoo_symbol = price_target.symbol
        rows = _query_yahoo_candles(
            yahoo_symbol, since=since_d, until=until_d, limit=limit
        )

    # Freshness check on the freshest returned row (B-023). Crypto + equity
    # prices are daily, so the freshest candle should be ~a day old; older
    # means the ingest likely stalled. Warning goes to stderr only.
    source_table = {
        "coingecko": "coingecko.market_data",
        "coinbase": "coinbase.candles",
        "yahoo": "yahoo.candles",
    }[source]
    freshest_ts: Any = rows[0]["ts"] if rows else None
    if rows and until_d is not None:
        # A historical end date intentionally excludes recent candles; probe
        # the unbounded latest row so the warning reflects ingest freshness.
        if source == "coingecko":
            assert crypto is not None
            freshest_ts = _query_coingecko_latest_ts(crypto.coingecko_id)
        elif source == "coinbase":
            assert crypto is not None and crypto.coinbase_product is not None
            freshest_ts = _query_coinbase_latest_ts(crypto.coinbase_product)
        else:
            freshest_ts = _query_yahoo_latest_ts(yahoo_symbol)
    freshness = (
        snapshot_freshness(
            freshest_ts, source=source_table, max_age_hours=max_snapshot_age_hours
        )
        if freshest_ts
        else None
    )

    if json_out:
        typer.echo(json.dumps(rows, indent=2, default=_json_default))
    else:
        typer.echo(_format_human(ticker.upper(), source, rows))
    emit_freshness_warning(freshness, json_out=json_out)
