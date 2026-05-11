"""CoinGecko normalizer — reads meta.raw_blobs, upserts coingecko.* (B-034).

A normalizer run is itself a row in ``meta.ingest_runs`` with
``endpoint='normalize'`` and ``metadata.source_run_id`` pointing at the
collector run whose blobs were processed. Re-running is idempotent:
every write is an ``ON CONFLICT DO UPDATE`` keyed on the table's
natural PK.

Two blob shapes dispatched by endpoint_name prefix:

  - ``coin_<id>``           → coin metadata row in ``coingecko.coins``
  - ``market_chart_<id>``   → historical price/market_cap/volume rows in
                               ``coingecko.market_data``

market_chart payload shape (G-024):
  CoinGecko returns three parallel arrays — ``prices``, ``market_caps``,
  ``total_volumes`` — each a list of ``[unix_ms, value]`` pairs. The
  arrays are not always the same length and the timestamps don't always
  align. We zip them by *timestamp* (not index): a row lands only when
  we have a timestamp present in all three arrays. Drops cleanly when
  arrays have minor offsets at the head/tail (common for new coins).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timezone
from typing import Any

from genkei.common import db

SOURCE_NAME = "coingecko"
NORMALIZE_ENDPOINT_LABEL = "normalize"
COLLECT_ENDPOINT_LABEL = "collect"
COIN_BLOB_PREFIX = "coin_"
MARKET_CHART_BLOB_PREFIX = "market_chart_"
RawBlob = tuple[str, Any, datetime]
JsonObject = dict[str, Any]
LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def parse_unix_ms(value: Any) -> datetime | None:
    """Parse a CoinGecko unix-milliseconds timestamp into a UTC datetime."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        seconds = float(value) / 1000.0
    except (TypeError, ValueError):
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def parse_iso_date(value: Any) -> date | None:
    """Parse a YYYY-MM-DD genesis_date into a ``date``."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Per-table normalizers
# ---------------------------------------------------------------------------


def normalize_coin(
    payload: Any,
    *,
    coingecko_id: str,
    source_endpoint: str,
    ingest_run_id: int,
    fetched_at: datetime,
) -> JsonObject | None:
    """Map a ``/coins/{id}`` payload to a ``coingecko.coins`` row."""
    if not isinstance(payload, dict):
        return None
    symbol = payload.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        return None
    # description is a localized dict; pull English when present.
    description_block = payload.get("description")
    description = None
    if isinstance(description_block, dict):
        desc_en = description_block.get("en")
        if isinstance(desc_en, str) and desc_en:
            description = desc_en
    # homepage is nested under links.homepage as a list of URLs; first non-empty wins.
    homepage = None
    links = payload.get("links")
    if isinstance(links, dict):
        homepage_list = links.get("homepage")
        if isinstance(homepage_list, list):
            for url in homepage_list:
                if isinstance(url, str) and url:
                    homepage = url
                    break
    categories = payload.get("categories")
    if not isinstance(categories, list):
        categories = None
    else:
        categories = [str(c) for c in categories if isinstance(c, str)]
    return {
        "coingecko_id": coingecko_id,
        "symbol": str(symbol).upper(),
        "name": _stringify(payload.get("name")),
        "market_cap_rank": _maybe_int(payload.get("market_cap_rank")),
        "genesis_date": parse_iso_date(payload.get("genesis_date")),
        "description": description,
        "homepage": homepage,
        "categories": categories,
        "source_endpoint": source_endpoint,
        "fetched_at": fetched_at,
        "ingest_run_id": ingest_run_id,
    }


def normalize_market_chart(
    payload: Any,
    *,
    coingecko_id: str,
    source_endpoint: str,
    ingest_run_id: int,
    fetched_at: datetime,
) -> list[JsonObject]:
    """Map a ``/market_chart`` payload to ``coingecko.market_data`` rows.

    The three parallel arrays (``prices``, ``market_caps``,
    ``total_volumes``) are indexed by timestamp; we build per-array
    dicts keyed on the unix-ms and emit rows only for timestamps present
    in all three. Sorted by timestamp ascending so the resulting rows
    insert in stable order.
    """
    if not isinstance(payload, dict):
        return []
    prices = _index_by_ts(payload.get("prices"))
    market_caps = _index_by_ts(payload.get("market_caps"))
    volumes = _index_by_ts(payload.get("total_volumes"))
    common = set(prices) & set(market_caps) & set(volumes)
    if not common:
        return []
    rows: list[JsonObject] = []
    for ts in sorted(common):
        rows.append(
            {
                "coingecko_id": coingecko_id,
                "ts": ts,
                "price_usd": prices[ts],
                "market_cap_usd": market_caps[ts],
                "volume_usd": volumes[ts],
                "source_endpoint": source_endpoint,
                "fetched_at": fetched_at,
                "ingest_run_id": ingest_run_id,
            }
        )
    return rows


def _index_by_ts(series: Any) -> dict[datetime, float]:
    """Convert a list of ``[unix_ms, value]`` pairs to ``{ts: value}``."""
    if not isinstance(series, list):
        return {}
    out: dict[datetime, float] = {}
    for item in series:
        if not isinstance(item, list) or len(item) != 2:
            continue
        ts = parse_unix_ms(item[0])
        if ts is None:
            continue
        value = _as_numeric(item[1])
        if value is None:
            continue
        # Last write wins on duplicate ts (CoinGecko rarely emits dupes,
        # but if it does the later value is canonical).
        out[ts] = value
    return out


# ---------------------------------------------------------------------------
# Run orchestration
# ---------------------------------------------------------------------------


def latest_collector_run_id() -> int:
    """Return the most recent successful CoinGecko collector run id."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM meta.ingest_runs "
            "WHERE source = %s AND endpoint = %s AND status = 'success' "
            "ORDER BY started_at DESC LIMIT 1",
            [SOURCE_NAME, COLLECT_ENDPOINT_LABEL],
        )
        row = cur.fetchone()
    if row is None:
        raise SystemExit(
            "No successful CoinGecko collector run found in meta.ingest_runs. "
            "Run `python -m genkei.ingest.coingecko` first."
        )
    return int(row[0])


