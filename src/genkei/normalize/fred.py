"""FRED normalizer — reads meta.raw_blobs, upserts fred.* (B-028).

A normalizer run is itself a row in ``meta.ingest_runs`` with
``endpoint='normalize'`` and ``metadata.source_run_id`` pointing at the
collector run whose blobs were processed. Re-running is idempotent:
every write is an ``ON CONFLICT DO UPDATE`` keyed on the table's
natural PK.

Vintage handling (D-013): ``fred.observations`` PK is
``(series_id, ts, realtime_start)``. Each FRED observation revision
shows up as a distinct row keyed on its ``realtime_start`` date — so a
series with a 2024-Q1 GDP value first published on 2024-04-25 and
revised on 2024-05-30 produces two rows for ts=2024-Q1, both retained.
``realtime_end`` carries forward the date the value stopped being
current (or 9999-12-31 for the still-current value).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timezone
from typing import Any

from genkei.common import db

SOURCE_NAME = "fred"
NORMALIZE_ENDPOINT_LABEL = "normalize"
COLLECT_ENDPOINT_LABEL = "collect"
SERIES_BLOB_PREFIX = "series_"
OBSERVATIONS_BLOB_PREFIX = "observations_"
RawBlob = tuple[str, Any, datetime]
JsonObject = dict[str, Any]
LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def parse_fred_date(value: Any) -> date | None:
    """Parse a FRED YYYY-MM-DD string into a ``date``."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse_fred_datetime(value: Any) -> datetime | None:
    """Parse FRED ``last_updated`` (e.g. ``"2024-05-30 08:30:00-05"``).

    Falls back to ``date`` parsing when only YYYY-MM-DD is present.
    """
    if not isinstance(value, str) or not value:
        return None
    cleaned = value.replace(" ", "T", 1)
    # FRED tacks on ``-05`` (no minutes); normalise to ``-05:00`` for fromisoformat.
    if len(cleaned) >= 3 and cleaned[-3] in ("+", "-") and cleaned[-2:].isdigit():
        cleaned = cleaned + ":00"
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        fred_date = parse_fred_date(value)
        return (
            datetime.combine(fred_date, datetime.min.time(), tzinfo=timezone.utc)
            if fred_date is not None
            else None
        )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_fred_value(raw: Any) -> float | None:
    """Coerce FRED's observation value to ``float``; ``"."`` means missing."""
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        if raw == "." or not raw.strip():
            return None
        try:
            return float(raw)
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Per-table normalizers
# ---------------------------------------------------------------------------


def normalize_series(
    payload: Any,
    *,
    series_id: str,
    source_endpoint: str,
    ingest_run_id: int,
    fetched_at: datetime,
) -> JsonObject | None:
    """Map a ``/series`` payload to a single ``fred.series`` row dict."""
    if not isinstance(payload, dict):
        return None
    seriess = payload.get("seriess")
    if not isinstance(seriess, list) or not seriess:
        return None
    item = seriess[0]
    if not isinstance(item, dict):
        return None
    return {
        "series_id": series_id,
        "title": _stringify(item.get("title")),
        "units": _stringify(item.get("units")),
        "units_short": _stringify(item.get("units_short")),
        "frequency": _stringify(item.get("frequency")),
        "frequency_short": _stringify(item.get("frequency_short")),
        "seasonal_adjustment": _stringify(item.get("seasonal_adjustment")),
        "seasonal_adjustment_short": _stringify(item.get("seasonal_adjustment_short")),
        "notes": _stringify(item.get("notes")),
        "popularity": _maybe_int(item.get("popularity")),
        "observation_start": parse_fred_date(item.get("observation_start")),
        "observation_end": parse_fred_date(item.get("observation_end")),
        "last_updated": parse_fred_datetime(item.get("last_updated")),
        "source_endpoint": source_endpoint,
        "fetched_at": fetched_at,
        "ingest_run_id": ingest_run_id,
    }


