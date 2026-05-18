"""DeFiLlama collector — fetches public payloads and lands them in Postgres.

Replaces the file-based ``scripts/collect_defillama.py`` (B-017 + B-013).
Each run records a row in ``meta.ingest_runs`` and inserts one
``meta.raw_blobs`` row per endpoint. The downstream normalizer
(``genkei.normalize.defillama``) reads from ``meta.raw_blobs`` rather
than re-hitting the upstream API — replay is a single SQL query.

Endpoints collected (driven by ``config/defillama.sources.json``):

  - ``prices_current``               coins.llama.fi /prices/current
  - ``protocols``                    api.llama.fi /protocols
  - ``chains``                       api.llama.fi /v2/chains (optional; not normalized)
  - ``stablecoins``                  stablecoins.llama.fi /stablecoins
  - ``chain_tvl_history_<chain>``    one per ``chain_focus`` entry

Required normalized endpoints raise on failure and the run is marked
``failed``. Optional endpoints such as ``chains`` and per-chain history are
logged and recorded in run metadata under ``partial_endpoints`` so the run
still completes.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from genkei.common import db
from genkei.common.http import HttpClient, RateLimit

DEFAULT_CONFIG_PATH = Path("config/defillama.sources.json")
SOURCE_NAME = "defillama"
COLLECT_ENDPOINT_LABEL = "collect"
BACKFILL_ENDPOINT_LABEL = "backfill"
# Backfill-blob naming. Uniqueness is per (ingest_run_id, endpoint_name);
# these prefixes also drive normalizer dispatch.
PRICE_HISTORICAL_PREFIX = "prices_historical_"
PROTOCOL_HISTORY_PREFIX = "protocol_"
PROTOCOL_FEES_PREFIX = "protocol_fees_"
PROTOCOL_REVENUE_PREFIX = "protocol_revenue_"
STABLECOIN_HISTORY_PREFIX = "stablecoin_"
# Resumability: skip URLs we've fetched within this window. Long enough
# that crashed-then-resumed runs stay efficient; short enough that data
# eventually refreshes if the user re-runs the same backfill weeks later.
RESUME_WINDOW = timedelta(days=14)
JsonObject = dict[str, Any]
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CollectionTarget:
    """A DeFiLlama endpoint to fetch."""

    name: str
    url: str
    required: bool = True


def load_config(path: Path) -> JsonObject:
    """Load the collector configuration from disk."""
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise SystemExit(f"Config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Config file is not valid JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("Config root must be a JSON object.")
    return data


def read_base_urls(config: JsonObject) -> dict[str, str]:
    """Return configured DeFiLlama base URLs with trailing slashes stripped."""
    base_urls = config.get("defillama_base_urls")
    if not isinstance(base_urls, dict):
        raise SystemExit("defillama_base_urls must be an object.")
    return {str(key): str(value).rstrip("/") for key, value in base_urls.items()}


def require_string(source: JsonObject, key: str) -> str:
    """Return a required string field from a JSON object."""
    value = source.get(key)
    if not isinstance(value, str) or not value:
        raise SystemExit(f"Missing required string field: {key}")
    return value


def chain_history_target_name(chain_name: str) -> str:
    """Normalizer-friendly name stem for a chain TVL history payload."""
    safe = "".join(ch if ch.isalnum() else "_" for ch in chain_name.lower())
    return f"chain_tvl_history_{safe}"


def build_price_target(config: JsonObject) -> CollectionTarget:
    """Build the bulk price URL for the configured target assets."""
    coin_keys = target_asset_coin_keys(config)
    base_urls = read_base_urls(config)
    if "coins" not in base_urls:
        raise SystemExit("defillama_base_urls.coins is missing")
    joined = quote(",".join(coin_keys), safe=":,")
    return CollectionTarget("prices_current", f"{base_urls['coins']}/prices/current/{joined}")


def target_asset_coin_keys(config: JsonObject) -> list[str]:
    """Return configured target assets as DeFiLlama coin keys."""
    assets = config.get("target_assets", [])
    if not isinstance(assets, list) or not assets:
        raise SystemExit("Config must define at least one target asset.")
    coin_keys = []
    for asset in assets:
        if not isinstance(asset, dict) or not asset.get("coingecko_id"):
            raise SystemExit("Each target asset must include coingecko_id.")
        coin_keys.append(f"coingecko:{asset['coingecko_id']}")
    return coin_keys


def build_chain_history_targets(config: JsonObject) -> list[CollectionTarget]:
    """Build per-chain TVL history URLs for the focus chain set."""
    base_urls = read_base_urls(config)
    if "core" not in base_urls:
        raise SystemExit("defillama_base_urls.core is missing")
    chain_focus = config.get("chain_focus", [])
    if not isinstance(chain_focus, list):
        raise SystemExit("chain_focus must be a list.")
    targets = []
    for chain_name in chain_focus:
        if not isinstance(chain_name, str) or not chain_name:
            raise SystemExit("chain_focus must contain only non-empty strings.")
        encoded = quote(chain_name, safe="")
        targets.append(
            CollectionTarget(
                chain_history_target_name(chain_name),
                f"{base_urls['core']}/v2/historicalChainTvl/{encoded}",
                required=False,
            )
        )
    return targets


def build_collection_targets(config: JsonObject) -> list[CollectionTarget]:
    """Build the full ordered set of endpoints the collector hits."""
    base_urls = read_base_urls(config)
    endpoints = config.get("collection_endpoints", [])
    if not isinstance(endpoints, list):
        raise SystemExit("collection_endpoints must be a list.")

    config_endpoint_names: set[str] = set()
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            raise SystemExit("Each collection endpoint must be an object.")
        config_endpoint_names.add(require_string(endpoint, "name"))
    missing = {"protocols", "stablecoins"} - config_endpoint_names
    if missing:
        raise SystemExit(f"Missing required collection endpoints: {', '.join(sorted(missing))}")

    targets: list[CollectionTarget] = [build_price_target(config)]
    targets.extend(build_chain_history_targets(config))
    seen = {target.name for target in targets}
    for endpoint in endpoints:
        name = require_string(endpoint, "name")
        if name in seen:
            raise SystemExit(f"Duplicate collection endpoint name: {name}")
        base_key = require_string(endpoint, "base")
        path = require_string(endpoint, "path")
        if base_key not in base_urls:
            raise SystemExit(f"Unknown base URL key for endpoint {name}: {base_key}")
        seen.add(name)
        targets.append(
            CollectionTarget(name, f"{base_urls[base_key]}{path}", required=name != "chains")
        )
    return targets


def collect(
    config_path: Path,
    *,
    http: HttpClient | None = None,
    watchlist_path: Path | None = None,
) -> int:
    """Run the collector once and return the ``meta.ingest_runs`` row id.

    ``http`` is injectable for testing; the production path constructs a
    rate-limited :class:`HttpClient`.

    Per B-081, the daily collect also fetches ``/protocol/{slug}`` for
    every protocol in the watchlist's ``protocols:`` section (~7 calls
    today) so ``defillama.protocol_tvl`` stays current without needing
    the full ~3k-protocol backfill on every run. ``watchlist_path``
    defaults to the package's bundled watchlist; tests pass an
    explicit path.
    """
    config = load_config(config_path)
    targets = build_collection_targets(config)
    base_urls = read_base_urls(config)
    protocol_slugs = _load_watchlist_protocol_slugs(watchlist_path)

    owns_http = http is None
    if http is None:
        http = HttpClient(SOURCE_NAME, rate_limit=RateLimit.per_second(5))

    partial: list[dict[str, str]] = []
    try:
        with db.ingest_run(
            SOURCE_NAME,
            endpoint=COLLECT_ENDPOINT_LABEL,
            metadata={
                "config_path": str(config_path),
                "watchlist_protocol_count": len(protocol_slugs),
            },
        ) as run:
            for target in targets:
                try:
                    payload = http.get_json(target.url)
                except Exception as exc:
                    if target.required:
                        raise
                    LOGGER.warning("partial data for %s: %s", target.name, exc)
                    partial.append({"name": target.name, "url": target.url, "error": str(exc)})
                    continue
                db.store_raw_blob(run.id, target.name, target.url, payload)
                run.add_rows(1)

            # B-081 — watchlist-driven per-protocol /protocol/{slug} pull.
            # Soft-failure per slug: a single 404 (e.g. a renamed slug)
            # doesn't fail the daily run; it's logged + recorded.
            core_base = base_urls.get("core")
            if core_base and protocol_slugs:
                for slug in protocol_slugs:
                    url = f"{core_base}/protocol/{quote(slug, safe='')}"
                    endpoint_name = f"{PROTOCOL_HISTORY_PREFIX}{slug}"
                    try:
                        payload = http.get_json(url)
                    except Exception as exc:
                        LOGGER.warning(
                            "watchlist protocol fetch failed for %s: %s", slug, exc
                        )
                        partial.append(
                            {"name": endpoint_name, "url": url, "error": str(exc)}
                        )
                        continue
                    db.store_raw_blob(run.id, endpoint_name, url, payload)
                    run.add_rows(1)

                # B-083 — watchlist-driven per-protocol fees + revenue.
                # Both kinds live behind the same `/summary/fees/{slug}`
                # endpoint with a `?dataType=` query-param differentiator
                # — the bare `/summary/revenue/{slug}` path returns 500
                # across the board (DefiLlama deprecated it). The
                # fees-side default and `?dataType=dailyRevenue` both
                # return 200 with the standard `totalDataChart` shape.
                # Soft-fail per (slug, kind) so each is independent.
                for slug in protocol_slugs:
                    for kind, prefix, datatype in (
                        ("fees", PROTOCOL_FEES_PREFIX, "dailyFees"),
                        ("revenue", PROTOCOL_REVENUE_PREFIX, "dailyRevenue"),
                    ):
                        url = (
                            f"{core_base}/summary/fees/{quote(slug, safe='')}"
                            f"?dataType={datatype}"
                        )
                        endpoint_name = f"{prefix}{slug}"
                        try:
                            payload = http.get_json(url)
                        except Exception as exc:
                            LOGGER.info(
                                "watchlist %s fetch unavailable for %s (expected for "
                                "protocols where %s isn't tracked): %s",
                                kind,
                                slug,
                                kind,
                                exc,
                            )
                            partial.append(
                                {"name": endpoint_name, "url": url, "error": str(exc)}
                            )
                            continue
                        db.store_raw_blob(run.id, endpoint_name, url, payload)
                        run.add_rows(1)

            if partial:
                _record_partial(run.id, partial)
            return run.id
    finally:
        if owns_http:
            http.close()


_TIER_PRIORITY = ("primary", "secondary")


def _load_watchlist_protocol_slugs(watchlist_path: Path | None) -> list[str]:
    """Return DefiLlama protocol slugs from the watchlist, primary tier first.

    Reads via the shared ``genkei.common.watchlist`` loader; on any I/O or
    parse error the warning is logged and an empty list is returned so the
    daily run still lands the fixed-name endpoints.
    """
    from genkei.common.watchlist import DEFAULT_WATCHLIST_PATH, load_watchlist

    path = watchlist_path if watchlist_path is not None else DEFAULT_WATCHLIST_PATH
    try:
        watchlist = load_watchlist(path)
    except (FileNotFoundError, ValueError, OSError) as exc:
        LOGGER.warning(
            "watchlist could not be read at %s — skipping per-protocol fetch: %s",
            path,
            exc,
        )
        return []

    def _tier_rank(tier: str) -> int:
        try:
            return _TIER_PRIORITY.index(tier)
        except ValueError:
            return len(_TIER_PRIORITY)

    seen: set[str] = set()
    slugs: list[str] = []
    for entry in sorted(watchlist.protocols, key=lambda e: _tier_rank(e.tier)):
        if entry.slug in seen:
            continue
        seen.add(entry.slug)
        slugs.append(entry.slug)
    return slugs


def _record_partial(ingest_run_id: int, partial: list[dict[str, str]]) -> None:
    """Stash per-endpoint partial-failure metadata on the ingest_runs row."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE meta.ingest_runs SET metadata = "
            "COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('partial_endpoints', %s::jsonb) "
            "WHERE id = %s",
            [json.dumps(partial), ingest_run_id],
        )


