"""CoinGecko crypto market-data collector (B-034).

Fetches per-coin metadata + daily price/market-cap/volume history for
every crypto entry in ``config/watchlists.yml::crypto``. Lands two raw
blobs per coin (``coin_<id>``, ``market_chart_<id>``) in
``meta.raw_blobs``. The downstream normalizer
(``genkei.normalize.coingecko``) reads from those blobs.

Daily mode uses the Demo/Public rolling 365-day chart window. Backfill
mode requires a paid Pro API key, because CoinGecko restricts
Demo/Public historical chart access to the past 365 days. Pro backfill
uses ``/market_chart/range`` in bounded date chunks and aggregates the
chunks into the existing ``market_chart_<id>`` raw blob shape.

Auth tiers (configurable via ``COINGECKO_API_KEY`` / ``COINGECKO_API_TIER``):

* **Keyless** (no ``COINGECKO_API_KEY``): hits the public host with no
  auth header. CoinGecko applies a conservative ~5-15 req/min ceiling
  here; we cap at ``per_minute(5)`` to stay well below it. Backfill is
  rejected — Pro range endpoint requires a key. 14 calls per daily run
  takes ~3 min keyless vs ~30s with a Demo key.
* **Demo** (``COINGECKO_API_KEY`` set, default tier ``demo``): sent via
  ``x-cg-demo-api-key`` to the Demo host, ``per_minute(25)``.
* **Pro** (``COINGECKO_API_TIER=pro``): sent via ``x-cg-pro-api-key`` to
  the Pro host; only mode that supports backfill.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from genkei.common import db
from genkei.common.http import HttpClient, RateLimit

DEFAULT_WATCHLIST_PATH = Path("config/watchlists.yml")
SOURCE_NAME = "coingecko"
COLLECT_ENDPOINT_LABEL = "collect"
DEMO_COINGECKO_BASE = "https://api.coingecko.com/api/v3"
PRO_COINGECKO_BASE = "https://pro-api.coingecko.com/api/v3"
COIN_BLOB_PREFIX = "coin_"
MARKET_CHART_BLOB_PREFIX = "market_chart_"
API_KEY_ENV = "COINGECKO_API_KEY"
API_TIER_ENV = "COINGECKO_API_TIER"
DEMO_API_TIER = "demo"
PRO_API_TIER = "pro"
# Demo-key header name per CoinGecko docs. Pro keys use x-cg-pro-api-key;
# we default to demo since that's the free tier.
DEMO_API_KEY_HEADER = "x-cg-demo-api-key"
PRO_API_KEY_HEADER = "x-cg-pro-api-key"
# Demo/Public historical chart access is limited to the past 365 days.
DEMO_MARKET_CHART_DAYS = 365
# Keep Pro range payloads bounded and naturally daily-resolution.
BACKFILL_CHUNK_DAYS = 365
# Conservative default under the documented free Demo limit to leave
# headroom for retries.
DEMO_RATE_LIMIT = RateLimit.per_minute(25)
# Public (no-key) requests get a much tighter ceiling. CoinGecko hasn't
# published an exact unauthenticated rate limit; community reports put
# it in the 5-15 req/min range. 5/min is safe and still finishes the
# daily watchlist run in ~3 minutes.
KEYLESS_RATE_LIMIT = RateLimit.per_minute(5)
RAW_BLOBS_INSERT = (
    "INSERT INTO meta.raw_blobs (ingest_run_id, endpoint_name, url, payload) "
    "VALUES (%s, %s, %s, %s::jsonb) "
    "ON CONFLICT (ingest_run_id, endpoint_name) DO NOTHING"
)
LOGGER = logging.getLogger(__name__)
# Sentinel so ``api_key=None`` in collect() can mean "explicit keyless"
# while the default ``api_key=_USE_ENV`` falls back to ``resolve_api_key``.
_USE_ENV: Any = object()


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
    """Return the API key from the environment, or ``None`` for keyless mode."""
    return normalize_api_key(os.environ.get(API_KEY_ENV))


def normalize_api_key(api_key: str | None) -> str | None:
    """Trim a configured CoinGecko API key. Returns ``None`` if unset/empty.

    Keyless is a supported mode (public CoinGecko host, no auth header,
    tighter rate limit). Whitespace-only values are treated the same as
    unset so an empty GH Actions secret doesn't surprise us with a 403.
    """
    api_key = api_key.strip() if api_key is not None else ""
    return api_key or None


def resolve_api_tier() -> str:
    """Return the configured CoinGecko API tier."""
    return normalize_api_tier(os.environ.get(API_TIER_ENV, DEMO_API_TIER))


def normalize_api_tier(api_tier: str) -> str:
    """Validate and normalize the configured CoinGecko API tier."""
    tier = api_tier.strip().lower()
    if tier not in {DEMO_API_TIER, PRO_API_TIER}:
        raise SystemExit(f"{API_TIER_ENV} must be either 'demo' or 'pro'.")
    return tier


def validate_api_key_tier(api_tier: str, *, backfill: bool, api_key: str | None) -> None:
    """Fail fast when the configured tier/key combo cannot support the request."""
    if api_tier == PRO_API_TIER and api_key is None:
        raise SystemExit(
            f"COINGECKO_API_TIER=pro requires {API_KEY_ENV} to be set."
        )
    if backfill and (api_tier != PRO_API_TIER or api_key is None):
        raise SystemExit(
            "CoinGecko historical backfill requires COINGECKO_API_TIER=pro and a Pro API key. "
            "Demo/Public/keyless API access is limited to the past 365 days."
        )


def api_base_url(api_tier: str) -> str:
    """Return the API host for the configured tier.

    Keyless and Demo share the same public host; only Pro uses the
    separate pro-api host.
    """
    return PRO_COINGECKO_BASE if api_tier == PRO_API_TIER else DEMO_COINGECKO_BASE


def api_key_headers(api_tier: str, api_key: str | None) -> dict[str, str]:
    """Return the auth header for the configured tier, or ``{}`` if keyless."""
    if api_key is None:
        return {}
    header = PRO_API_KEY_HEADER if api_tier == PRO_API_TIER else DEMO_API_KEY_HEADER
    return {header: api_key}


def build_coin_url(coingecko_id: str, *, base_url: str = DEMO_COINGECKO_BASE) -> str:
    """Build the URL for the per-coin metadata endpoint.

    Suppresses every optional payload section we don't store — keeps the
    raw_blob row small and the response fast.
    """
    return (
        f"{base_url}/coins/{coingecko_id}"
        "?localization=false"
        "&tickers=false"
        "&market_data=true"
        "&community_data=false"
        "&developer_data=false"
        "&sparkline=false"
    )


def build_market_chart_url(
    coingecko_id: str, *, base_url: str = DEMO_COINGECKO_BASE
) -> str:
    """Build the URL for the daily-resolution Demo market chart."""
    return (
        f"{base_url}/coins/{coingecko_id}/market_chart"
        f"?vs_currency=usd&days={DEMO_MARKET_CHART_DAYS}&interval=daily"
    )


def build_market_chart_range_url(
    coingecko_id: str,
    *,
    since: date,
    until: date,
    base_url: str = PRO_COINGECKO_BASE,
) -> str:
    """Build the URL for a Pro historical chart range request."""
    return (
        f"{base_url}/coins/{coingecko_id}/market_chart/range"
        f"?vs_currency=usd&from={since.isoformat()}&to={until.isoformat()}&interval=daily"
    )


def _store_blob(ingest_run_id: int, endpoint_name: str, url: str, payload: Any) -> None:
    """Insert one raw_blobs row."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(RAW_BLOBS_INSERT, [ingest_run_id, endpoint_name, url, json.dumps(payload)])


