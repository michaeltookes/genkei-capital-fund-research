"""CoinGecko crypto market-data collector (B-034).

Fetches per-coin metadata + full daily price/market-cap/volume history
for every crypto entry in ``config/watchlists.yml::crypto``. Lands two
raw blobs per coin (``coin_<id>``, ``market_chart_<id>``) in
``meta.raw_blobs``. The downstream normalizer
(``genkei.normalize.coingecko``) reads from those blobs.

Single-mode design: CoinGecko's ``/coins/{id}/market_chart`` returns
the entire history in one call when ``days=max``, so daily and backfill
are the same code path. New observations land via the natural PK; older
observations re-upsert idempotently.

CoinGecko rate limits (G-023):
  - Free / keyless: ~5-15 req/min, undocumented and aggressively throttled.
  - Demo key (free, instant): 25-30 req/min, sent via the
    ``x-cg-demo-api-key`` header.
  - Pro key: paid, higher.
We default to per_minute(5) keyless or per_minute(25) with a demo key.
Two calls per coin × 7 watchlist crypto entries = 14 calls per run;
takes ~3 min keyless, ~30s with key. Configurable via
``COINGECKO_API_KEY`` env var.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from genkei.common import db
from genkei.common.http import HttpClient, RateLimit

DEFAULT_WATCHLIST_PATH = Path("config/watchlists.yml")
SOURCE_NAME = "coingecko"
COLLECT_ENDPOINT_LABEL = "collect"
COINGECKO_BASE = "https://api.coingecko.com/api/v3"
COIN_BLOB_PREFIX = "coin_"
MARKET_CHART_BLOB_PREFIX = "market_chart_"
API_KEY_ENV = "COINGECKO_API_KEY"
# Demo-key header name per CoinGecko docs. Pro keys use x-cg-pro-api-key;
# we default to demo since that's the free tier.
DEMO_API_KEY_HEADER = "x-cg-demo-api-key"
# Conservative defaults — keyless gets 5/min, demo gets 25/min, both
# under the documented free-tier limits to leave headroom for retries.
KEYLESS_RATE_LIMIT = RateLimit.per_minute(5)
DEMO_RATE_LIMIT = RateLimit.per_minute(25)
RAW_BLOBS_INSERT = (
    "INSERT INTO meta.raw_blobs (ingest_run_id, endpoint_name, url, payload) "
    "VALUES (%s, %s, %s, %s::jsonb) "
    "ON CONFLICT (ingest_run_id, endpoint_name) DO NOTHING"
)
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CoinTarget:
    """A CoinGecko coin we want to fetch."""

    coingecko_id: str
    symbol: str
    name: str


def load_coins(path: Path) -> list[CoinTarget]:
    """Read ``crypto:`` from watchlists.yml as ``CoinTarget``s.

    Walks both primary and secondary tiers. Skips entries without a
    ``coingecko_id`` field.
    """
    try:
        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise SystemExit(f"Watchlist file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise SystemExit(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("Watchlist root must be a YAML mapping.")
    crypto = data.get("crypto", {})
    if not isinstance(crypto, dict):
        raise SystemExit("watchlists.yml `crypto` must be a mapping (tier -> list).")

    out: list[CoinTarget] = []
    seen_ids: set[str] = set()
    for tier_name, tier_entries in crypto.items():
        if not isinstance(tier_entries, list):
            continue
        for entry in tier_entries:
            if not isinstance(entry, dict):
                continue
            cgid = entry.get("coingecko_id")
            symbol = entry.get("symbol")
            name = entry.get("name")
            if not isinstance(cgid, str) or not cgid:
                LOGGER.warning(
                    "skip crypto %s in tier %s — missing coingecko_id", symbol, tier_name
                )
                continue
            if not isinstance(symbol, str) or not isinstance(name, str):
                LOGGER.warning("skip malformed crypto entry under tier %s", tier_name)
                continue
            if cgid in seen_ids:
                continue
            seen_ids.add(cgid)
            out.append(CoinTarget(coingecko_id=cgid, symbol=symbol, name=name))
    if not out:
        raise SystemExit("No crypto entries with coingecko_id found in the watchlist.")
    return out


def resolve_api_key() -> str | None:
    """Return the demo API key from the environment, or None if unset."""
    return os.environ.get(API_KEY_ENV) or None


def build_coin_url(coingecko_id: str) -> str:
    """Build the URL for the per-coin metadata endpoint.

    Suppresses every optional payload section we don't store — keeps the
    raw_blob row small and the response fast.
    """
    return (
        f"{COINGECKO_BASE}/coins/{coingecko_id}"
        "?localization=false"
        "&tickers=false"
        "&market_data=true"
        "&community_data=false"
        "&developer_data=false"
        "&sparkline=false"
    )


def build_market_chart_url(coingecko_id: str) -> str:
    """Build the URL for the daily-resolution full-history market chart."""
    return (
        f"{COINGECKO_BASE}/coins/{coingecko_id}/market_chart"
        "?vs_currency=usd&days=max&interval=daily"
    )


def _store_blob(ingest_run_id: int, endpoint_name: str, url: str, payload: Any) -> None:
    """Insert one raw_blobs row."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(RAW_BLOBS_INSERT, [ingest_run_id, endpoint_name, url, json.dumps(payload)])


