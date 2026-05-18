"""DeFiLlama normalizer — reads meta.raw_blobs, upserts defillama.*.

Replaces the file-based ``scripts/normalize_defillama.py`` (B-018 + B-013).
A normalizer run is itself a row in ``meta.ingest_runs`` with
``endpoint='normalize'`` and ``metadata.source_run_id`` pointing at the
collector run whose blobs were processed. Re-running the normalizer
against the same source run is idempotent: every write is an
``ON CONFLICT DO UPDATE`` keyed on the table's natural PK.

Scope decisions (worth flagging — diverge from the legacy report-shaped
normalizer):

  - The normalizer is data-lake-shaped, not report-shaped. It writes the
    raw shape of every endpoint. Derived classifications (momentum,
    trend, zombie risk) lived in the legacy script because they were
    consumed by ``build_daily_report.py``; they belong in the report
    layer when B-025 lands, not here.
  - ``defillama.protocols`` and ``defillama.stablecoins`` are stored
    fully (no chain filter). ``defillama.chain_tvl`` is filtered to the
    configured ``chain_focus`` set — chain history blobs only exist for
    focus chains, and writing a row per all-50+ DeFiLlama chains every
    day would blow up the hypertable for no current consumer.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from genkei.common import db

DEFAULT_CONFIG_PATH = Path("config/defillama.sources.json")
SOURCE_NAME = "defillama"
NORMALIZE_ENDPOINT_LABEL = "normalize"
NORMALIZE_BACKFILL_ENDPOINT_LABEL = "normalize_backfill"
COLLECT_ENDPOINT_LABEL = "collect"
BACKFILL_ENDPOINT_LABEL = "backfill"
CHAIN_HISTORY_PREFIX = "chain_tvl_history_"
# Backfill blob name prefixes (mirror genkei.ingest.defillama).
PRICE_HISTORICAL_PREFIX = "prices_historical_"
PROTOCOL_HISTORY_PREFIX = "protocol_"
STABLECOIN_HISTORY_PREFIX = "stablecoin_"
JsonObject = dict[str, Any]
RawBlob = tuple[str, Any, datetime]
LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def as_float(value: Any) -> float | None:
    """Coerce numeric API values to ``float`` while preserving missingness."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_history_timestamp(value: Any) -> datetime | None:
    """Parse DeFiLlama history timestamps (epoch seconds or ISO) as UTC."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def chain_history_stem(chain_name: str) -> str:
    """The blob-name suffix the collector uses for a chain TVL history."""
    return "".join(ch if ch.isalnum() else "_" for ch in chain_name.lower())


# ---------------------------------------------------------------------------
# Per-table normalizers (raw payload -> row dicts)
# ---------------------------------------------------------------------------


def normalize_protocols(
    payload: Any,
    *,
    source_endpoint: str,
    ingest_run_id: int,
    now: datetime,
) -> list[JsonObject]:
    """Map the ``/protocols`` payload to ``defillama.protocols`` row dicts."""
    if not isinstance(payload, list):
        return []
    rows: list[JsonObject] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        name = item.get("name")
        if not slug or not name:
            continue
        raw_chains = item.get("chains")
        chains = [str(c) for c in raw_chains] if isinstance(raw_chains, list) else None
        rows.append(
            {
                "slug": str(slug),
                "defillama_id": _stringify(item.get("id")),
                "name": str(name),
                "category": _stringify(item.get("category")),
                "chains": chains,
                "url": _stringify(item.get("url")),
                "description": _stringify(item.get("description")),
                "parent_protocol": _stringify(item.get("parentProtocol")),
                "twitter": _stringify(item.get("twitter")),
                "last_updated_at": now,
                "source_endpoint": source_endpoint,
                "fetched_at": now,
                "ingest_run_id": ingest_run_id,
            }
        )
    return rows


def normalize_chain_tvl_history(
    payload: Any,
    *,
    chain_name: str,
    source_endpoint: str,
    ingest_run_id: int,
    now: datetime,
) -> list[JsonObject]:
    """Map a single ``chain_tvl_history_<chain>`` payload to row dicts."""
    if not isinstance(payload, list):
        return []
    rows: list[JsonObject] = []
    seen_ts: set[datetime] = set()
    for item in payload:
        if not isinstance(item, dict):
            continue
        ts = parse_history_timestamp(item.get("date"))
        tvl = as_float(item.get("tvl"))
        if ts is None or tvl is None or ts in seen_ts:
            continue
        seen_ts.add(ts)
        rows.append(
            {
                "chain": chain_name,
                "ts": ts,
                "tvl_usd": tvl,
                "source_endpoint": source_endpoint,
                "fetched_at": now,
                "ingest_run_id": ingest_run_id,
            }
        )
    return rows


def normalize_stablecoins(
    payload: Any,
    *,
    source_endpoint: str,
    ingest_run_id: int,
    now: datetime,
) -> list[JsonObject]:
    """Map the ``/stablecoins`` payload to ``defillama.stablecoins`` rows."""
    if not isinstance(payload, dict):
        return []
    pegged_assets = payload.get("peggedAssets")
    if not isinstance(pegged_assets, list):
        return []
    rows: list[JsonObject] = []
    for asset in pegged_assets:
        if not isinstance(asset, dict):
            continue
        asset_id = _stringify(asset.get("id"))
        symbol = _stringify(asset.get("symbol"))
        if asset_id is None or symbol is None:
            continue
        # DeFiLlama's /stablecoins payload exposes per-chain supply under
        # `chainCirculating`; an older shape used `chainBalances` and the
        # legacy normalizer carried that over by mistake. Accept either,
        # newer field wins.
        if "chainCirculating" in asset:
            chain_supply = asset["chainCirculating"]
        elif "chainBalances" in asset:
            chain_supply = asset["chainBalances"]
        else:
            continue
        if not isinstance(chain_supply, dict):
            continue
        for chain_name, balance in chain_supply.items():
            supply = _stablecoin_supply(balance)
            if supply is None:
                continue
            rows.append(
                {
                    "asset_id": asset_id,
                    "chain": str(chain_name),
                    "ts": now,
                    "symbol": symbol,
                    "name": _stringify(asset.get("name")),
                    "peg_type": _stringify(asset.get("pegType")),
                    "supply_usd": supply,
                    "source_endpoint": source_endpoint,
                    "fetched_at": now,
                    "ingest_run_id": ingest_run_id,
                }
            )
    return rows


def normalize_prices(
    payload: Any,
    *,
    source_endpoint: str,
    ingest_run_id: int,
    now: datetime,
) -> list[JsonObject]:
    """Map the ``coins/prices/current`` payload to ``defillama.prices`` rows."""
    if not isinstance(payload, dict):
        return []
    coins = payload.get("coins")
    if not isinstance(coins, dict):
        return []
    rows: list[JsonObject] = []
    for asset_key, record in coins.items():
        if not isinstance(record, dict):
            continue
        price = as_float(record.get("price"))
        if price is None:
            continue
        ts = _price_timestamp(record.get("timestamp"), default=now)
        rows.append(
            {
                "asset_key": str(asset_key),
                "ts": ts,
                "price_usd": price,
                "confidence": as_float(record.get("confidence")),
                "symbol": _stringify(record.get("symbol")),
                "decimals": _maybe_int(record.get("decimals")),
                "source_endpoint": source_endpoint,
                "fetched_at": now,
                "ingest_run_id": ingest_run_id,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Per-table historical normalizers (B-019 backfill blobs)
# ---------------------------------------------------------------------------


def normalize_protocol_history(
    payload: Any,
    *,
    slug: str,
    source_endpoint: str,
    ingest_run_id: int,
    fetched_at: datetime,
) -> list[JsonObject]:
    """Map ``/protocol/{slug}`` to ``defillama.protocol_tvl`` rows.

    Payload shape: ``chainTvls.<chain>.tvl`` is a list of
    ``{date: epoch, totalLiquidityUSD: number}``. We dedupe on (chain, ts)
    inside one slug.
    """
    if not isinstance(payload, dict):
        return []
    chain_tvls = payload.get("chainTvls")
    if not isinstance(chain_tvls, dict):
        return []
    rows: list[JsonObject] = []
    seen: set[tuple[str, datetime]] = set()
    for chain_name, chain_data in chain_tvls.items():
        if not isinstance(chain_data, dict):
            continue
        # Skip the synthetic "borrowed" / "staking" / "<chain>-borrowed" sub-buckets;
        # we want plain TVL only. DeFiLlama mixes these into chainTvls.
        if "-" in str(chain_name):
            continue
        series = chain_data.get("tvl")
        if not isinstance(series, list):
            continue
        for point in series:
            if not isinstance(point, dict):
                continue
            ts = parse_history_timestamp(point.get("date"))
            tvl = as_float(point.get("totalLiquidityUSD"))
            if ts is None or tvl is None:
                continue
            key = (str(chain_name), ts)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "slug": slug,
                    "chain": str(chain_name),
                    "ts": ts,
                    "tvl_usd": tvl,
                    "source_endpoint": source_endpoint,
                    "fetched_at": fetched_at,
                    "ingest_run_id": ingest_run_id,
                }
            )
    return rows


def normalize_stablecoin_history(
    payload: Any,
    *,
    asset_id: str,
    source_endpoint: str,
    ingest_run_id: int,
    fetched_at: datetime,
) -> list[JsonObject]:
    """Map ``/stablecoin/{id}`` to ``defillama.stablecoins`` rows.

    Payload shape: ``chainCirculating.<chain>.tokens`` is a list of points,
    each carrying a ``date`` plus a ``circulating`` (or similar) dict with
    ``peggedUSD``. Older payloads used ``chainBalances``; accept that for
    compatibility.
    """
    if not isinstance(payload, dict):
        return []
    symbol = _stringify(payload.get("symbol"))
    if symbol is None:
        return []
    name = _stringify(payload.get("name"))
    peg_type = _stringify(payload.get("pegType"))
    if "chainCirculating" in payload:
        chain_balances = payload["chainCirculating"]
    else:
        chain_balances = payload.get("chainBalances")
    if not isinstance(chain_balances, dict):
        return []
    rows: list[JsonObject] = []
    seen: set[tuple[str, datetime]] = set()
    for chain_name, chain_data in chain_balances.items():
        if not isinstance(chain_data, dict):
            continue
        series = chain_data.get("tokens")
        if not isinstance(series, list):
            continue
        for point in series:
            if not isinstance(point, dict):
                continue
            ts = parse_history_timestamp(point.get("date"))
            supply = _stablecoin_supply(point)
            if ts is None or supply is None:
                continue
            key = (str(chain_name), ts)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "asset_id": asset_id,
                    "chain": str(chain_name),
                    "ts": ts,
                    "symbol": symbol,
                    "name": name,
                    "peg_type": peg_type,
                    "supply_usd": supply,
                    "source_endpoint": source_endpoint,
                    "fetched_at": fetched_at,
                    "ingest_run_id": ingest_run_id,
                }
            )
    return rows


# ---------------------------------------------------------------------------
# Run orchestration
# ---------------------------------------------------------------------------


def load_config(path: Path) -> JsonObject:
    """Load the normalizer's slice of the shared DeFiLlama config."""
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


