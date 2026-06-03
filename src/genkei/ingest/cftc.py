"""CFTC Commitments of Traders collector (B-031).

Fetches per-market weekly position breakdowns from the CFTC public
Socrata API and lands one row per ``(report_date, market_code,
trader_category)`` in ``cftc.cot_reports``. v1 covers the curated
market list in ``config/watchlists.yml`` under ``cot_markets:`` —
BTC, ETH, ES (Traders in Financial Futures format) and GC, CL
(Disaggregated format). Adding a new market is a watchlist edit, no
code change.

The CFTC publishes three overlapping report formats; we hit two:

  * **TFF** (Traders in Financial Futures) — financial futures
    (BTC, ETH, ES, NQ, FX, rates). Dataset ``gpe5-46if``. Has the
    Asset Manager / Leveraged Funds breakdown that drives the
    crypto-core position-sizing read.
  * **Disaggregated** — commodities (gold, oil, ag, livestock).
    Dataset ``72hh-3qpy``. Has the Managed Money / Swap Dealer /
    Producer Merchant breakdown.

Both publish weekly: Tuesday is the snapshot date, Friday 3:30pm ET is
the publication time. Downstream signal experiments that use COT data
must lag-shift the report_date forward to Friday (the earliest
knowable-by date) to avoid lookahead — same pattern as the 13F
45-day shift in B-101's stack-outcome backtest.

Modes:
  - **incremental** (default) — per market, fetch reports newer than
    the latest ``report_date`` already in ``cftc.cot_reports``. Idempotent
    via the ``(report_date, market_code, trader_category)`` PK.
  - **backfill** (``--backfill``) — pull every report for every market.
    CFTC history goes back to 2006 for the TFF format and to 1995 for
    Legacy; per-market history is a few thousand rows, single HTTP
    call per market.

No API key required. The Socrata throttle on app-token-less requests is
generous enough that a 5-market sweep completes in well under a
minute.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from genkei.common import db
from genkei.common.http import HttpClient, RateLimit
from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    CotMarketEntry,
    load_watchlist,
)

SOURCE_NAME = "cftc"
COLLECT_ENDPOINT_LABEL = "collect"

CFTC_BASE_URL = "https://publicreporting.cftc.gov/resource"
TFF_DATASET_ID = "gpe5-46if"
DISAGGREGATED_DATASET_ID = "72hh-3qpy"

# Socrata's default $limit is 1000. A per-market COT history (weekly,
# ~30 years) tops out around 1600 rows, so 50_000 is a comfortable
# single-call ceiling that avoids pagination logic for v1. If a future
# market needs more (e.g. multiple subcontracts), bump this or add
# offset-paging.
SOCRATA_LIMIT = 50_000

# Free public Socrata endpoints. Without an app token throttling kicks in
# above ~1 req/s but we make 5-10 requests per run so 1/s is fine.
DEFAULT_RATE_LIMIT = RateLimit.per_second(1)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _CategoryFields:
    """Maps a Socrata column triple (long / short / spread) → trader_category."""

    long_field: str
    short_field: str
    # Socrata column for spreading positions, or None if the category has
    # no spreading concept (CFTC convention: non-reportable / producer-
    # merchant don't spread).
    spread_field: str | None
    trader_category: str


# Column → category mapping for each report type. Sourced from the CFTC
# Socrata column metadata; verified against a real BTC TFF response and
# a real WTI Disaggregated response during initial backfill design.
TFF_CATEGORIES: tuple[_CategoryFields, ...] = (
    _CategoryFields(
        long_field="dealer_positions_long_all",
        short_field="dealer_positions_short_all",
        spread_field="dealer_positions_spread_all",
        trader_category="dealer_intermediary",
    ),
    _CategoryFields(
        long_field="asset_mgr_positions_long",
        short_field="asset_mgr_positions_short",
        spread_field="asset_mgr_positions_spread",
        trader_category="asset_manager",
    ),
    _CategoryFields(
        long_field="lev_money_positions_long",
        short_field="lev_money_positions_short",
        spread_field="lev_money_positions_spread",
        trader_category="leveraged_funds",
    ),
    _CategoryFields(
        long_field="other_rept_positions_long",
        short_field="other_rept_positions_short",
        spread_field="other_rept_positions_spread",
        trader_category="other_reportables",
    ),
    _CategoryFields(
        long_field="nonrept_positions_long_all",
        short_field="nonrept_positions_short_all",
        spread_field=None,
        trader_category="non_reportable",
    ),
)

DISAGGREGATED_CATEGORIES: tuple[_CategoryFields, ...] = (
    _CategoryFields(
        long_field="prod_merc_positions_long",
        short_field="prod_merc_positions_short",
        spread_field=None,
        trader_category="producer_merchant",
    ),
    _CategoryFields(
        long_field="swap_positions_long_all",
        # Double underscore in these upstream column names is intentional:
        # the CFTC Socrata schema publishes "swap__positions_short_all" and
        # "swap__positions_spread_all".
        short_field="swap__positions_short_all",
        spread_field="swap__positions_spread_all",
        trader_category="swap_dealer",
    ),
    _CategoryFields(
        long_field="m_money_positions_long_all",
        short_field="m_money_positions_short_all",
        spread_field="m_money_positions_spread",
        trader_category="managed_money",
    ),
    _CategoryFields(
        long_field="other_rept_positions_long",
        short_field="other_rept_positions_short",
        spread_field="other_rept_positions_spread",
        trader_category="other_reportables",
    ),
    _CategoryFields(
        long_field="nonrept_positions_long_all",
        short_field="nonrept_positions_short_all",
        spread_field=None,
        trader_category="non_reportable",
    ),
)


def _dataset_id_for(report_type: str) -> str:
    """Return the Socrata dataset id for a supported CFTC report type."""
    if report_type == "tff":
        return TFF_DATASET_ID
    if report_type == "disaggregated":
        return DISAGGREGATED_DATASET_ID
    raise ValueError(f"Unsupported CFTC report_type: {report_type!r}")


def _categories_for(report_type: str) -> tuple[_CategoryFields, ...]:
    """Return the category column mapping for a supported CFTC report type."""
    if report_type == "tff":
        return TFF_CATEGORIES
    if report_type == "disaggregated":
        return DISAGGREGATED_CATEGORIES
    raise ValueError(f"Unsupported CFTC report_type: {report_type!r}")


def build_market_url(market: CotMarketEntry, *, since: date | None) -> str:
    """Construct the Socrata GET URL for one market's history.

    Socrata's ``$where`` filter takes ISO-8601 date strings without quoting
    when the column type is a floating timestamp; ``report_date_as_yyyy_mm_dd``
    is one such column. ``$limit`` is set well above any one market's
    realistic row count to avoid pagination.
    """
    dataset_id = _dataset_id_for(market.report_type)
    where_clauses: list[str] = [f"cftc_contract_market_code='{market.code}'"]
    if since is not None:
        where_clauses.append(
            f"report_date_as_yyyy_mm_dd >= '{since.isoformat()}T00:00:00'"
        )
    params = {
        "$where": " AND ".join(where_clauses),
        "$order": "report_date_as_yyyy_mm_dd ASC",
        "$limit": str(SOCRATA_LIMIT),
    }
    return f"{CFTC_BASE_URL}/{dataset_id}.json?{urlencode(params, safe=' ')}"


def _coerce_int(raw: Any) -> int | None:
    """Socrata serializes numerics as strings; parse to int or None."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            try:
                return int(float(stripped))
            except ValueError:
                return None
    if isinstance(raw, float):
        return int(raw)
    return None