# ---------------------------------------------------------------------------
# Backfill mode (B-019)
# ---------------------------------------------------------------------------
#
# DeFiLlama exposes per-endpoint historical depth that varies by source:
#   prices       — `/coins/prices/historical/{ts}/{keys}` per timestamp.
#                  Major assets (BTC/ETH) go back ~5y; newer assets less.
#                  We walk daily timestamps from --since to today.
#   protocols    — `/protocol/{slug}` returns full per-chain TVL series.
#                  Most major protocols cover 3-5y.
#   stablecoins  — `/stablecoin/{id}` returns per-chain peggedUSD series.
#                  Major stablecoins (USDT/USDC) cover ~3-5y.
#   chain_tvl    — already covered by the daily collector pulling
#                  `/v2/historicalChainTvl/{chain}` in full on every run
#                  (G-012). Backfill is a no-op here.
#
# Resumability: every prospective fetch checks meta.raw_blobs for a
# matching URL within RESUME_WINDOW (14d). Already-fetched URLs are
# skipped, so a crashed backfill resumes from where it left off without
# refetching what's already on disk.
#
# All three backfillable endpoints are best-effort per item: a single
# failed protocol/stablecoin/date doesn't tank the whole run; it's
# logged and recorded in meta.ingest_runs.metadata.partial_endpoints.