def chain_focus_from_config(config: JsonObject) -> list[str]:
    """Return the configured chain focus list, validated."""
    chain_focus = config.get("chain_focus", [])
    if not isinstance(chain_focus, list):
        raise SystemExit("chain_focus must be a list.")
    out = []
    for chain in chain_focus:
        if not isinstance(chain, str) or not chain:
            raise SystemExit("chain_focus must contain only non-empty strings.")
        out.append(chain)
    return out


def latest_collector_run_id() -> int:
    """Return the most recent successful collector run id."""
    return _latest_run_id(COLLECT_ENDPOINT_LABEL, "Run `python -m genkei.ingest.defillama` first.")


def latest_backfill_run_id() -> int:
    """Return the most recent successful backfill run id."""
    return _latest_run_id(
        BACKFILL_ENDPOINT_LABEL,
        "Run `python -m genkei.ingest.defillama --backfill --since YYYY-MM-DD` first.",
    )


def _latest_run_id(endpoint_label: str, hint: str) -> int:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM meta.ingest_runs "
            "WHERE source = %s AND endpoint = %s AND status = 'success' "
            "ORDER BY started_at DESC LIMIT 1",
            [SOURCE_NAME, endpoint_label],
        )
        row = cur.fetchone()
    if row is None:
        raise SystemExit(
            f"No successful DeFiLlama {endpoint_label!r} run found in meta.ingest_runs. {hint}"
        )
    return int(row[0])


