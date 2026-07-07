"""Zcash shielded-pool usage collector (ZEC usage ingester).

Closes the load-bearing gap from the 2026-07-06 ZEC research decision: the lake
had ZEC price but no *usage* signal, so the privacy-*adoption* thesis was
unmeasurable. This lands a daily snapshot of the on-chain value held in each
Zcash value pool — transparent vs the shielded pools (sprout / sapling /
orchard) vs the dev-fund lockbox — into ``zcash.shielded_pools``, from which the
headline metric (**shielded share of supply**, and above all its *trend*) is
derived at query time.

**Source.** The Zcash node's ``getblockchaininfo.valuePools`` array, surfaced
free/keyless by ``zcashexplorer.app/api/v1/blockchain-info`` (see
``docs/sources/zcash-usage.md`` for the source survey). Each entry is
``{id, chainValue, monitored}`` where ``chainValue`` is the cumulative ZEC in
that pool.

**Forward-only snapshot.** The endpoint exposes only the *current* chain state,
so the collector lands one snapshot per run dated at the fetch day (UTC) —
structurally identical to the iShares/Bitwise ETF-NAV ingesters (B-107/B-113).
The series builds forward; deep history would require a full Zcash node. The
``(pool, snapshot_date)`` PK makes a same-day re-run an idempotent upsert.

No API key required.
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

SOURCE_NAME = "zcash_usage"
# Single-step ingester (parse inline, write directly to zcash.shielded_pools);
# "collect" is the recurring endpoint label the health check keys on.
COLLECT_ENDPOINT_LABEL = "collect"

BLOCKCHAIN_INFO_URL = "https://mainnet.zcashexplorer.app/api/v1/blockchain-info"

# The privacy pools. Everything else (transparent, lockbox) is not user-private:
# transparent = public t-addresses; lockbox = deferred dev-fund ZEC (NU6).
SHIELDED_POOLS = frozenset({"sprout", "sapling", "orchard"})

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
DEFAULT_RATE_LIMIT = RateLimit.per_second(1)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _PoolSnapshot:
    """One value pool's ZEC balance at a snapshot."""

    pool: str
    snapshot_date: date
    chain_value_zec: Decimal
    shielded: bool
    block_height: int | None