DEFAULT_BACKFILL_RATE_LIMIT = RateLimit.per_second(5)
BACKFILLABLE_ENDPOINTS = ("prices", "protocols", "stablecoins")


def parse_since_date(value: str) -> date:
    """Parse a YYYY-MM-DD argument."""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--since must be YYYY-MM-DD: {value}") from exc


def _cached_blob(url: str) -> tuple[Any, datetime] | None:
    """Return the newest recent blob for ``url`` if it is inside the resume window."""
    cutoff = datetime.now(timezone.utc) - RESUME_WINDOW
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT payload, fetched_at FROM meta.raw_blobs "
            "WHERE url = %s AND fetched_at >= %s "
            "ORDER BY fetched_at DESC LIMIT 1",
            [url, cutoff],
        )
        row = cur.fetchone()
    if row is None:
        return None
    payload, fetched_at = row
    return payload, fetched_at


def _fetch_with_resume(
    url: str,
    endpoint_name: str,
    ingest_run_id: int,
    http: HttpClient,
    failures: list[dict[str, str]],
) -> bool:
    """Fetch one URL into raw_blobs; skip if recently cached. Return True on row written."""
    cached = _cached_blob(url)
    if cached is not None:
        payload, fetched_at = cached
        db.copy_raw_blob_for_run(ingest_run_id, endpoint_name, url, payload, fetched_at)
        LOGGER.debug("reuse %s (already fetched within %s)", endpoint_name, RESUME_WINDOW)
        return True
    try:
        payload = http.get_json(url)
    except Exception as exc:
        LOGGER.warning("backfill fetch failed for %s: %s", endpoint_name, exc)
        failures.append({"name": endpoint_name, "url": url, "error": str(exc)})
        return False
    db.store_raw_blob(ingest_run_id, endpoint_name, url, payload)
    return True