def fetch_raw_blobs(source_run_id: int) -> dict[str, RawBlob]:
    """Return ``{endpoint_name: (url, payload, fetched_at)}`` for a collector run."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT endpoint_name, url, payload, fetched_at "
            "FROM meta.raw_blobs WHERE ingest_run_id = %s",
            [source_run_id],
        )
        rows = cur.fetchall()
    if not rows:
        raise SystemExit(f"No raw blobs found for ingest_run_id={source_run_id}.")
    return {name: (url, payload, fetched_at) for name, url, payload, fetched_at in rows}


def normalize(config_path: Path, *, source_run_id: int | None = None) -> int:
    """Run the normalizer once and return the normalizer ``ingest_runs`` id."""
    config = load_config(config_path)
    chain_focus = chain_focus_from_config(config)

    if source_run_id is None:
        source_run_id = latest_collector_run_id()
    blobs = fetch_raw_blobs(source_run_id)

    with db.ingest_run(
        SOURCE_NAME,
        endpoint=NORMALIZE_ENDPOINT_LABEL,
        metadata={"source_run_id": source_run_id},
    ) as run:
        protocol_rows = _rows_for(blobs, "protocols", normalize_protocols, run.id, source_run_id)
        chain_tvl_rows = _chain_tvl_rows(blobs, chain_focus, run.id)
        stablecoin_rows = _rows_for(
            blobs, "stablecoins", normalize_stablecoins, run.id, source_run_id
        )
        price_rows = _rows_for(blobs, "prices_current", normalize_prices, run.id, source_run_id)

        # B-081 — daily collect now ships `protocol_<slug>` blobs for
        # every watchlist protocol. Dispatch them the same way the
        # backfill normalizer does so they land in defillama.protocol_tvl.
        protocol_tvl_rows: list[JsonObject] = []
        for endpoint_name, (url, payload, fetched_at) in blobs.items():
            if not endpoint_name.startswith(PROTOCOL_HISTORY_PREFIX):
                continue
            slug = endpoint_name[len(PROTOCOL_HISTORY_PREFIX) :]
            protocol_tvl_rows.extend(
                normalize_protocol_history(
                    payload,
                    slug=slug,
                    source_endpoint=url,
                    ingest_run_id=run.id,
                    fetched_at=fetched_at,
                )
            )

        with db.connection() as conn:
            run.add_rows(
                db.bulk_upsert(conn, "defillama.protocols", protocol_rows, conflict_keys=["slug"])
            )
            run.add_rows(
                db.bulk_upsert(
                    conn, "defillama.chain_tvl", chain_tvl_rows, conflict_keys=["chain", "ts"]
                )
            )
            # Watchlist-driven per-protocol rows — must land AFTER
            # `defillama.protocols` upsert above because protocol_tvl has
            # an FK on slug → protocols.
            run.add_rows(
                db.bulk_upsert(
                    conn,
                    "defillama.protocol_tvl",
                    protocol_tvl_rows,
                    conflict_keys=["slug", "chain", "ts"],
                )
            )
            run.add_rows(
                db.bulk_upsert(
                    conn,
                    "defillama.stablecoins",
                    stablecoin_rows,
                    conflict_keys=["asset_id", "chain", "ts"],
                )
            )
            run.add_rows(
                db.bulk_upsert(
                    conn, "defillama.prices", price_rows, conflict_keys=["asset_key", "ts"]
                )
            )

        return run.id


def _rows_for(
    blobs: dict[str, RawBlob],
    endpoint_name: str,
    normalizer: Any,
    ingest_run_id: int,
    source_run_id: int,
) -> list[JsonObject]:
    """Run a per-endpoint normalizer for a required blob."""
    blob = blobs.get(endpoint_name)
    if blob is None:
        raise RuntimeError(
            f"Missing required raw blob for endpoint {endpoint_name!r} "
            f"in ingest_run_id={source_run_id}."
        )
    url, payload, fetched_at = blob
    return list(
        normalizer(
            payload,
            source_endpoint=url,
            ingest_run_id=ingest_run_id,
            now=fetched_at,
        )
    )


def normalize_backfill(*, source_run_id: int | None = None) -> int:
    """Process a B-019 backfill run's raw blobs into the lake tables.

    Dispatches each blob by endpoint_name prefix:
      - ``prices_historical_*``  → defillama.prices via normalize_prices
      - ``protocol_*``           → defillama.protocol_tvl via normalize_protocol_history
      - ``stablecoin_*``         → defillama.stablecoins via normalize_stablecoin_history

    Backfill runs don't need the chain_focus config — they're keyed
    entirely on what raw blobs the collector produced.
    """
    if source_run_id is None:
        source_run_id = latest_backfill_run_id()
    blobs = fetch_raw_blobs(source_run_id)

    with db.ingest_run(
        SOURCE_NAME,
        endpoint=NORMALIZE_BACKFILL_ENDPOINT_LABEL,
        metadata={"source_run_id": source_run_id},
    ) as run:
        price_rows: list[JsonObject] = []
        protocol_tvl_rows: list[JsonObject] = []
        stablecoin_rows: list[JsonObject] = []

        for endpoint_name, (url, payload, fetched_at) in blobs.items():
            if endpoint_name.startswith(PRICE_HISTORICAL_PREFIX):
                price_rows.extend(
                    normalize_prices(
                        payload,
                        source_endpoint=url,
                        ingest_run_id=run.id,
                        now=fetched_at,
                    )
                )
            elif endpoint_name.startswith(PROTOCOL_HISTORY_PREFIX):
                slug = endpoint_name[len(PROTOCOL_HISTORY_PREFIX) :]
                protocol_tvl_rows.extend(
                    normalize_protocol_history(
                        payload,
                        slug=slug,
                        source_endpoint=url,
                        ingest_run_id=run.id,
                        fetched_at=fetched_at,
                    )
                )
            elif endpoint_name.startswith(STABLECOIN_HISTORY_PREFIX):
                asset_id = endpoint_name[len(STABLECOIN_HISTORY_PREFIX) :]
                stablecoin_rows.extend(
                    normalize_stablecoin_history(
                        payload,
                        asset_id=asset_id,
                        source_endpoint=url,
                        ingest_run_id=run.id,
                        fetched_at=fetched_at,
                    )
                )
            else:
                LOGGER.debug("backfill normalizer skipping unknown blob: %s", endpoint_name)

        with db.connection() as conn:
            run.add_rows(
                db.bulk_upsert(
                    conn, "defillama.prices", price_rows, conflict_keys=["asset_key", "ts"]
                )
            )
            run.add_rows(
                db.bulk_upsert(
                    conn,
                    "defillama.protocol_tvl",
                    protocol_tvl_rows,
                    conflict_keys=["slug", "chain", "ts"],
                )
            )
            run.add_rows(
                db.bulk_upsert(
                    conn,
                    "defillama.stablecoins",
                    stablecoin_rows,
                    conflict_keys=["asset_id", "chain", "ts"],
                )
            )

        return run.id


def _chain_tvl_rows(
    blobs: dict[str, RawBlob],
    chain_focus: Iterable[str],
    ingest_run_id: int,
) -> list[JsonObject]:
    """Concatenate chain TVL history rows across every focus chain."""
    rows: list[JsonObject] = []
    for chain_name in chain_focus:
        endpoint_name = CHAIN_HISTORY_PREFIX + chain_history_stem(chain_name)
        blob = blobs.get(endpoint_name)
        if blob is None:
            LOGGER.info("no chain history blob for %s; skipping", chain_name)
            continue
        url, payload, fetched_at = blob
        rows.extend(
            normalize_chain_tvl_history(
                payload,
                chain_name=chain_name,
                source_endpoint=url,
                ingest_run_id=ingest_run_id,
                now=fetched_at,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Small coercion helpers
# ---------------------------------------------------------------------------


def _stringify(value: Any) -> str | None:
    """Coerce a JSON scalar to ``str`` while preserving real missingness."""
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    return str(value)


def _maybe_int(value: Any) -> int | None:
    """Coerce numeric values to ``int`` while preserving missingness."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _stablecoin_supply(balance: Any) -> float | None:
    """Pick a USD supply figure out of a DeFiLlama chainBalances entry."""
    if isinstance(balance, dict):
        for outer_key in ("current", "circulating"):
            outer = balance.get(outer_key)
            if isinstance(outer, dict):
                for inner_key in ("peggedUSD", "current", "circulating"):
                    value = as_float(outer.get(inner_key))
                    if value is not None:
                        return value
            else:
                value = as_float(outer)
                if value is not None:
                    return value
        for key in ("peggedUSD", "supply"):
            value = as_float(balance.get(key))
            if value is not None:
                return value
        return None
    return as_float(balance)


def _price_timestamp(value: Any, *, default: datetime) -> datetime:
    """Parse the per-coin price timestamp; fall back to blob ``fetched_at``."""
    parsed = parse_history_timestamp(value)
    return parsed if parsed is not None else default


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize DeFiLlama raw blobs into defillama.* tables."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument(
        "--source-run-id",
        type=int,
        default=None,
        help="Collector or backfill ingest_run id. Default: latest success of the chosen mode.",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help=(
            "Process a B-019 backfill run rather than a daily collect run. "
            "Dispatches blobs by endpoint_name prefix."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv or sys.argv[1:])
    if args.backfill:
        run_id = normalize_backfill(source_run_id=args.source_run_id)
        print(f"DeFiLlama backfill normalizer wrote ingest_run_id={run_id}")
    else:
        run_id = normalize(args.config, source_run_id=args.source_run_id)
        print(f"DeFiLlama normalizer wrote ingest_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
