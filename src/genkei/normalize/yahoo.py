"""Yahoo normalizer — reads meta.raw_blobs, upserts yahoo.candles (B-092).

A normalizer run is itself a row in ``meta.ingest_runs`` with
``endpoint='normalize'`` and ``metadata.source_run_id`` pointing at
the collector run whose blobs were processed. Re-running is
idempotent: every write is an ``ON CONFLICT DO UPDATE`` keyed on
the ``(ticker, ts)`` PK.

Single blob shape (both daily + backfill modes):

  ``chart_<ticker>``                  (daily mode)
  ``chart_<ticker>_<since>_<until>``  (backfill mode)

Yahoo payload shape:
  ``{chart: {result: [{meta, timestamp, indicators: {quote: [{open, high, low, close, volume}], adjclose: [{adjclose}]}}]}}``

Each of ``timestamp``, ``quote.open/high/low/close/volume``, and
``adjclose.adjclose`` are parallel arrays indexed by position. We
zip by index — Yahoo guarantees alignment. Rows with any NULL in
the NOT NULL columns (open/high/low/close/volume) are skipped to
preserve the schema invariant; ``adj_close`` is nullable so a NULL
there is preserved.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from genkei.common import db

SOURCE_NAME = "yahoo"
NORMALIZE_ENDPOINT_LABEL = "normalize"
COLLECT_ENDPOINT_LABEL = "collect"
BACKFILL_ENDPOINT_LABEL = "backfill"
CHART_BLOB_PREFIX = "chart_"
RawBlob = tuple[str, Any, datetime]
JsonObject = dict[str, Any]
LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _parse_unix_seconds(value: Any) -> datetime | None:
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
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ticker_from_endpoint_name(endpoint_name: str) -> str | None:
    """Extract the ticker from ``chart_<ticker>[_<since>_<until>]``.

    Both daily and backfill blob names encode the ticker after the
    ``chart_`` prefix (chosen to mirror Yahoo's `/v8/finance/chart/`
    endpoint and avoid colliding with Coinbase's ``candles_%`` spec
    pattern in `genkei.common.schema_drift`). Backfill names append
    ``_<since>_<until>`` where each date is ISO-8601. Walk the
    underscore-split parts and stop at the first 10-char ISO date.
    """
    if not endpoint_name.startswith(CHART_BLOB_PREFIX):
        return None
    tail = endpoint_name[len(CHART_BLOB_PREFIX) :]
    parts = tail.split("_")
    ticker_parts: list[str] = []
    for part in parts:
        if len(part) == 10 and part[:4].isdigit() and part[4] == "-":
            break
        ticker_parts.append(part)
    if not ticker_parts:
        return None
    joined = "_".join(ticker_parts)
    return joined or None


# ---------------------------------------------------------------------------
# Per-blob normalizer
# ---------------------------------------------------------------------------


def normalize_chart(
    payload: Any,
    *,
    ticker: str,
    source_endpoint: str,
    ingest_run_id: int,
    fetched_at: datetime,
) -> list[JsonObject]:
    """Walk a Yahoo chart payload into ``yahoo.candles`` rows.

    Returns one row per timestamp that has all NOT NULL fields
    populated. Rows with any missing OHLCV value are skipped (Yahoo
    occasionally returns NULL on holiday-adjacent days or pre-IPO
    placeholders).
    """
    if not isinstance(payload, dict):
        return []
    chart = payload.get("chart")
    if not isinstance(chart, dict):
        return []
    results = chart.get("result")
    if not isinstance(results, list) or not results:
        return []
    result = results[0]
    if not isinstance(result, dict):
        return []
    timestamps = result.get("timestamp")
    indicators = result.get("indicators")
    if not isinstance(timestamps, list) or not isinstance(indicators, dict):
        return []
    quote_array = indicators.get("quote")
    if not isinstance(quote_array, list) or not quote_array:
        return []
    quote = quote_array[0]
    if not isinstance(quote, dict):
        return []
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    # adjclose is optional — Yahoo may omit for very new tickers.
    adjclose_array = indicators.get("adjclose")
    adj_closes: list[Any] = []
    if isinstance(adjclose_array, list) and adjclose_array:
        adj = adjclose_array[0]
        if isinstance(adj, dict):
            adj_closes = adj.get("adjclose") or []

    rows: list[JsonObject] = []
    for i, raw_ts in enumerate(timestamps):
        ts = _parse_unix_seconds(raw_ts)
        if ts is None:
            continue

        # Safe positional lookups — Yahoo arrays *should* be aligned
        # with `timestamp` but defensive indexing keeps us crash-free
        # if they ever aren't.
        def _at(arr: list[Any], idx: int) -> Any:
            return arr[idx] if idx < len(arr) else None

        open_ = _as_numeric(_at(opens, i))
        high = _as_numeric(_at(highs, i))
        low = _as_numeric(_at(lows, i))
        close = _as_numeric(_at(closes, i))
        volume = _as_numeric(_at(volumes, i))
        adj_close = _as_numeric(_at(adj_closes, i)) if adj_closes else None

        if (
            open_ is None
            or high is None
            or low is None
            or close is None
            or volume is None
        ):
            # NOT NULL columns — skip the row rather than fail the batch.
            continue
        rows.append(
            {
                "ticker": ticker,
                "ts": ts,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "adj_close": adj_close,
                "volume": volume,
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
            "No successful Yahoo collector run found in meta.ingest_runs. "
            "Run `python -m genkei.ingest.yahoo` first."
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
    """Run the Yahoo normalizer once and return the normalizer run id."""
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
            ticker = _ticker_from_endpoint_name(endpoint_name)
            if ticker is None:
                LOGGER.debug("Yahoo normalizer skipping unknown blob: %s", endpoint_name)
                continue
            all_rows.extend(
                normalize_chart(
                    payload,
                    ticker=ticker,
                    source_endpoint=url,
                    ingest_run_id=run.id,
                    fetched_at=fetched_at,
                )
            )

        with db.connection() as conn:
            run.add_rows(
                db.bulk_upsert(
                    conn,
                    "yahoo.candles",
                    all_rows,
                    conflict_keys=["ticker", "ts"],
                )
            )

        return run.id


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize Yahoo raw_blobs into yahoo.candles."
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
    print(f"Yahoo normalizer wrote ingest_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