def _backfill_prices(
    config: JsonObject,
    since: date,
    http: HttpClient,
    ingest_run_id: int,
    failures: list[dict[str, str]],
) -> int:
    """Walk daily timestamps and fetch /coins/prices/historical/{ts}/{keys}."""
    base_urls = read_base_urls(config)
    if "coins" not in base_urls:
        raise SystemExit("defillama_base_urls.coins is missing")
    coin_keys = quote(",".join(target_asset_coin_keys(config)), safe=":,")
    written = 0
    today = date.today()
    cursor = since
    while cursor <= today:
        ts = int(datetime.combine(cursor, datetime.min.time(), tzinfo=timezone.utc).timestamp())
        url = f"{base_urls['coins']}/prices/historical/{ts}/{coin_keys}"
        endpoint_name = f"{PRICE_HISTORICAL_PREFIX}{cursor.isoformat()}"
        if _fetch_with_resume(url, endpoint_name, ingest_run_id, http, failures):
            written += 1
        cursor += timedelta(days=1)
    return written


def _backfill_protocols(
    config: JsonObject,
    http: HttpClient,
    ingest_run_id: int,
    failures: list[dict[str, str]],
) -> int:
    """Iterate every known protocol slug, fetch /protocol/{slug}."""
    base_urls = read_base_urls(config)
    if "core" not in base_urls:
        raise SystemExit("defillama_base_urls.core is missing")
    slugs = _known_protocol_slugs()
    if not slugs:
        LOGGER.warning("no protocols in defillama.protocols — run the daily collector first")
        return 0
    written = 0
    for index, slug in enumerate(slugs):
        url = f"{base_urls['core']}/protocol/{quote(slug, safe='')}"
        endpoint_name = f"{PROTOCOL_HISTORY_PREFIX}{slug}"
        if _fetch_with_resume(url, endpoint_name, ingest_run_id, http, failures):
            written += 1
        if (index + 1) % 100 == 0:
            LOGGER.info("protocols backfill progress: %s/%s", index + 1, len(slugs))
    return written


