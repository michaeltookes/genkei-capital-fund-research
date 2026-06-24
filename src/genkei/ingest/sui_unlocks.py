"""SUI token unlock / vesting schedule collector (B-089).

Scrapes the SUI vesting schedule from CryptoRank's public vesting page
(``https://cryptorank.io/price/sui/vesting``) by extracting the Next.js
SSR ``__NEXT_DATA__`` JSON blob embedded in the HTML. Lands one row per
``(allocation_name, unlock_date)`` in ``onchain.sui_unlocks``.

**v1 covers ONE of SUI's 8 allocation categories** — Community Reserves
(10.648% of supply, 85 monthly batches from 2023-05-03 through
2030-05-01). The remaining 7 categories (Allocated After 2030, Mysten
Labs Treasury, Series A, Series B, Early Contributors, Community Access
Program, Stake Subsidies) are paywalled across the surveyed free
sources. See ``docs/sources/sui-unlocks.md`` for the Phase 1
investigation and the specific paywall mechanism for each source.

**Caller-side analytics must NOT treat the resulting table as a complete
SUI unlock picture.** The most signal-rich VC categories (Series A/B,
Early Contributors — totaling ~20% of supply) are absent and remain a
paid-data gap.

The collector is structured to extend cleanly when additional categories
become available: ``parse_allocations`` filters the upstream payload to
``KNOWN_FREE_ALLOCATIONS`` (currently just Community Reserves) — adding
a category to that tuple is the entire change needed once data exists.

CryptoRank publishes the schedule as static data (no daily-cron urgency
to refetch); the vesting schedule for already-shipped batches is
effectively immutable once announced. The collector still runs daily so
that any forward-looking schedule revisions are picked up promptly.
Idempotent on the ``(allocation_name, unlock_date)`` PK; the upsert
overwrites prior rows in case a forward batch's ``unlock_percent`` is
revised upstream.

No API key required. Standard HTTP GET against the public HTML.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
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

CRYPTORANK_SUI_VESTING_URL = "https://cryptorank.io/price/sui/vesting"

# Tuple of allocation names we ingest from CryptoRank's free public SSR data.
# Phase 1 verified that only "Community Reserves" has its batch schedule
# exposed un-gated. The other 7 allocations are paywalled. Adding a name to
# this tuple is the full code change needed if/when paid-data becomes
# available or the upstream gating loosens — the parse path already supports
# any allocation that has a populated ``batches`` array.
KNOWN_FREE_ALLOCATIONS: tuple[str, ...] = ("Community Reserves",)

DEFAULT_RATE_LIMIT = RateLimit.per_second(1)

# Use a browser-like User-Agent — CryptoRank's edge has historically returned
# variable responses to non-browser UAs; the SSR payload is consistent under
# a standard browser UA.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/16.0 Safari/605.1.15"
)

_NEXT_DATA_PATTERN = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)

LOGGER = logging.getLogger(__name__)


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


def extract_next_data(html: str) -> dict[str, Any]:
    """Pull the ``__NEXT_DATA__`` JSON blob out of a Next.js SSR HTML page.

    Raises ``ValueError`` if the blob is missing or unparseable. CryptoRank
    is a Next.js Pages-Router app; the blob shape is stable across renders
    and contains all server-rendered page data including the un-gated
    vesting schedule.
    """
    m = _NEXT_DATA_PATTERN.search(html)
    if not m:
        raise ValueError(
            "CryptoRank vesting page is missing the __NEXT_DATA__ script — "
            "the upstream HTML structure may have changed."
        )
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"CryptoRank __NEXT_DATA__ payload failed to parse as JSON: {exc}"
        ) from exc


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


def _parse_iso_date(raw: Any) -> date | None:
    """Parse a CryptoRank ISO-8601 timestamp (e.g. ``"2023-05-03T00:00:00.000Z"``)
    into a Python ``date``. CryptoRank stamps every batch at midnight UTC so
    truncating to the date is correct."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        # Fallback: bare YYYY-MM-DD
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None


