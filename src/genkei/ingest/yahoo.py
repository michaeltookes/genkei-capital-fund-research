"""Yahoo Finance equity OHLCV collector (B-092).

Fetches daily OHLCV candles for every entry in
``config/watchlists.yml::equities`` from Yahoo Finance's public chart
endpoint. Lands one raw blob per (ticker, window) into
``meta.raw_blobs``. The downstream normalizer
(``genkei.normalize.yahoo``) reads from those blobs.

**Why Yahoo Finance.** Equity counterpart to B-035 (Coinbase, crypto).
Free, no auth, US-accessible, daily OHLCV back to listing date (AAPL
to 1980, MSFT to 1986). Single request per ticker returns the full
range — no 300-candle chunking like Coinbase. The fallback documented
in `docs/backlog.md` B-092 is Stooq.com if Yahoo's API proves
unreliable; today's smoke (all 10 sampled tickers OK, AAPL 11,453
candles back to 1980) doesn't motivate ship-time fallback wiring.

**API shape.** ``GET /v8/finance/chart/<ticker>?interval=1d&period1=...&period2=...``
returns ``{chart: {result: [{meta, timestamp, indicators: {quote:
[{open, high, low, close, volume}], adjclose: [{adjclose}]}}]}}``.
Timestamps are unix seconds; quote and adjclose arrays are parallel
to ``timestamp``. ``period1`` / ``period2`` are unix seconds; passing
``period1=0`` requests from-listing-date.

**Auth.** None. Yahoo serves the v8 chart endpoint unauthenticated.
A browser-flavored User-Agent is sent because Yahoo occasionally
flags requests with sparse / empty UAs.

**Rate limit.** Yahoo doesn't publish explicit limits; community
guidance pegs it at ~1-2 req/sec for unauthenticated public access.
Capped at 2/sec — 28 watchlist equities × 1 call each = 14s for a
full daily collect, well within bounds.

**Modes.**

* **Daily** (default): fetch the trailing 14 days per ticker. The
  longer overlap (vs Coinbase's 7d) absorbs equity-calendar holidays
  + weekends + occasional Yahoo gaps. Idempotent — upserts overwrite
  any revised closes Yahoo publishes.
* **Backfill** (``--backfill``): one call per ticker with ``period1=0``
  pulls the full listing-date-to-today series in a single response.
  AAPL = 11,453 candles in one ~3MB response, ~1.5s to fetch.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx

from genkei.common import db
from genkei.common.http import HttpClient, RateLimit
from genkei.common.watchlist import DEFAULT_WATCHLIST_PATH, load_watchlist_or_exit

SOURCE_NAME = "yahoo"
COLLECT_ENDPOINT_LABEL = "collect"
BACKFILL_ENDPOINT_LABEL = "backfill"
YAHOO_BASE_URL = "https://query1.finance.yahoo.com"
CHART_BLOB_PREFIX = "chart_"
# Yahoo's public chart endpoint occasionally returns 429 for requests
# with sparse / empty UAs. A browser-flavored UA stays well under any
# moderate-bot heuristic.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; genkei-research/1.0; +https://github.com/)"
)
# Documented community ceiling is ~5 req/sec; we stay at 2 to be
# polite — full daily mode (28 equities × 1 call) completes in 14s.
DEFAULT_RATE_LIMIT = RateLimit.per_second(2)
DAILY_LOOKBACK_DAYS = 14
LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Target loading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EquityTarget:
    """A watchlist equity we want OHLCV for."""

    ticker: str  # Yahoo uses the same ticker shape (AAPL, MSFT, GOOG, etc.)


def load_equities(path: Path) -> list[EquityTarget]:
    """Read Yahoo-backed targets from watchlists.yml.

    All supported sections are equity-shaped from Yahoo's perspective (same
    chart endpoint, same blob shape, same ``yahoo.candles`` storage).
    Listing them in separate watchlist sections keeps the *semantic*
    distinction without complicating the ingester:

      * ``equities:`` — research targets (insider clusters, 8-K events,
        crowding analyses pick these up).
      * ``benchmarks:`` — comparators (SPY / QQQ / IWM); ``find_equity``
        intentionally skips them so equity-targeted analyses ignore them,
        but ``genkei prices --ticker SPY`` and the B-101 stack-outcome
        backtest's abnormal-return column both work as soon as the bars
        land.
      * ``etf_tickers:`` — spot crypto ETFs (B-105 pivot). Tagged with
        their underlying asset (BTC / ETH) so ``genkei etf-flows
        --asset BTC`` can aggregate per-asset daily-dollar-volume across
        the configured tickers.
      * ``yahoo_price_targets:`` — price-only/reflection targets. They
        route through ``genkei prices`` and this collector, but do not
        become equity research targets for SEC, insider, crowding, or
        scoring pipelines.

    Yahoo accepts the watchlist's bare ticker as-is for every US-listed
    equity / ETF / OTC symbol we care about today. If a future ticker needs a
    Yahoo-specific suffix (BRK-B, BHP.AX, etc.) the watchlist can grow a
    ``yahoo_ticker`` field — out of scope for v1.
    """
    watchlist = load_watchlist_or_exit(path)
    out: list[EquityTarget] = []
    seen: set[str] = set()
    for entry in watchlist.equities:
        symbol_key = entry.symbol.upper()
        if symbol_key in seen:
            # GOOG / GOOGL are both Alphabet (one share class each) —
            # different Yahoo tickers, so this only catches truly
            # duplicated entries (which would be a watchlist bug).
            raise SystemExit(f"Duplicate equity symbol in watchlist: {entry.symbol}")
        seen.add(symbol_key)
        out.append(EquityTarget(ticker=entry.symbol))
    for benchmark in watchlist.benchmarks:
        symbol_key = benchmark.symbol.upper()
        if symbol_key in seen:
            raise SystemExit(
                f"Benchmark symbol {benchmark.symbol!r} collides with an equity in the watchlist."
            )
        seen.add(symbol_key)
        out.append(EquityTarget(ticker=benchmark.symbol))
    for etf in watchlist.etf_tickers:
        symbol_key = etf.ticker.upper()
        if symbol_key in seen:
            # ETH (Grayscale Ethereum Mini Trust) is technically a valid
            # ticker collision risk in theory; the watchlist loader
            # uppers everything, so a real collision means a watchlist
            # bug (e.g. someone added "ETH" as an equity).
            raise SystemExit(
                f"ETF ticker {etf.ticker!r} collides with an existing watchlist entry."
            )
        seen.add(symbol_key)
        out.append(EquityTarget(ticker=etf.ticker))
    for target in watchlist.yahoo_price_targets:
        symbol_key = target.symbol.upper()
        if symbol_key in seen:
            raise SystemExit(
                f"Yahoo price target {target.symbol!r} collides with an existing "
                "watchlist entry."
            )
        seen.add(symbol_key)
        out.append(EquityTarget(ticker=target.symbol))
    if not out:
        raise SystemExit(
            "No equity / benchmark / ETF / Yahoo price-target entries in the watchlist; "
            "the Yahoo collector has nothing to do."
        )
    return out


# ---------------------------------------------------------------------------
# URL builder
# ---------------------------------------------------------------------------


def build_chart_url(
    ticker: str,
    *,
    period1: int,
    period2: int,
    interval: str = "1d",
) -> str:
    """Build the URL for one Yahoo chart call.

    ``period1`` / ``period2`` are unix seconds. ``period1=0`` requests
    from listing date.
    """
    params = {
        "interval": interval,
        "period1": str(period1),
        "period2": str(period2),
    }
    return f"{YAHOO_BASE_URL}/v8/finance/chart/{ticker}?{urlencode(params)}"


# ---------------------------------------------------------------------------
# Collect / backfill
# ---------------------------------------------------------------------------


def _build_http() -> HttpClient:
    """Build a Yahoo-flavored HttpClient with the browser-style UA."""
    return HttpClient(
        SOURCE_NAME,
        rate_limit=DEFAULT_RATE_LIMIT,
        user_agent=DEFAULT_USER_AGENT,
    )


def _exclusive_period_end(until: date) -> datetime:
    """Convert an inclusive date bound to Yahoo's exclusive period2 timestamp."""
    return datetime.combine(
        until + timedelta(days=1),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )


def collect(
    config_path: Path = DEFAULT_WATCHLIST_PATH,
    *,
    http: HttpClient | None = None,
    days: int = DAILY_LOOKBACK_DAYS,
) -> int:
    """Run the Yahoo daily collector once. Returns ``meta.ingest_runs.id``."""
    if days <= 0:
        raise ValueError("days must be a positive integer")
    equities = load_equities(config_path)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = now - timedelta(days=days)

    owns_http = http is None
    if http is None:
        http = _build_http()

    failures: list[dict[str, str]] = []
    try:
        with db.ingest_run(
            SOURCE_NAME,
            endpoint=COLLECT_ENDPOINT_LABEL,
            metadata={
                "watchlist_path": str(config_path),
                "ticker_count": len(equities),
                "lookback_days": days,
            },
        ) as run:
            written = 0
            for target in equities:
                if _fetch_window(
                    target, start, now, http, run.id, failures, blob_suffix=""
                ):
                    written += 1
            run.add_rows(written)
            if failures:
                db.record_partial_endpoints(run.id, failures)
                raise RuntimeError(
                    f"Yahoo fetch failed for {len(failures)} ticker(s); "
                    "no partial snapshot will be normalized."
                )
            return run.id
    finally:
        if owns_http:
            http.close()


def backfill(
    config_path: Path = DEFAULT_WATCHLIST_PATH,
    *,
    since: date | None = None,
    until: date | None = None,
    http: HttpClient | None = None,
) -> int:
    """Backfill every watchlist equity's full history (or a bounded window).

    Default: ``period1=0`` (listing-date-to-now). When ``since`` is
    provided, ``period1`` is bumped to ``since``. Yahoo returns the
    full requested range in a single response — no chunking, no
    pre-listing-window handling beyond Yahoo just returning fewer
    rows for newer tickers.
    """
    if since is not None and until is not None and until < since:
        raise SystemExit(f"--until {until} is before --since {since}")
    end_dt = (
        _exclusive_period_end(until)
        if until is not None
        else datetime.now(timezone.utc).replace(microsecond=0)
    )
    until_date = until if until is not None else end_dt.date()
    start_dt = (
        datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc)
        if since is not None
        else datetime(1970, 1, 1, tzinfo=timezone.utc)
    )
    if end_dt < start_dt:
        raise SystemExit(f"--until {end_dt} is before --since {start_dt}")
    equities = load_equities(config_path)

    owns_http = http is None
    if http is None:
        http = _build_http()

    failures: list[dict[str, str]] = []
    try:
        with db.ingest_run(
            SOURCE_NAME,
            endpoint=BACKFILL_ENDPOINT_LABEL,
            metadata={
                "watchlist_path": str(config_path),
                "ticker_count": len(equities),
                "since": start_dt.date().isoformat(),
                "until": until_date.isoformat(),
            },
        ) as run:
            written = 0
            since_iso = start_dt.date().isoformat()
            until_iso = until_date.isoformat()
            blob_suffix = f"_{since_iso}_{until_iso}"
            for target in equities:
                if _fetch_window(
                    target,
                    start_dt,
                    end_dt,
                    http,
                    run.id,
                    failures,
                    blob_suffix=blob_suffix,
                ):
                    written += 1
                LOGGER.info("Yahoo backfill: %s done", target.ticker)
            run.add_rows(written)
            if failures:
                db.record_partial_endpoints(run.id, failures)
                raise RuntimeError(
                    f"Yahoo backfill failed for {len(failures)} ticker(s); "
                    "see meta.ingest_runs.metadata.partial_endpoints for details."
                )
            return run.id
    finally:
        if owns_http:
            http.close()