def _backfill_stablecoins(
    config: JsonObject,
    http: HttpClient,
    ingest_run_id: int,
    failures: list[dict[str, str]],
) -> int:
    """Iterate every known stablecoin id, fetch /stablecoin/{id}."""
    base_urls = read_base_urls(config)
    if "stablecoins" not in base_urls:
        raise SystemExit("defillama_base_urls.stablecoins is missing")
    asset_ids = _known_stablecoin_ids()
    if not asset_ids:
        LOGGER.warning("no stablecoins in defillama.stablecoins — run the daily collector first")
        return 0
    written = 0
    for index, asset_id in enumerate(asset_ids):
        url = f"{base_urls['stablecoins']}/stablecoin/{quote(asset_id, safe='')}"
        endpoint_name = f"{STABLECOIN_HISTORY_PREFIX}{asset_id}"
        if _fetch_with_resume(url, endpoint_name, ingest_run_id, http, failures):
            written += 1
        if (index + 1) % 50 == 0:
            LOGGER.info("stablecoins backfill progress: %s/%s", index + 1, len(asset_ids))
    return written


def _known_protocol_slugs() -> list[str]:
    """Slugs we've seen at least once in defillama.protocols."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT slug FROM defillama.protocols ORDER BY slug")
        return [r[0] for r in cur.fetchall()]


def _known_stablecoin_ids() -> list[str]:
    """Distinct stablecoin asset_ids seen in defillama.stablecoins."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT asset_id FROM defillama.stablecoins ORDER BY asset_id")
        return [r[0] for r in cur.fetchall()]


def backfill(
    config_path: Path,
    *,
    since: date,
    endpoints: list[str] | None = None,
    http: HttpClient | None = None,
) -> int:
    """Run the historical backfill once and return the meta.ingest_runs id.

    `endpoints` filters to a subset of {'prices', 'protocols', 'stablecoins'}.
    None = all three. chain_tvl is intentionally not backfillable here —
    the daily collector already pulls full chain history every run.

    Returns the backfill run's ingest_run id. Resumability via
    meta.raw_blobs.url lookup means re-running after a partial failure
    skips already-fetched URLs (within RESUME_WINDOW).
    """
    config = load_config(config_path)
    selected = list(endpoints) if endpoints else list(BACKFILLABLE_ENDPOINTS)
    invalid = [e for e in selected if e not in BACKFILLABLE_ENDPOINTS]
    if invalid:
        raise SystemExit(f"unknown backfill endpoints: {invalid}")

    owns_http = http is None
    if http is None:
        http = HttpClient(SOURCE_NAME, rate_limit=DEFAULT_BACKFILL_RATE_LIMIT)

    failures: list[dict[str, str]] = []
    try:
        with db.ingest_run(
            SOURCE_NAME,
            endpoint=BACKFILL_ENDPOINT_LABEL,
            metadata={
                "config_path": str(config_path),
                "since": since.isoformat(),
                "endpoints": selected,
            },
        ) as run:
            total = 0
            if "prices" in selected:
                total += _backfill_prices(config, since, http, run.id, failures)
            if "protocols" in selected:
                total += _backfill_protocols(config, http, run.id, failures)
            if "stablecoins" in selected:
                total += _backfill_stablecoins(config, http, run.id, failures)
            run.add_rows(total)
            if failures:
                _record_partial(run.id, failures)
            return run.id
    finally:
        if owns_http:
            http.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect DeFiLlama public API snapshots into Postgres."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Run historical backfill instead of the daily snapshot.",
    )
    parser.add_argument(
        "--since",
        type=parse_since_date,
        default=None,
        help="Backfill start date (YYYY-MM-DD). Required with --backfill.",
    )
    parser.add_argument(
        "--endpoint",
        action="append",
        choices=BACKFILLABLE_ENDPOINTS,
        help=(
            "Backfill a subset of endpoints. Repeatable; default = all "
            f"({', '.join(BACKFILLABLE_ENDPOINTS)})."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv or sys.argv[1:])
    if not args.backfill and (args.since is not None or args.endpoint):
        raise SystemExit("--since/--endpoint only valid with --backfill")
    if args.backfill:
        if args.since is None:
            raise SystemExit("--since YYYY-MM-DD is required with --backfill")
        run_id = backfill(args.config, since=args.since, endpoints=args.endpoint)
        print(f"DeFiLlama backfill wrote ingest_run_id={run_id}")
    else:
        run_id = collect(args.config)
        print(f"DeFiLlama collector wrote ingest_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
