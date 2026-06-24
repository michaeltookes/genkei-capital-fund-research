"""Coinbase Exchange OHLCV candle collector (B-035).

Fetches daily candles for every crypto entry that has a
``coinbase_product`` in ``config/watchlists.yml::crypto``. Lands one
raw blob per (product, window) into ``meta.raw_blobs``. The
downstream normalizer (``genkei.normalize.coinbase``) reads from
those blobs.

**Why Coinbase, not Binance.** B-035 was originally scoped as a
Binance public-data ingester but the homelab Beelink (and any US
GitHub-hosted runner) is geo-blocked from ``api.binance.com``:

  > "Service unavailable from a restricted location according to
  >  'b. Eligibility' in https://www.binance.com/en/terms"

Binance.US is the US-compliant subset but doesn't list PYTH (one of
our secondary tactical alts). Coinbase Exchange's public candles
endpoint covers all 7 of our watchlist coins, requires no auth,
allows US IPs, and has the longest history of US-accessible options
for BTC/ETH (2015+).

**API shape.** ``GET /products/<product>/candles`` returns a list of
``[timestamp, low, high, open, close, volume]`` arrays (note the
unusual column order — low/high before open/close). Daily granularity
is ``86400``. The endpoint enforces a hard **300-candle cap per call**;
asking for more returns a JSON error object instead of partial data.
Backfill walks 280-day chunks (oldest first) so a single re-run on
failure picks up cleanly.

**Modes.**

* **Daily** (default): fetch the last 7 days for every watchlist
  product. Idempotent — upserts overwrite the latest close-price
  revisions Coinbase publishes within 24h of each daily close.
* **Backfill** (``--backfill``): walk from ``--since`` to ``--until``
  (default: 2015-01-01 → today) in 280-day chunks. Per-product
  earliest-available varies — BTC-USD goes back to 2015-07, SUI-USD
  to 2023-05, PYTH-USD to 2023-11. The script tolerates empty windows
  (pre-listing dates) silently.

**Auth + rate limits.** No API key required. Public REST has a 10
req/sec ceiling per source IP; we cap at 5/sec to stay well below.
The full daily-mode run touches 7 products x 1 call = 7 calls (<2s).
A full historical backfill of BTC-USD (~4,000 days) takes 14 chunks
x 7 products = 98 calls (<25s).
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
from genkei.common.dates import iter_date_windows
from genkei.common.http import HttpClient, RateLimit
from genkei.common.watchlist import DEFAULT_WATCHLIST_PATH, load_watchlist

SOURCE_NAME = "coinbase"
COLLECT_ENDPOINT_LABEL = "collect"
BACKFILL_ENDPOINT_LABEL = "backfill"
COINBASE_BASE_URL = "https://api.exchange.coinbase.com"
CANDLES_BLOB_PREFIX = "candles_"
# Coinbase's documented public-REST ceiling is 10 req/sec per IP; we
# stay at 5/sec so a daily collect + a slow backfill can overlap
# without tripping the limit.
DEFAULT_RATE_LIMIT = RateLimit.per_second(5)
# The candles endpoint hard-caps at 300 rows per call. We chunk at 280
# to leave headroom for the off-by-one quirks (the API can return up
# to ~301 on edge cases when start/end land exactly on candle boundaries).
DAILY_GRANULARITY_SECONDS = 86_400
BACKFILL_CHUNK_DAYS = 280
DAILY_LOOKBACK_DAYS = 7
DEFAULT_BACKFILL_START = date(2015, 1, 1)
LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Target loading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProductTarget:
    """A Coinbase product (BTC-USD, ETH-USD, …) we want candles for."""

    symbol: str  # the watchlist ticker (BTC, ETH, …)
    product: str  # the Coinbase Exchange product (BTC-USD, …)


def load_products(path: Path) -> list[ProductTarget]:
    """Read ``crypto:`` from watchlists.yml, keep only entries with coinbase_product.

    Rejects duplicate product identifiers — every coin maps to exactly
    one product. A duplicate would silently double-fetch and cost rate
    budget for nothing.
    """
    try:
        watchlist = load_watchlist(path)
    except FileNotFoundError as exc:
        raise SystemExit(f"Watchlist file not found: {path}") from exc
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    out: list[ProductTarget] = []
    seen_products: set[str] = set()
    for entry in watchlist.crypto:
        if not entry.coinbase_product:
            LOGGER.info(
                "Coinbase: skipping %s (no coinbase_product in watchlist)", entry.symbol
            )
            continue
        if entry.coinbase_product in seen_products:
            raise SystemExit(
                f"Duplicate coinbase_product in watchlist: {entry.coinbase_product}"
            )
        seen_products.add(entry.coinbase_product)
        out.append(ProductTarget(symbol=entry.symbol, product=entry.coinbase_product))
    if not out:
        raise SystemExit(
            "No crypto entries have coinbase_product set; the collector has nothing to do."
        )
    return out


# ---------------------------------------------------------------------------
# URL builder
# ---------------------------------------------------------------------------


def build_candles_url(
    product: str,
    *,
    start: datetime,
    end: datetime,
    granularity: int = DAILY_GRANULARITY_SECONDS,
) -> str:
    """Build the URL for a single candles call.

    Coinbase accepts ``start``/``end`` as ISO-8601 in UTC; we format
    explicitly with the trailing ``Z`` so URLs are deterministic
    (useful for raw_blobs.url uniqueness).
    """
    params = {
        "granularity": str(granularity),
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return f"{COINBASE_BASE_URL}/products/{product}/candles?{urlencode(params)}"


def _chunk_windows(
    start: date, end: date, chunk_days: int = BACKFILL_CHUNK_DAYS
) -> list[tuple[date, date]]:
    """Split [start, end] into [chunk_days]-day inclusive windows.

    Thin wrapper over the shared ``common.dates.iter_date_windows`` that binds
    Coinbase's default chunk size (the candles endpoint's 300-row cap → 280-day
    windows). See that helper for the windowing contract.
    """
    return iter_date_windows(start, end, chunk_days=chunk_days)


# ---------------------------------------------------------------------------
# Collect / backfill
# ---------------------------------------------------------------------------


def collect(
    config_path: Path = DEFAULT_WATCHLIST_PATH,
    *,
    http: HttpClient | None = None,
    days: int = DAILY_LOOKBACK_DAYS,
) -> int:
    """Run the Coinbase daily collector once. Returns ``meta.ingest_runs.id``.

    The daily mode fetches the trailing ``days`` window per product.
    Default 7 days gives a comfortable overlap that absorbs missed
    runs without re-doing the full history.
    """
    products = load_products(config_path)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = now - timedelta(days=days)

    owns_http = http is None
    if http is None:
        http = HttpClient(SOURCE_NAME, rate_limit=DEFAULT_RATE_LIMIT)

    failures: list[dict[str, str]] = []
    try:
        with db.ingest_run(
            SOURCE_NAME,
            endpoint=COLLECT_ENDPOINT_LABEL,
            metadata={
                "watchlist_path": str(config_path),
                "product_count": len(products),
                "lookback_days": days,
            },
        ) as run:
            written = 0
            for target in products:
                if _fetch_window(target, start, now, http, run.id, failures):
                    written += 1
            run.add_rows(written)
            if failures:
                db.record_partial_endpoints(run.id, failures)
                raise RuntimeError(
                    f"Coinbase fetch failed for {len(failures)} product(s); "
                    "no partial snapshot will be normalized."
                )
            return run.id
    finally:
        if owns_http:
            http.close()


def backfill(
    config_path: Path = DEFAULT_WATCHLIST_PATH,
    *,
    since: date = DEFAULT_BACKFILL_START,
    until: date | None = None,
    http: HttpClient | None = None,
) -> int:
    """Walk [since, until] in 280-day chunks per product.

    Per-product earliest-available is product-dependent (BTC-USD 2015,
    SUI-USD 2023, etc.); the chunked walk just returns empty arrays
    for pre-listing windows and the script tolerates them silently.
    """
    end_date = until or datetime.now(timezone.utc).date()
    if end_date < since:
        raise SystemExit(f"--until {end_date} is before --since {since}")
    products = load_products(config_path)
    windows = _chunk_windows(since, end_date, BACKFILL_CHUNK_DAYS)

    owns_http = http is None
    if http is None:
        http = HttpClient(SOURCE_NAME, rate_limit=DEFAULT_RATE_LIMIT)

    failures: list[dict[str, str]] = []
    try:
        with db.ingest_run(
            SOURCE_NAME,
            endpoint=BACKFILL_ENDPOINT_LABEL,
            metadata={
                "watchlist_path": str(config_path),
                "product_count": len(products),
                "since": since.isoformat(),
                "until": end_date.isoformat(),
                "chunk_count": len(windows),
            },
        ) as run:
            written = 0
            for target in products:
                for chunk_start, chunk_end in windows:
                    start_dt = datetime.combine(
                        chunk_start, datetime.min.time(), tzinfo=timezone.utc
                    )
                    # +1 day on end so the inclusive end-of-day is captured
                    # by Coinbase's strict-less-than `end` filter.
                    end_dt = datetime.combine(
                        chunk_end + timedelta(days=1),
                        datetime.min.time(),
                        tzinfo=timezone.utc,
                    )
                    endpoint_name = (
                        f"{CANDLES_BLOB_PREFIX}{target.product}_"
                        f"{chunk_start.isoformat()}_{chunk_end.isoformat()}"
                    )
                    if _fetch_named_window(
                        target, start_dt, end_dt, endpoint_name, http, run.id, failures
                    ):
                        written += 1
                LOGGER.info("Coinbase backfill: %s done (%s chunks)", target.product, len(windows))
            run.add_rows(written)
            if failures:
                db.record_partial_endpoints(run.id, failures)
                raise RuntimeError(
                    f"Coinbase backfill failed for {len(failures)} window(s); "
                    "see meta.ingest_runs.metadata.partial_endpoints for details."
                )
            return run.id
    finally:
        if owns_http:
            http.close()


def _fetch_window(
    target: ProductTarget,
    start: datetime,
    end: datetime,
    http: HttpClient,
    ingest_run_id: int,
    failures: list[dict[str, str]],
) -> bool:
    """Daily-mode helper. One blob per product, named ``candles_<product>``."""
    endpoint_name = f"{CANDLES_BLOB_PREFIX}{target.product}"
    return _fetch_named_window(target, start, end, endpoint_name, http, ingest_run_id, failures)


def _fetch_named_window(
    target: ProductTarget,
    start: datetime,
    end: datetime,
    endpoint_name: str,
    http: HttpClient,
    ingest_run_id: int,
    failures: list[dict[str, str]],
) -> bool:
    """Fetch one (product, [start, end)) window and persist the raw payload.

    Returns ``True`` if a blob was written. Empty-array responses still
    write a blob — the normalizer treats it as "no data for this
    window" without error. Dict responses (Coinbase's error format)
    are recorded as failures.
    """
    url = build_candles_url(target.product, start=start, end=end)
    try:
        payload = http.get_json(url)
    except (
        httpx.TimeoutException,
        httpx.NetworkError,
        httpx.HTTPStatusError,
        json.JSONDecodeError,
    ) as exc:
        LOGGER.warning("Coinbase fetch failed for %s: %s", endpoint_name, exc)
        failures.append({"name": endpoint_name, "url": url, "error": str(exc)})
        return False
    if isinstance(payload, dict):
        # Coinbase returns {"message": "..."} on error rather than a
        # non-2xx status — surface as a failure even though the HTTP
        # call succeeded.
        msg = str(payload.get("message", payload))
        LOGGER.warning("Coinbase error for %s: %s", endpoint_name, msg)
        failures.append({"name": endpoint_name, "url": url, "error": msg})
        return False
    if not isinstance(payload, list):
        failures.append(
            {
                "name": endpoint_name,
                "url": url,
                "error": f"unexpected payload type: {type(payload).__name__}",
            }
        )
        return False
    db.store_raw_blob(ingest_run_id, endpoint_name, url, payload)
    return True


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect Coinbase Exchange OHLCV candles into Postgres."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_WATCHLIST_PATH)
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Walk historical chunks instead of the daily lookback window.",
    )
    parser.add_argument(
        "--since",
        type=lambda s: date.fromisoformat(s),
        default=DEFAULT_BACKFILL_START,
        help="Backfill start (YYYY-MM-DD). Default 2015-01-01.",
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv or sys.argv[1:])
    if args.backfill:
        run_id = backfill(args.config, since=args.since, until=args.until)
        print(f"Coinbase backfill wrote ingest_run_id={run_id}")
    else:
        run_id = collect(args.config, days=args.days)
        print(f"Coinbase collector wrote ingest_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
