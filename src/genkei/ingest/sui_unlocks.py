"""SUI token unlock / vesting schedule collector (B-089; DeFiLlama since B-145).

Fetches the SUI emission/unlock schedule from DeFiLlama's open datasets
bucket (``https://defillama-datasets.llama.fi/emissions/sui`` — the same
unauthenticated URL the defillama.com/unlocks frontend reads) and lands
one row per ``(allocation_name, unlock_date)`` in ``onchain.sui_unlocks``.

**History:** v1 (B-089, 2026-06-07) scraped CryptoRank's vesting page,
which exposed only ONE of SUI's allocation categories un-gated
("Community Reserves", 10.648% of supply — the seven signal-rich
categories were paywalled, tracked as B-115). CryptoRank then put the
page behind a Cloudflare JS challenge (observed 2026-09: 403 + "Just a
moment..." interstitial), killing the scrape outright. The DeFiLlama
dataset replaces it and **closes the B-115 gap at the same time**: all
8 allocation series are published, they sum to exactly the 10B max
supply, and the schedule spans TGE (2023-05-03) through 2030 — past and
future. The Series A / Series B / Early Contributors VC tranches that
drive the unlock-pressure thesis are all covered.

**Taxonomy change at the switchover:** DeFiLlama's "Community Reserve"
(4.972B, 49.72% of supply) is Sui's full reserve bucket, NOT the same
series as CryptoRank's "Community Reserves" sub-bucket (1.065B, 10.6%).
The legacy CryptoRank rows were removed from the table when this
collector shipped (one-time delete, recorded in
``docs/sources/sui-unlocks.md``) — keeping both taxonomies would
double-count reserve supply in any SUM over the table.

**Shape:** the upstream payload is per-allocation *cumulative* unlocked
curves sampled daily. The parser converts them to discrete batch rows by
taking day-over-day deltas; a positive delta on day *i* is recorded as
an unlock dated at day *i-1*'s timestamp (the cumulative figure is
"unlocked as of start of day", so the increment belongs to the earlier
day — validated against the TGE cliffs, which land exactly on
2023-05-03). Mid-schedule monthly batches can land ±1 day vs the
canonical vesting date because of the daily sampling grid; immaterial at
the table's monthly-batch granularity. ``vesting_type`` is NULL for
DeFiLlama-sourced rows — the cumulative curves don't distinguish cliff
from linear tranches on the same date.

The schedule is effectively static for shipped batches; the collector
still runs daily so forward-schedule revisions are picked up promptly.
Idempotent on the ``(allocation_name, unlock_date)`` PK; the upsert
overwrites prior rows in case a forward batch is revised upstream.

No API key required. (Note: DeFiLlama's *api.llama.fi* ``/emission``
endpoints are paid-tier (HTTP 402); the datasets bucket is the free,
open path and is what their own frontend uses.)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from genkei.common import db
from genkei.common.http import HttpClient, RateLimit

SOURCE_NAME = "sui_unlocks"
COLLECT_ENDPOINT_LABEL = "collect"

LLAMA_SUI_EMISSIONS_URL = "https://defillama-datasets.llama.fi/emissions/sui"

DEFAULT_RATE_LIMIT = RateLimit.per_second(1)

LOGGER = logging.getLogger(__name__)

_TOKEN_QUANT = Decimal("0.0001")
_PCT_QUANT = Decimal("0.0001")


@dataclass(frozen=True)
class _UnlockRow:
    """A normalized per-batch unlock row ready for bulk_upsert."""

    allocation_name: str
    unlock_date: date
    allocation_total_tokens: Decimal
    allocation_total_percent_of_supply: Decimal
    is_tge: bool
    unlock_percent_of_allocation: Decimal
    unlock_tokens: Decimal
    vesting_type: str | None


def _coerce_decimal(raw: Any) -> Decimal | None:
    """Pull a Decimal from any JSON-numeric source (int / float / str)."""
    if raw is None:
        return None
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return Decimal(str(raw))
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return None
        try:
            return Decimal(stripped)
        except (ValueError, InvalidOperation):
            return None
        except Exception:  # pragma: no cover - defensive
            # A non-numeric string is benign (handled above); anything else
            # is a surprise worth surfacing rather than swallowing in
            # unattended daily ingest (B-121).
            LOGGER.warning(
                "sui_unlocks _coerce_decimal: unexpected error coercing %r to Decimal",
                stripped,
                exc_info=True,
            )
            return None
    return None


def _ts_to_utc_date(raw: Any) -> date | None:
    """Parse a unix-seconds timestamp (int/float/str) into a UTC date."""
    value = _coerce_decimal(raw)
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).date()
    except (ValueError, OSError, OverflowError):
        return None


def _series_points(series: dict[str, Any]) -> list[tuple[date, Decimal]]:
    """Extract a series' (date, cumulative_unlocked) points, sorted by date.

    Malformed points (unparseable timestamp or unlocked value) are dropped
    with a warning rather than failing the whole series.
    """
    raw_points = series.get("data")
    if not isinstance(raw_points, list):
        return []
    points: list[tuple[date, Decimal]] = []
    for point in raw_points:
        if not isinstance(point, dict):
            continue
        point_date = _ts_to_utc_date(point.get("timestamp"))
        unlocked = _coerce_decimal(point.get("unlocked"))
        if point_date is None or unlocked is None:
            LOGGER.warning(
                "SUI emissions series %r has a malformed point — dropping it",
                series.get("label"),
            )
            continue
        points.append((point_date, unlocked))
    points.sort(key=lambda p: p[0])
    return points


def parse_unlock_rows(payload: dict[str, Any]) -> list[_UnlockRow]:
    """Decode the DeFiLlama emissions payload into per-batch unlock rows.

    Walks ``documentedData.data`` (one series per allocation, cumulative
    unlocked sampled daily) and emits one row per positive day-over-day
    delta, dated at the earlier sample's date (see module docstring).

    ``allocation_total_percent_of_supply`` is derived from the series
    totals themselves (they sum to the 10B max supply) rather than
    hardcoding the supply constant.
    """
    if not isinstance(payload, dict):
        raise ValueError(
            f"DeFiLlama emissions payload is not a JSON object: "
            f"{type(payload).__name__}"
        )
    try:
        series_list = payload["documentedData"]["data"]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            f"DeFiLlama emissions payload missing documentedData.data: {exc}"
        ) from exc
    if not isinstance(series_list, list) or not series_list:
        raise ValueError(
            "DeFiLlama emissions documentedData.data is empty or not a list"
        )

    parsed: list[tuple[str, list[tuple[date, Decimal]]]] = []
    for series in series_list:
        if not isinstance(series, dict):
            continue
        label = series.get("label")
        if not isinstance(label, str) or not label.strip():
            LOGGER.warning("SUI emissions series missing label — skipping")
            continue
        points = _series_points(series)
        if len(points) < 2:
            LOGGER.warning(
                "SUI emissions series %r has <2 usable points — skipping", label
            )
            continue
        parsed.append((label.strip(), points))

    if not parsed:
        raise ValueError("DeFiLlama emissions payload yielded no usable series")

    supply_total = sum((points[-1][1] for _, points in parsed), Decimal(0))
    if supply_total <= 0:
        raise ValueError(
            "DeFiLlama emissions series totals sum to zero — cannot derive "
            "percent-of-supply"
        )
    tge_date = min(points[0][0] for _, points in parsed)

    rows: list[_UnlockRow] = []
    for label, points in parsed:
        allocation_total = points[-1][1]
        if allocation_total <= 0:
            LOGGER.warning(
                "SUI emissions series %r has a non-positive total — skipping", label
            )
            continue
        total_pct = (allocation_total / supply_total * Decimal(100)).quantize(
            _PCT_QUANT
        )
        for i in range(1, len(points)):
            delta = points[i][1] - points[i - 1][1]
            if delta <= 0:
                continue
            unlock_date = points[i - 1][0]
            rows.append(
                _UnlockRow(
                    allocation_name=label,
                    unlock_date=unlock_date,
                    allocation_total_tokens=allocation_total.quantize(_TOKEN_QUANT),
                    allocation_total_percent_of_supply=total_pct,
                    is_tge=unlock_date == tge_date,
                    unlock_percent_of_allocation=(
                        delta / allocation_total * Decimal(100)
                    ).quantize(_PCT_QUANT),
                    unlock_tokens=delta.quantize(_TOKEN_QUANT),
                    vesting_type=None,
                )
            )
    return rows


def _row_to_dict(
    row: _UnlockRow,
    *,
    ingest_run_id: int,
    source_endpoint: str,
    fetched_at: datetime,
) -> dict[str, Any]:
    """Convert an _UnlockRow to the dict bulk_upsert expects."""
    return {
        "allocation_name": row.allocation_name,
        "unlock_date": row.unlock_date,
        "allocation_total_tokens": row.allocation_total_tokens,
        "allocation_total_percent_of_supply": row.allocation_total_percent_of_supply,
        "is_tge": row.is_tge,
        "unlock_percent_of_allocation": row.unlock_percent_of_allocation,
        "unlock_tokens": row.unlock_tokens,
        "vesting_type": row.vesting_type,
        "source_endpoint": source_endpoint,
        "fetched_at": fetched_at,
        "ingest_run_id": ingest_run_id,
    }


def collect(*, http: HttpClient | None = None) -> int:
    """Run the SUI unlocks collector once. Returns the meta.ingest_runs id.

    No backfill mode at this layer — the DeFiLlama dataset carries the
    FULL schedule (past + future batches) on every request, so a single
    fetch is the full picture. Re-runs are no-op upserts on the
    ``(allocation_name, unlock_date)`` PK.
    """
    owns_http = http is None
    if http is None:
        http = HttpClient(SOURCE_NAME, rate_limit=DEFAULT_RATE_LIMIT)

    try:
        with db.ingest_run(
            SOURCE_NAME,
            endpoint=COLLECT_ENDPOINT_LABEL,
        ) as run:
            try:
                response = http.get(LLAMA_SUI_EMISSIONS_URL)
                response.raise_for_status()
                payload = response.json()
                fetched_at = datetime.now(timezone.utc)
            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.HTTPStatusError,
                json.JSONDecodeError,
                ValueError,
            ) as exc:
                LOGGER.error("DeFiLlama SUI emissions fetch failed: %s", exc)
                db.record_partial_endpoints(
                    run.id,
                    [
                        {
                            "name": COLLECT_ENDPOINT_LABEL,
                            "url": LLAMA_SUI_EMISSIONS_URL,
                            "error": str(exc),
                        }
                    ],
                )
                raise RuntimeError(
                    f"DeFiLlama SUI emissions fetch failed: {exc}"
                ) from exc

            db.store_raw_blob(
                run.id,
                COLLECT_ENDPOINT_LABEL,
                LLAMA_SUI_EMISSIONS_URL,
                payload,
            )

            try:
                unlock_rows = parse_unlock_rows(payload)
            except ValueError as exc:
                LOGGER.error("DeFiLlama SUI emissions parse failed: %s", exc)
                db.record_partial_endpoints(
                    run.id,
                    [
                        {
                            "name": COLLECT_ENDPOINT_LABEL,
                            "url": LLAMA_SUI_EMISSIONS_URL,
                            "error": str(exc),
                        }
                    ],
                )
                raise RuntimeError(
                    f"DeFiLlama SUI emissions parse failed: {exc}"
                ) from exc

            rows = [
                _row_to_dict(
                    r,
                    ingest_run_id=run.id,
                    source_endpoint=LLAMA_SUI_EMISSIONS_URL,
                    fetched_at=fetched_at,
                )
                for r in unlock_rows
            ]
            with db.connection() as conn:
                written = db.bulk_upsert(
                    conn,
                    "onchain.sui_unlocks",
                    rows,
                    conflict_keys=("allocation_name", "unlock_date"),
                )
            run.add_rows(written)
            covered = sorted({r.allocation_name for r in unlock_rows})
            LOGGER.info(
                "SUI unlocks: +%s rows across %s allocation(s): %s",
                written,
                len(covered),
                covered,
            )
            return run.id
    finally:
        if owns_http:
            http.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI flags for the collector entry point."""
    parser = argparse.ArgumentParser(
        description=(
            "Collect the SUI token unlock schedule (all 8 allocations, via "
            "DeFiLlama's open datasets bucket) into onchain.sui_unlocks."
        )
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the collector from ``python -m genkei.ingest.sui_unlocks``."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parse_args(argv if argv is not None else sys.argv[1:])
    run_id = collect()
    print(f"SUI unlocks collector wrote ingest_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