def collect(
    config_path: Path = DEFAULT_WATCHLIST_PATH,
    *,
    http: HttpClient | None = None,
    api_key: str | None = None,
) -> int:
    """Run the CoinGecko collector once and return the meta.ingest_runs id."""
    coins = load_coins(config_path)
    key = api_key if api_key is not None else resolve_api_key()

    owns_http = http is None
    if http is None:
        rate_limit = DEMO_RATE_LIMIT if key else KEYLESS_RATE_LIMIT
        if not key:
            LOGGER.warning(
                "%s not set; falling back to keyless rate limit (~5/min). "
                "Register a free demo key at https://www.coingecko.com/en/api/pricing "
                "for 25/min throughput.",
                API_KEY_ENV,
            )
        http = HttpClient(SOURCE_NAME, rate_limit=rate_limit)

    failures: list[dict[str, str]] = []
    try:
        with db.ingest_run(
            SOURCE_NAME,
            endpoint=COLLECT_ENDPOINT_LABEL,
            metadata={
                "watchlist_path": str(config_path),
                "coin_count": len(coins),
                "authenticated": bool(key),
            },
        ) as run:
            written = 0
            for index, target in enumerate(coins, start=1):
                written += _fetch_coin_pair(target, key, http, run.id, failures)
                if index % 3 == 0:
                    LOGGER.info("CoinGecko collect progress: %s/%s", index, len(coins))
            run.add_rows(written)
            if failures:
                _record_partial(run.id, failures)
                raise RuntimeError(
                    f"CoinGecko fetch failed for {len(failures)} endpoint(s); "
                    "no partial market snapshot will be normalized."
                )
            return run.id
    finally:
        if owns_http:
            http.close()


def _fetch_coin_pair(
    target: CoinTarget,
    api_key: str | None,
    http: HttpClient,
    ingest_run_id: int,
    failures: list[dict[str, str]],
) -> int:
    """Fetch coin metadata + market_chart for one coin. Returns rows written."""
    written = 0
    headers = {DEMO_API_KEY_HEADER: api_key} if api_key else {}
    for prefix, url in (
        (COIN_BLOB_PREFIX, build_coin_url(target.coingecko_id)),
        (MARKET_CHART_BLOB_PREFIX, build_market_chart_url(target.coingecko_id)),
    ):
        endpoint_name = f"{prefix}{target.coingecko_id}"
        try:
            payload = http.get_json(url, headers=headers)
        except Exception as exc:
            LOGGER.warning(
                "CoinGecko fetch failed for %s (%s): %s", endpoint_name, target.symbol, exc
            )
            failures.append({"name": endpoint_name, "url": url, "error": str(exc)})
            continue
        _store_blob(ingest_run_id, endpoint_name, url, payload)
        written += 1
    return written


def _record_partial(ingest_run_id: int, partial: list[dict[str, str]]) -> None:
    """Stash per-coin partial-failure metadata on the ingest_runs row."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE meta.ingest_runs SET metadata = "
            "COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('partial_endpoints', %s::jsonb) "
            "WHERE id = %s",
            [json.dumps(partial), ingest_run_id],
        )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect CoinGecko crypto market-data snapshots into Postgres."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_WATCHLIST_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv or sys.argv[1:])
    run_id = collect(args.config)
    print(f"CoinGecko collector wrote ingest_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