def collect(
    config_path: Path = DEFAULT_WATCHLIST_PATH,
    *,
    http: HttpClient | None = None,
    api_key: str | None = _USE_ENV,
    api_tier: str | None = None,
    backfill: bool = False,
    since: date | None = None,
) -> int:
    """Run the CoinGecko collector once and return the meta.ingest_runs id.

    ``api_key`` semantics:
    - default (``_USE_ENV``) — look up ``COINGECKO_API_KEY`` from env;
      missing/blank means keyless.
    - explicit ``None`` — force keyless mode.
    - explicit string — use that key (trimmed; blank becomes keyless).
    """
    key = resolve_api_key() if api_key is _USE_ENV else normalize_api_key(api_key)
    tier = normalize_api_tier(api_tier) if api_tier is not None else resolve_api_tier()
    validate_api_key_tier(tier, backfill=backfill, api_key=key)
    if backfill and since is None:
        raise SystemExit("--since YYYY-MM-DD is required with --backfill.")
    until = date.today()
    if since is not None and since > until:
        raise SystemExit("--since cannot be in the future.")
    coins = load_coins(config_path)

    owns_http = http is None
    if http is None:
        rate_limit = DEMO_RATE_LIMIT if key is not None else KEYLESS_RATE_LIMIT
        http = HttpClient(SOURCE_NAME, rate_limit=rate_limit)
    if key is None:
        LOGGER.warning(
            "CoinGecko collector running keyless — rate limited to ~5 req/min, "
            "Pro/backfill endpoints unavailable. Set %s to use a Demo or Pro key.",
            API_KEY_ENV,
        )

    failures: list[dict[str, str]] = []
    try:
        with db.ingest_run(
            SOURCE_NAME,
            endpoint=COLLECT_ENDPOINT_LABEL,
            metadata={
                "watchlist_path": str(config_path),
                "coin_count": len(coins),
                "authenticated": bool(key),
                "api_tier": tier,
                "mode": "backfill" if backfill else "daily",
                **({"since": since.isoformat(), "until": until.isoformat()} if backfill else {}),
            },
        ) as run:
            written = 0
            for index, target in enumerate(coins, start=1):
                written += _fetch_coin_pair(
                    target,
                    key,
                    http,
                    run.id,
                    failures,
                    api_tier=tier,
                    backfill=backfill,
                    since=since,
                    until=until,
                )
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
    *,
    api_tier: str,
    backfill: bool,
    since: date | None,
    until: date,
) -> int:
    """Fetch coin metadata + market_chart for one coin. Returns rows written."""
    written = 0
    headers = api_key_headers(api_tier, api_key)
    base_url = api_base_url(api_tier)

    coin_url = build_coin_url(target.coingecko_id, base_url=base_url)
    if _fetch_and_store(
        target=target,
        http=http,
        headers=headers,
        ingest_run_id=ingest_run_id,
        endpoint_name=f"{COIN_BLOB_PREFIX}{target.coingecko_id}",
        url=coin_url,
        failures=failures,
    ):
        written += 1

    endpoint_name = f"{MARKET_CHART_BLOB_PREFIX}{target.coingecko_id}"
    if backfill:
        assert since is not None
        url = build_market_chart_range_url(target.coingecko_id, since=since, until=until)
        try:
            payload = fetch_historical_market_chart(
                target,
                http,
                headers=headers,
                since=since,
                until=until,
                base_url=base_url,
            )
        except Exception as exc:
            LOGGER.warning(
                "CoinGecko backfill failed for %s (%s): %s",
                endpoint_name,
                target.symbol,
                exc,
            )
            failures.append({"name": endpoint_name, "url": url, "error": str(exc)})
        else:
            _store_blob(ingest_run_id, endpoint_name, url, payload)
            written += 1
    else:
        url = build_market_chart_url(target.coingecko_id, base_url=base_url)
        if _fetch_and_store(
            target=target,
            http=http,
            headers=headers,
            ingest_run_id=ingest_run_id,
            endpoint_name=endpoint_name,
            url=url,
            failures=failures,
        ):
            written += 1
    return written


