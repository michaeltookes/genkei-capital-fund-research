"""Coinbase normalizer — reads meta.raw_blobs, upserts coinbase.candles (B-035).

A normalizer run is itself a row in ``meta.ingest_runs`` with
``endpoint='normalize'`` and ``metadata.source_run_id`` pointing at
the collector run whose blobs were processed. Re-running is
idempotent: every write is an ``ON CONFLICT DO UPDATE`` keyed on the
(product, ts) PK.

Single blob shape:

  ``candles_<product>``                    (daily mode)
  ``candles_<product>_<start>_<end>``      (backfill mode)

Payload is a JSON array of arrays — Coinbase candles come in the
unusual order ``[time, low, high, open, close, volume]``. The parser
unpacks by position; an empty list (pre-listing window or no data) is
silently accepted as zero rows.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from genkei.common import db

SOURCE_NAME = "coinbase"
NORMALIZE_ENDPOINT_LABEL = "normalize"
COLLECT_ENDPOINT_LABEL = "collect"
BACKFILL_ENDPOINT_LABEL = "backfill"
CANDLES_BLOB_PREFIX = "candles_"
RawBlob = tuple[str, Any, datetime]
JsonObject = dict[str, Any]
LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _parse_unix_seconds(value: Any) -> datetime | None:
    """Coinbase candle timestamps are unix-seconds (not ms like CoinGecko)."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _as_numeric(value: Any) -> float | None:
    """Coerce a numeric scalar to ``float`` while preserving missingness."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _product_from_endpoint_name(endpoint_name: str) -> str | None:
    """Extract ``BTC-USD`` from ``candles_BTC-USD`` or ``candles_BTC-USD_<start>_<end>``.

    Both daily and backfill blob shapes encode the product after the
    ``candles_`` prefix. Backfill names append ``_<start>_<end>`` where
    each date is ISO-8601 (e.g. ``2015-07-20``). The product itself is
    of the form ``<BASE>-<QUOTE>`` with letters only, so we split on
    underscore and take everything before the first ``YYYY`` token.
    """
    if not endpoint_name.startswith(CANDLES_BLOB_PREFIX):
        return None
    tail = endpoint_name[len(CANDLES_BLOB_PREFIX) :]
    parts = tail.split("_")
    # Walk from left; the product token doesn't contain digits in its
    # head segment. The first part starting with 4 digits is the start
    # date.
    product_parts: list[str] = []
    for part in parts:
        if len(part) == 10 and part[:4].isdigit() and part[4] == "-":
            break
        product_parts.append(part)
    if not product_parts:
        return None
    joined = "_".join(product_parts)
    return joined or None


# ---------------------------------------------------------------------------
# Per-blob normalizer
# ---------------------------------------------------------------------------


def normalize_candles(
    payload: Any,
    *,
    product: str,
    source_endpoint: str,
    ingest_run_id: int,
    fetched_at: datetime,
) -> list[JsonObject]:
    """Map a Coinbase candles payload to ``coinbase.candles`` rows.

    Coinbase candle row shape: ``[time, low, high, open, close, volume]``.
    Note the order — low/high before open/close, not the typical
    OHLCV. We unpack by position.
    """
    if not isinstance(payload, list):
        return []
    rows: list[JsonObject] = []
    for candle in payload:
        if not isinstance(candle, list) or len(candle) < 6:
            continue
        ts = _parse_unix_seconds(candle[0])
        low = _as_numeric(candle[1])
        high = _as_numeric(candle[2])
        open_ = _as_numeric(candle[3])
        close = _as_numeric(candle[4])
        volume = _as_numeric(candle[5])
        if (
            ts is None
            or low is None
            or high is None
            or open_ is None
            or close is None
            or volume is None
        ):
            # All six fields are NOT NULL in the table; skip
            # malformed rows rather than failing the whole batch.
            continue
        rows.append(
            {
                "product": product,
                "ts": ts,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume_base": volume,
                "source_endpoint": source_endpoint,
                "fetched_at": fetched_at,
                "ingest_run_id": ingest_run_id,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def latest_collector_run_id() -> int:
    """Pick the most recent successful collector OR backfill run."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM meta.ingest_runs "
            "WHERE source = %s AND endpoint IN (%s, %s) AND status = 'success' "
            "ORDER BY started_at DESC LIMIT 1",
            [SOURCE_NAME, COLLECT_ENDPOINT_LABEL, BACKFILL_ENDPOINT_LABEL],
        )
        row = cur.fetchone()
    if row is None:
        raise SystemExit(
            "No successful Coinbase collector run found in meta.ingest_runs. "
            "Run `python -m genkei.ingest.coinbase` first."
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
    """Run the Coinbase normalizer once and return the normalizer run id."""
    if source_run_id is None:
        source_run_id = latest_collector_run_id()
    blobs = fetch_raw_blobs(source_run_id)

    with db.ingest_run(
        SOURCE_NAME,
        endpoint=NORMALIZE_ENDPOINT_LABEL,
        metadata={"source_run_id": source_run_id},
    ) as run:
        all_rows: list[JsonObject] = []

        for endpoint_name, (url, payload, fetched_at) in blobs.items():
            product = _product_from_endpoint_name(endpoint_name)
            if product is None:
                LOGGER.debug("Coinbase normalizer skipping unknown blob: %s", endpoint_name)
                continue
            all_rows.extend(
                normalize_candles(
                    payload,
                    product=product,
                    source_endpoint=url,
                    ingest_run_id=run.id,
                    fetched_at=fetched_at,
                )
            )

        with db.connection() as conn:
            run.add_rows(
                db.bulk_upsert(
                    conn,
                    "coinbase.candles",
                    all_rows,
                    conflict_keys=["product", "ts"],
                )
            )

        return run.id


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize Coinbase raw_blobs into coinbase.candles."
    )
    parser.add_argument(
        "--source-run-id",
        type=int,
        default=None,
        help="Specific collector run to process. Default: latest success.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv or sys.argv[1:])
    run_id = normalize(source_run_id=args.source_run_id)
    print(f"Coinbase normalizer wrote ingest_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