def parse_allocations(
    next_data: dict[str, Any],
    *,
    allowed_allocations: tuple[str, ...] = KNOWN_FREE_ALLOCATIONS,
) -> list[_UnlockRow]:
    """Decode the CryptoRank __NEXT_DATA__ payload into unlock rows.

    Walks ``next_data.props.pageProps.vestingInfo.allocations`` and lifts
    every batch from allocations whose ``name`` matches ``allowed_allocations``
    (after whitespace stripping — CryptoRank's "Community Reserves " has a
    trailing space upstream that we strip for the schema).

    Drops batches with no parseable ``date`` or ``unlock_percent``.
    """
    try:
        allocations = (
            next_data["props"]["pageProps"]["vestingInfo"]["allocations"]
        )
    except (KeyError, TypeError) as exc:
        raise ValueError(
            f"CryptoRank __NEXT_DATA__ missing "
            f"props.pageProps.vestingInfo.allocations: {exc}"
        ) from exc

    if not isinstance(allocations, list):
        raise ValueError(
            f"vestingInfo.allocations is not a list: {type(allocations).__name__}"
        )

    allowed_lower = {n.strip().lower() for n in allowed_allocations}
    rows: list[_UnlockRow] = []
    for alloc in allocations:
        if not isinstance(alloc, dict):
            continue
        raw_name = alloc.get("name")
        if not isinstance(raw_name, str):
            continue
        name = raw_name.strip()
        if name.lower() not in allowed_lower:
            continue

        total_tokens = _coerce_decimal(alloc.get("tokens"))
        total_pct = _coerce_decimal(alloc.get("tokens_percent"))
        vesting_type_raw = alloc.get("unlock_type")
        vesting_type = (
            vesting_type_raw.strip().lower()
            if isinstance(vesting_type_raw, str) and vesting_type_raw.strip()
            else None
        )
        if total_tokens is None or total_pct is None:
            LOGGER.warning(
                "SUI unlock allocation %r missing tokens / tokens_percent — skipping",
                name,
            )
            continue

        batches = alloc.get("batches")
        if not isinstance(batches, list):
            LOGGER.warning(
                "SUI unlock allocation %r has no batches list — skipping", name
            )
            continue

        rows_by_date: dict[date, _UnlockRow] = {}
        for batch in batches:
            if not isinstance(batch, dict):
                continue
            unlock_date = _parse_iso_date(batch.get("date"))
            unlock_pct = _coerce_decimal(batch.get("unlock_percent"))
            if unlock_date is None or unlock_pct is None:
                continue

            # unlock_percent is % of THIS allocation, not % of total supply.
            # Derive absolute SUI: allocation_total * pct / 100. Quantize to
            # 4 decimals to match the NUMERIC(20,4) column.
            unlock_tokens = (total_tokens * unlock_pct / Decimal(100)).quantize(
                Decimal("0.0001")
            )
            is_tge_raw = batch.get("is_tge")
            is_tge = bool(is_tge_raw) if isinstance(is_tge_raw, bool) else False

            candidate = _UnlockRow(
                allocation_name=name,
                unlock_date=unlock_date,
                allocation_total_tokens=total_tokens,
                allocation_total_percent_of_supply=total_pct,
                is_tge=is_tge,
                unlock_percent_of_allocation=unlock_pct,
                unlock_tokens=unlock_tokens,
                vesting_type=vesting_type,
            )
            # Collapse same-day rows within the same allocation. CryptoRank can
            # publish a zero placeholder before the real row, or a monthly row
            # plus a special-event row on the same date. The table key cannot
            # represent both, so preserve zero-placeholders only until a real
            # row arrives and aggregate multiple real same-day batches.
            existing = rows_by_date.get(unlock_date)
            if existing is None:
                rows_by_date[unlock_date] = candidate
                continue

            aggregate_pct = existing.unlock_percent_of_allocation
            if unlock_pct > Decimal("0"):
                if aggregate_pct == Decimal("0"):
                    aggregate_pct = unlock_pct
                else:
                    aggregate_pct += unlock_pct

            rows_by_date[unlock_date] = _UnlockRow(
                allocation_name=existing.allocation_name,
                unlock_date=existing.unlock_date,
                allocation_total_tokens=existing.allocation_total_tokens,
                allocation_total_percent_of_supply=existing.allocation_total_percent_of_supply,
                is_tge=existing.is_tge or candidate.is_tge,
                unlock_percent_of_allocation=aggregate_pct,
                unlock_tokens=(total_tokens * aggregate_pct / Decimal(100)).quantize(
                    Decimal("0.0001")
                ),
                vesting_type=existing.vesting_type,
            )
        rows.extend(rows_by_date.values())
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

    No backfill mode at this layer — the CryptoRank page renders the FULL
    schedule (past + future batches) on every request, so a single fetch
    is the full picture. Re-runs are no-op upserts on the
    ``(allocation_name, unlock_date)`` PK.
    """
    owns_http = http is None
    if http is None:
        http = HttpClient(
            SOURCE_NAME,
            user_agent=USER_AGENT,
            rate_limit=DEFAULT_RATE_LIMIT,
        )

    try:
        with db.ingest_run(
            SOURCE_NAME,
            endpoint=COLLECT_ENDPOINT_LABEL,
            metadata={"known_free_allocations": list(KNOWN_FREE_ALLOCATIONS)},
        ) as run:
            try:
                response = http.get(CRYPTORANK_SUI_VESTING_URL)
                response.raise_for_status()
                html = response.text
                next_data = extract_next_data(html)
                fetched_at = datetime.now(timezone.utc)
            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.HTTPStatusError,
                ValueError,
            ) as exc:
                LOGGER.error("CryptoRank SUI vesting fetch failed: %s", exc)
                db.record_partial_endpoints(
                    run.id,
                    [
                        {
                            "name": COLLECT_ENDPOINT_LABEL,
                            "url": CRYPTORANK_SUI_VESTING_URL,
                            "error": str(exc),
                        }
                    ],
                )
                raise RuntimeError(
                    f"CryptoRank SUI vesting fetch failed: {exc}"
                ) from exc

            db.store_raw_blob(
                run.id,
                COLLECT_ENDPOINT_LABEL,
                CRYPTORANK_SUI_VESTING_URL,
                next_data,
            )

            unlock_rows = parse_allocations(next_data)
            if not unlock_rows:
                error = (
                    "SUI unlocks collector parsed 0 rows; upstream may have "
                    "changed the allocation names or gated Community Reserves."
                )
                LOGGER.error(error)
                db.record_partial_endpoints(
                    run.id,
                    [
                        {
                            "name": COLLECT_ENDPOINT_LABEL,
                            "url": CRYPTORANK_SUI_VESTING_URL,
                            "error": error,
                        }
                    ],
                )
                raise RuntimeError(error)

            rows = [
                _row_to_dict(
                    r,
                    ingest_run_id=run.id,
                    source_endpoint=CRYPTORANK_SUI_VESTING_URL,
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
            "Collect SUI token unlock schedule into onchain.sui_unlocks. v1 covers "
            "Community Reserves only — see docs/sources/sui-unlocks.md."
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