def fetch_raw_blobs(source_run_id: int) -> dict[str, RawBlob]:
    """Return ``{endpoint_name: (url, payload, fetched_at)}`` for a run."""
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


def normalize(*, source_run_id: int | None = None) -> int:
    """Run the CoinGecko normalizer once and return the normalizer run id."""
    if source_run_id is None:
        source_run_id = latest_collector_run_id()
    blobs = fetch_raw_blobs(source_run_id)

    with db.ingest_run(
        SOURCE_NAME,
        endpoint=NORMALIZE_ENDPOINT_LABEL,
        metadata={"source_run_id": source_run_id},
    ) as run:
        coin_rows: list[JsonObject] = []
        market_rows: list[JsonObject] = []

        for endpoint_name, (url, payload, fetched_at) in blobs.items():
            if endpoint_name.startswith(MARKET_CHART_BLOB_PREFIX):
                cgid = endpoint_name[len(MARKET_CHART_BLOB_PREFIX) :]
                market_rows.extend(
                    normalize_market_chart(
                        payload,
                        coingecko_id=cgid,
                        source_endpoint=url,
                        ingest_run_id=run.id,
                        fetched_at=fetched_at,
                    )
                )
            elif endpoint_name.startswith(COIN_BLOB_PREFIX):
                cgid = endpoint_name[len(COIN_BLOB_PREFIX) :]
                row = normalize_coin(
                    payload,
                    coingecko_id=cgid,
                    source_endpoint=url,
                    ingest_run_id=run.id,
                    fetched_at=fetched_at,
                )
                if row is not None:
                    coin_rows.append(row)
            else:
                LOGGER.debug("CoinGecko normalizer skipping unknown blob: %s", endpoint_name)

        # coins must land before market_data (FK).
        with db.connection() as conn:
            run.add_rows(
                db.bulk_upsert(conn, "coingecko.coins", coin_rows, conflict_keys=["coingecko_id"])
            )
            run.add_rows(
                db.bulk_upsert(
                    conn,
                    "coingecko.market_data",
                    market_rows,
                    conflict_keys=["coingecko_id", "ts"],
                )
            )

        return run.id


# ---------------------------------------------------------------------------
# Small coercion helpers
# ---------------------------------------------------------------------------


def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    return str(value)


def _maybe_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_numeric(value: Any) -> float | None:
    """Coerce a numeric scalar to ``float`` while preserving missingness."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize CoinGecko raw blobs into coingecko.* tables."
    )
    parser.add_argument(
        "--source-run-id",
        type=int,
        default=None,
        help="CoinGecko collector ingest_run id. Default: latest success.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv or sys.argv[1:])
    run_id = normalize(source_run_id=args.source_run_id)
    print(f"CoinGecko normalizer wrote ingest_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