def normalize_observations(
    payload: Any,
    *,
    series_id: str,
    source_endpoint: str,
    ingest_run_id: int,
    fetched_at: datetime,
) -> list[JsonObject]:
    """Map a ``/series/observations`` payload to ``fred.observations`` rows.

    With ``realtime_start=1776-07-04&realtime_end=9999-12-31`` the response
    carries every vintage of every observation — newer revisions appear as
    additional rows with a fresh ``realtime_start``. We dedupe on
    ``(series_id, ts, realtime_start)`` to mirror the schema PK.
    """
    if not isinstance(payload, dict):
        return []
    observations = payload.get("observations")
    if not isinstance(observations, list):
        return []
    rows: list[JsonObject] = []
    seen: set[tuple[datetime, date]] = set()
    for obs in observations:
        if not isinstance(obs, dict):
            continue
        obs_date = parse_fred_date(obs.get("date"))
        rt_start = parse_fred_date(obs.get("realtime_start"))
        rt_end = parse_fred_date(obs.get("realtime_end"))
        if obs_date is None or rt_start is None or rt_end is None:
            continue
        ts = datetime.combine(obs_date, datetime.min.time(), tzinfo=timezone.utc)
        key = (ts, rt_start)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "series_id": series_id,
                "ts": ts,
                "realtime_start": rt_start,
                "realtime_end": rt_end,
                "value": parse_fred_value(obs.get("value")),
                "source_endpoint": source_endpoint,
                "fetched_at": fetched_at,
                "ingest_run_id": ingest_run_id,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Run orchestration
# ---------------------------------------------------------------------------


def latest_collector_run_id() -> int:
    """Return the most recent successful FRED collector run id."""
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
            "No successful FRED collector run found in meta.ingest_runs. "
            "Run `python -m genkei.ingest.fred` first."
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
    """Run the FRED normalizer once and return the normalizer run id."""
    if source_run_id is None:
        source_run_id = latest_collector_run_id()
    blobs = fetch_raw_blobs(source_run_id)

    with db.ingest_run(
        SOURCE_NAME,
        endpoint=NORMALIZE_ENDPOINT_LABEL,
        metadata={"source_run_id": source_run_id},
    ) as run:
        series_rows: list[JsonObject] = []
        observation_rows: list[JsonObject] = []

        for endpoint_name, (url, payload, fetched_at) in blobs.items():
            if endpoint_name.startswith(SERIES_BLOB_PREFIX):
                series_id = endpoint_name[len(SERIES_BLOB_PREFIX) :]
                row = normalize_series(
                    payload,
                    series_id=series_id,
                    source_endpoint=url,
                    ingest_run_id=run.id,
                    fetched_at=fetched_at,
                )
                if row is not None:
                    series_rows.append(row)
            elif endpoint_name.startswith(OBSERVATIONS_BLOB_PREFIX):
                series_id = endpoint_name[len(OBSERVATIONS_BLOB_PREFIX) :]
                observation_rows.extend(
                    normalize_observations(
                        payload,
                        series_id=series_id,
                        source_endpoint=url,
                        ingest_run_id=run.id,
                        fetched_at=fetched_at,
                    )
                )
            else:
                LOGGER.debug("FRED normalizer skipping unknown blob: %s", endpoint_name)

        with db.connection() as conn:
            run.add_rows(
                db.bulk_upsert(
                    conn,
                    "fred.series",
                    series_rows,
                    conflict_keys=["series_id"],
                )
            )
            run.add_rows(
                db.bulk_upsert(
                    conn,
                    "fred.observations",
                    observation_rows,
                    conflict_keys=["series_id", "ts", "realtime_start"],
                )
            )

        return run.id


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


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize FRED raw blobs into fred.* tables.")
    parser.add_argument(
        "--source-run-id",
        type=int,
        default=None,
        help="FRED collector ingest_run id. Default: latest success.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv or sys.argv[1:])
    run_id = normalize(source_run_id=args.source_run_id)
    print(f"FRED normalizer wrote ingest_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