def _fetch_window(
    target: EquityTarget,
    start: datetime,
    end: datetime,
    http: HttpClient,
    ingest_run_id: int,
    failures: list[dict[str, str]],
    *,
    blob_suffix: str,
) -> bool:
    """Fetch one (ticker, window) and persist the raw payload.

    ``blob_suffix`` is appended to the endpoint name so daily-mode
    blobs (``chart_<ticker>``) don't collide with backfill-mode
    blobs (``chart_<ticker>_<since>_<until>``). The normalizer's
    product-extraction handles both shapes.
    """
    endpoint_name = f"{CHART_BLOB_PREFIX}{target.ticker}{blob_suffix}"
    url = build_chart_url(
        target.ticker, period1=int(start.timestamp()), period2=int(end.timestamp())
    )
    try:
        payload = http.get_json(url)
    except (
        httpx.TimeoutException,
        httpx.NetworkError,
        httpx.HTTPStatusError,
        json.JSONDecodeError,
    ) as exc:
        LOGGER.warning("Yahoo fetch failed for %s: %s", endpoint_name, exc)
        failures.append({"name": endpoint_name, "url": url, "error": str(exc)})
        return False
    if not isinstance(payload, dict):
        failures.append(
            {
                "name": endpoint_name,
                "url": url,
                "error": f"unexpected payload type: {type(payload).__name__}",
            }
        )
        return False
    chart = payload.get("chart")
    if not isinstance(chart, dict):
        failures.append(
            {"name": endpoint_name, "url": url, "error": "missing 'chart' key"}
        )
        return False
    error = chart.get("error")
    if error is not None:
        # Yahoo's error shape is `{code, description}` under chart.error.
        msg = (
            error.get("description") if isinstance(error, dict) else str(error)
        ) or "unknown Yahoo error"
        LOGGER.warning("Yahoo error for %s: %s", endpoint_name, msg)
        failures.append({"name": endpoint_name, "url": url, "error": msg})
        return False
    db.store_raw_blob(ingest_run_id, endpoint_name, url, payload)
    return True


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect Yahoo Finance equity OHLCV into Postgres."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_WATCHLIST_PATH)
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Pull full history (period1=0) per ticker instead of the daily window.",
    )
    parser.add_argument(
        "--since",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help="Backfill start (YYYY-MM-DD). Default = listing date.",
    )
    parser.add_argument(
        "--until",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help="Backfill end (YYYY-MM-DD). Default = today.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DAILY_LOOKBACK_DAYS,
        help=f"Daily lookback window in days (default {DAILY_LOOKBACK_DAYS}).",
    )
    args = parser.parse_args(argv)
    if args.days <= 0:
        parser.error("--days must be a positive integer")
    return args


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if args.backfill:
        run_id = backfill(args.config, since=args.since, until=args.until)
        print(f"Yahoo backfill wrote ingest_run_id={run_id}")
    else:
        run_id = collect(args.config, days=args.days)
        print(f"Yahoo collector wrote ingest_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