def _fetch_and_store(
    *,
    target: CoinTarget,
    http: HttpClient,
    headers: dict[str, str],
    ingest_run_id: int,
    endpoint_name: str,
    url: str,
    failures: list[dict[str, str]],
) -> bool:
    """Fetch one JSON URL and store it as a raw blob."""
    try:
        payload = http.get_json(url, headers=headers)
    except Exception as exc:
        LOGGER.warning("CoinGecko fetch failed for %s (%s): %s", endpoint_name, target.symbol, exc)
        failures.append({"name": endpoint_name, "url": url, "error": str(exc)})
        return False
    _store_blob(ingest_run_id, endpoint_name, url, payload)
    return True


def fetch_historical_market_chart(
    target: CoinTarget,
    http: HttpClient,
    *,
    headers: dict[str, str],
    since: date,
    until: date,
    base_url: str = PRO_COINGECKO_BASE,
    chunk_days: int = BACKFILL_CHUNK_DAYS,
) -> dict[str, Any]:
    """Fetch and merge Pro historical chart ranges for one coin."""
    chunks: list[dict[str, Any]] = []
    for start, end in iter_date_ranges(since, until, chunk_days=chunk_days):
        url = build_market_chart_range_url(
            target.coingecko_id, since=start, until=end, base_url=base_url
        )
        chunks.append(http.get_json(url, headers=headers))
    return merge_market_chart_payloads(chunks)


def iter_date_ranges(since: date, until: date, *, chunk_days: int) -> list[tuple[date, date]]:
    """Return inclusive date windows for range-based historical fetches."""
    if chunk_days < 1:
        raise ValueError("chunk_days must be >= 1.")
    ranges: list[tuple[date, date]] = []
    start = since
    while start <= until:
        end = min(start + timedelta(days=chunk_days - 1), until)
        ranges.append((start, end))
        start = end + timedelta(days=1)
    return ranges


def merge_market_chart_payloads(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge range chunks into the normal ``/market_chart`` payload shape."""
    return {
        "prices": _merge_series(chunks, "prices"),
        "market_caps": _merge_series(chunks, "market_caps"),
        "total_volumes": _merge_series(chunks, "total_volumes"),
    }


def _merge_series(chunks: list[dict[str, Any]], key: str) -> list[list[Any]]:
    by_ts: dict[int, Any] = {}
    for chunk in chunks:
        values = chunk.get(key, [])
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, list) or len(item) < 2:
                continue
            ts = item[0]
            if isinstance(ts, int):
                by_ts[ts] = item[1]
    return [[ts, by_ts[ts]] for ts in sorted(by_ts)]


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
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Fetch historical chart data with the Pro range endpoint.",
    )
    parser.add_argument(
        "--since",
        type=_parse_date,
        default=None,
        help="Start date for --backfill in YYYY-MM-DD format.",
    )
    args = parser.parse_args(argv)
    if args.since is not None and not args.backfill:
        parser.error("--since requires --backfill")
    if args.backfill and args.since is None:
        parser.error("--backfill requires --since YYYY-MM-DD")
    return args


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv if argv is not None else sys.argv[1:])
    run_id = collect(args.config, backfill=args.backfill, since=args.since)
    print(f"CoinGecko collector wrote ingest_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