def _coerce_decimal(raw: Any) -> Decimal | None:
    """Pull a non-negative Decimal from a numeric ``chainValue`` field."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        try:
            value = Decimal(str(raw))
        except InvalidOperation:
            return None
    elif isinstance(raw, str):
        cleaned = raw.strip()
        if not cleaned:
            return None
        try:
            value = Decimal(cleaned)
        except InvalidOperation:
            return None
    else:
        return None
    return value if value >= 0 else None


def parse_value_pools(
    payload: Any,
    *,
    snapshot_date: date,
) -> list[_PoolSnapshot]:
    """Decode a ``blockchain-info`` payload into per-pool snapshot rows.

    Reads ``payload.valuePools`` (the node's ``getblockchaininfo`` output) and
    ``payload.blocks`` (best-block height, for provenance). Skips any pool whose
    ``chainValue`` is missing or negative, logging a WARNING, and rejects any
    otherwise usable pool whose ``monitored`` flag is not true. In unattended
    daily ingest a swallowed surprise is the difference between noticing bad
    data and not (B-121).
    """
    if not isinstance(payload, dict):
        raise ValueError(
            f"blockchain-info payload is not a JSON object: {type(payload).__name__}"
        )
    pools = payload.get("valuePools")
    if not isinstance(pools, list) or not pools:
        raise ValueError("blockchain-info payload has no 'valuePools' array")

    block_height = payload.get("blocks")
    block_height = block_height if isinstance(block_height, int) else None

    out: list[_PoolSnapshot] = []
    for entry in pools:
        if not isinstance(entry, dict):
            continue
        pool_id = entry.get("id")
        if not isinstance(pool_id, str) or not pool_id.strip():
            continue
        pool_id = pool_id.strip().lower()
        value = _coerce_decimal(entry.get("chainValue"))
        if value is None:
            LOGGER.warning(
                "zcash_usage: pool %s has missing/negative chainValue %r — skipping",
                pool_id,
                entry.get("chainValue"),
            )
            continue
        if entry.get("monitored") is not True:
            raise ValueError(
                f"blockchain-info valuePool {pool_id!r} is not monitored; "
                "refusing untrusted chainValue"
            )
        out.append(
            _PoolSnapshot(
                pool=pool_id,
                snapshot_date=snapshot_date,
                chain_value_zec=value.quantize(Decimal("0.00000001")),
                shielded=pool_id in SHIELDED_POOLS,
                block_height=block_height,
            )
        )
    return out


def _snapshot_to_row(
    snap: _PoolSnapshot,
    *,
    ingest_run_id: int,
    source_endpoint: str,
    fetched_at: datetime,
) -> dict[str, Any]:
    """Convert a _PoolSnapshot to a bulk_upsert row dict."""
    return {
        "pool": snap.pool,
        "snapshot_date": snap.snapshot_date,
        "chain_value_zec": snap.chain_value_zec,
        "shielded": snap.shielded,
        "block_height": snap.block_height,
        "source_endpoint": source_endpoint,
        "fetched_at": fetched_at,
        "ingest_run_id": ingest_run_id,
    }


def collect(*, http: HttpClient | None = None, snapshot_date: date | None = None) -> int:
    """Run the Zcash shielded-pool collector once. Returns the ingest_runs id.

    ``snapshot_date`` defaults to today (UTC) — the day the snapshot represents.
    The source has no backfill (current chain state only), so there is no
    ``--backfill`` mode; the series builds forward one day at a time.
    """
    if snapshot_date is None:
        snapshot_date = datetime.now(timezone.utc).date()

    owns_http = http is None
    if http is None:
        http = HttpClient(SOURCE_NAME, rate_limit=DEFAULT_RATE_LIMIT, user_agent=_BROWSER_UA)

    try:
        with db.ingest_run(
            SOURCE_NAME,
            endpoint=COLLECT_ENDPOINT_LABEL,
            metadata={"snapshot_date": snapshot_date.isoformat()},
        ) as run:
            try:
                payload = http.get_json(BLOCKCHAIN_INFO_URL)
                fetched_at = datetime.now(timezone.utc)
            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.HTTPStatusError,
                json.JSONDecodeError,
            ) as exc:
                LOGGER.error("zcash_usage blockchain-info fetch failed: %s", exc)
                db.record_partial_endpoints(
                    run.id,
                    [
                        {
                            "name": COLLECT_ENDPOINT_LABEL,
                            "url": BLOCKCHAIN_INFO_URL,
                            "error": str(exc),
                        }
                    ],
                )
                raise RuntimeError(f"zcash_usage fetch failed: {exc}") from exc

            db.store_raw_blob(run.id, COLLECT_ENDPOINT_LABEL, BLOCKCHAIN_INFO_URL, payload)
            try:
                snapshots = parse_value_pools(payload, snapshot_date=snapshot_date)
            except ValueError as exc:
                LOGGER.error("zcash_usage blockchain-info parse failed: %s", exc)
                db.record_partial_endpoints(
                    run.id,
                    [
                        {
                            "name": COLLECT_ENDPOINT_LABEL,
                            "url": BLOCKCHAIN_INFO_URL,
                            "error": str(exc),
                        }
                    ],
                )
                raise RuntimeError(f"zcash_usage parse failed: {exc}") from exc

            if not snapshots:
                error = "blockchain-info payload produced no usable valuePools"
                LOGGER.error("zcash_usage: %s", error)
                db.record_partial_endpoints(
                    run.id,
                    [
                        {
                            "name": COLLECT_ENDPOINT_LABEL,
                            "url": BLOCKCHAIN_INFO_URL,
                            "error": error,
                        }
                    ],
                )
                raise RuntimeError(f"zcash_usage parse failed: {error}")

            rows = [
                _snapshot_to_row(
                    snap,
                    ingest_run_id=run.id,
                    source_endpoint=BLOCKCHAIN_INFO_URL,
                    fetched_at=fetched_at,
                )
                for snap in snapshots
            ]
            with db.connection() as conn:
                written = db.bulk_upsert(
                    conn,
                    "zcash.shielded_pools",
                    rows,
                    conflict_keys=("pool", "snapshot_date"),
                )
            run.add_rows(written)
            shielded = sum(s.chain_value_zec for s in snapshots if s.shielded)
            total = sum(s.chain_value_zec for s in snapshots)
            share = (shielded / total * 100) if total else Decimal("0")
            LOGGER.info(
                "zcash_usage: +%s rows (%s pools, shielded share %.1f%% on %s)",
                written,
                len(snapshots),
                share,
                snapshot_date,
            )
            return run.id
    finally:
        if owns_http:
            http.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI flags for the collector entry point."""
    parser = argparse.ArgumentParser(
        description="Collect Zcash shielded-pool snapshots into zcash.shielded_pools."
    )
    parser.add_argument(
        "--snapshot-date",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help="Snapshot date (YYYY-MM-DD). Default = today (UTC).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the collector from ``python -m genkei.ingest.zcash_usage``."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv if argv is not None else sys.argv[1:])
    run_id = collect(snapshot_date=args.snapshot_date)
    print(f"Zcash usage collector wrote ingest_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