def _parse_report_date(raw: Any) -> date | None:
    """Socrata floating-timestamp column → date."""
    if not isinstance(raw, str) or not raw:
        return None
    # Format observed in the wild: "2024-01-09T00:00:00.000"
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        # Fallback: bare YYYY-MM-DD
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None


def parse_market_rows(
    payload: Any,
    *,
    market: CotMarketEntry,
    ingest_run_id: int,
    source_endpoint: str,
    fetched_at: datetime,
) -> list[dict[str, Any]]:
    """Decode a Socrata JSON payload into ``cftc.cot_reports`` rows.

    Each upstream row (one weekly report) fans out into 5 trader-
    category rows. Skips upstream rows that have no usable
    ``report_date_as_yyyy_mm_dd``; everything else lands, with NULLs
    where the category has no spread column.
    """
    if not isinstance(payload, list):
        raise ValueError(
            f"CFTC payload for market {market.code} is not a JSON array: {type(payload).__name__}"
        )
    categories = _categories_for(market.report_type)
    rows: list[dict[str, Any]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        report_date = _parse_report_date(entry.get("report_date_as_yyyy_mm_dd"))
        if report_date is None:
            continue
        # Prefer the actual upstream market name if present (it's the
        # canonical CFTC-published label), fall back to watchlist name.
        market_name = entry.get("market_and_exchange_names") or market.name
        for cat in categories:
            long_pos = _coerce_int(entry.get(cat.long_field))
            short_pos = _coerce_int(entry.get(cat.short_field))
            spread_pos: int | None = None
            if cat.spread_field is not None:
                spread_pos = _coerce_int(entry.get(cat.spread_field))
            # Skip the row entirely when long+short are both missing.
            # Spreading-only rows aren't meaningful and would silently
            # land as long=NULL, short=NULL, which queries reading
            # ``COALESCE(long, 0) - COALESCE(short, 0)`` would treat as
            # zero net position — wrong signal.
            if long_pos is None and short_pos is None:
                continue
            rows.append(
                {
                    "report_date": report_date,
                    "market_code": market.code,
                    "market_name": str(market_name),
                    "report_type": market.report_type,
                    "trader_category": cat.trader_category,
                    "long_positions": long_pos,
                    "short_positions": short_pos,
                    "spreading_positions": spread_pos,
                    "source_endpoint": source_endpoint,
                    "fetched_at": fetched_at,
                    "ingest_run_id": ingest_run_id,
                }
            )
    return rows


def latest_report_date_for_market(market_code: str) -> date | None:
    """Return the most-recent ``report_date`` already ingested for this market."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT max(report_date) FROM cftc.cot_reports WHERE market_code = %s",
            [market_code],
        )
        row = cur.fetchone()
    if row is None or row[0] is None:
        return None
    return row[0] if isinstance(row[0], date) else None


def collect(
    config_path: Path = DEFAULT_WATCHLIST_PATH,
    *,
    http: HttpClient | None = None,
    backfill: bool = False,
    since: date | None = None,
) -> int:
    """Run the CFTC collector once. Returns the meta.ingest_runs id.

    ``backfill=True`` forces a full per-market history pull (ignores
    ``since`` and the latest-ingested date). ``since`` (date or None)
    lets the caller override the incremental cutoff for replays /
    targeted backfills without going full-history.
    """
    watchlist = load_watchlist(config_path)
    markets = list(watchlist.cot_markets)
    if not markets:
        raise SystemExit(
            "watchlists.yml is missing cot_markets: or it is empty — nothing to fetch."
        )

    owns_http = http is None
    if http is None:
        http = HttpClient(SOURCE_NAME, rate_limit=DEFAULT_RATE_LIMIT)

    failures: list[dict[str, str]] = []
    try:
        with db.ingest_run(
            SOURCE_NAME,
            endpoint=COLLECT_ENDPOINT_LABEL,
            metadata={
                "mode": "backfill" if backfill else "incremental",
                "market_count": len(markets),
                "since": since.isoformat() if since else None,
            },
        ) as run:
            total_written = 0
            for market in markets:
                if backfill:
                    since_for_market: date | None = None
                elif since is not None:
                    since_for_market = since
                else:
                    latest = latest_report_date_for_market(market.code)
                    # Re-fetch the latest week itself in case it was a
                    # preliminary publication that got revised — the upsert
                    # is idempotent so a duplicate write is harmless.
                    since_for_market = latest if latest else None
                url = build_market_url(market, since=since_for_market)
                endpoint_name = f"cot_{market.report_type}_{market.code}"
                try:
                    payload = http.get_json(url)
                    fetched_at = datetime.now(timezone.utc)
                except (
                    httpx.TimeoutException,
                    httpx.NetworkError,
                    httpx.HTTPStatusError,
                    json.JSONDecodeError,
                ) as exc:
                    LOGGER.warning(
                        "CFTC fetch failed for %s (%s): %s",
                        market.symbol,
                        market.code,
                        exc,
                    )
                    failures.append(
                        {"name": endpoint_name, "url": url, "error": str(exc)}
                    )
                    continue
                db.store_raw_blob(run.id, endpoint_name, url, payload)
                rows = parse_market_rows(
                    payload,
                    market=market,
                    ingest_run_id=run.id,
                    source_endpoint=url,
                    fetched_at=fetched_at,
                )
                if not rows:
                    LOGGER.info(
                        "CFTC %s (%s): no new reports", market.symbol, market.code
                    )
                    continue
                with db.connection() as conn:
                    written = db.bulk_upsert(
                        conn,
                        "cftc.cot_reports",
                        rows,
                        conflict_keys=("report_date", "market_code", "trader_category"),
                    )
                total_written += written
                LOGGER.info(
                    "CFTC %s (%s): +%s rows (%s upstream weeks)",
                    market.symbol,
                    market.code,
                    written,
                    len(rows) // len(_categories_for(market.report_type)),
                )
            run.add_rows(total_written)
            if failures:
                db.record_partial_endpoints(run.id, failures)
                raise RuntimeError(
                    f"CFTC fetch failed for {len(failures)} market(s); see partial_endpoints."
                )
            return run.id
    finally:
        if owns_http:
            http.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI flags for the collector entry point."""
    parser = argparse.ArgumentParser(
        description="Collect CFTC Commitments of Traders positions into cftc.cot_reports."
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Pull every market's full history. One-shot; default is incremental.",
    )
    parser.add_argument(
        "--since",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help="ISO date (YYYY-MM-DD); fetch reports from this date forward. "
        "Ignored when --backfill is set.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_WATCHLIST_PATH,
        help="Watchlist path.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the collector from ``python -m genkei.ingest.cftc``."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv if argv is not None else sys.argv[1:])
    run_id = collect(args.config, backfill=args.backfill, since=args.since)
    print(f"CFTC collector wrote ingest_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
