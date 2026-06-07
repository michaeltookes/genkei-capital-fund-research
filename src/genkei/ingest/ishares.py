"""iShares spot crypto ETF daily snapshot collector (B-107).

Fetches the public iShares product-screener JSON feed and lands one row per
``(ticker, snapshot_date)`` in ``etf.fund_snapshots`` for each watchlist
``etf_tickers`` entry where ``issuer == "BlackRock"``. v1 covers IBIT, ETHA,
ETHB — see ``docs/sources/spot-etf-net-flow.md`` for the Phase 1 investigation
that landed on this source.

The feed publishes daily NAV (close-equivalent for an ETF) and total net
assets (AUM) at T+1 cadence — both fields stamped with ``navAmountAsOf`` /
``totalNetAssetsFundAsOf`` dates. ``shares_outstanding`` is derived as
``total_net_assets / nav_per_share`` at write time so callers don't pay a
divide-on-read tax. Daily net flow is NOT stored — it's computed at query
time via ``(shares - LAG(shares OVER ticker ORDER BY date)) * nav`` so the
table stays a pure raw-snapshot table with no recomputation race when a
backfill lands out of order.

Modes:
  - **incremental** (default) — fetch the current feed snapshot. The feed
    returns whatever date the iShares administrator most recently published
    (typically yesterday's close, available the next morning). The collector
    is idempotent via the ``(ticker, snapshot_date)`` PK; re-running on the
    same day is a no-op upsert.
  - **backfill** — not supported by the feed. The product-screener JSON
    returns a single point-in-time snapshot per fund; iShares' historical
    NAV history lives on a separate (and unfound) endpoint. Backfill is a
    v2.1 follow-up (SEC 10-Q quarterly checkpoints triangulated against
    forward-going snapshots).

No API key required. The feed is unauthenticated, no rate limit observed.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from genkei.common import db
from genkei.common.http import HttpClient, RateLimit
from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    EtfTickerEntry,
    load_watchlist,
)

SOURCE_NAME = "ishares"
# "collect" matches the convention pinned by test_every_source_expects_at_
# least_a_collect_endpoint in tests/cli/test_watchlist_cmd.py. The label is
# generic by design: a v2.1 ingester adding a second iShares endpoint
# (e.g. a per-product NAV-history download) would land alongside this one
# as "ishares.<other>" and not collide.
COLLECT_ENDPOINT_LABEL = "collect"
ISSUER_FILTER = "BlackRock"

# Public iShares product-screener endpoint. Returns a JSON object keyed by
# portfolioId covering all ~530 US iShares ETFs in one ~1.9 MB request.
PRODUCT_SCREENER_URL = (
    "https://www.ishares.com/us/product-screener/product-screener-v3.1.jsn"
    "?dcrPath=/templatedata/config/product-screener-v3/data/en/us-ishares/"
    "ishares-product-screener-backend-config"
    "&siteEntryPassthrough=true&loc=en_us"
)

# Generous default; the feed has no observed rate limit but one req/s is a
# polite ceiling for a daily-cron use case.
DEFAULT_RATE_LIMIT = RateLimit.per_second(1)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _FundSnapshot:
    """Normalized snapshot extracted from one iShares product-screener entry."""

    ticker: str
    snapshot_date: date
    issuer: str
    asset: str
    cusip: str | None
    isin: str | None
    nav_per_share_usd: Decimal
    total_net_assets_usd: Decimal
    shares_outstanding: Decimal


def _coerce_decimal(raw: Any) -> Decimal | None:
    """Pull a Decimal from an iShares ``{d: "...", r: ...}`` numeric field.

    The feed publishes most numerics as ``{"d": "33.81", "r": 33.805916}``
    where ``r`` is the unrounded source-precision number and ``d`` is the
    display-formatted string. We prefer ``r`` since it preserves more
    significant digits, falling back to ``d`` parsed without commas.
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        r = raw.get("r")
        if r is not None:
            try:
                return Decimal(str(r))
            except Exception:
                pass
        d = raw.get("d")
        if isinstance(d, str):
            cleaned = d.replace(",", "").strip()
            if cleaned and cleaned != "-":
                try:
                    return Decimal(cleaned)
                except Exception:
                    return None
        return None
    if isinstance(raw, (int, float)):
        return Decimal(str(raw))
    if isinstance(raw, str):
        cleaned = raw.replace(",", "").strip()
        if not cleaned or cleaned == "-":
            return None
        try:
            return Decimal(cleaned)
        except Exception:
            return None
    return None


def _coerce_string(raw: Any) -> str | None:
    """Pull a usable string from the iShares feed; ``"-"`` and empty → None."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        d = raw.get("d")
        if isinstance(d, str) and d.strip() and d.strip() != "-":
            return d.strip()
        return None
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped or stripped == "-":
            return None
        return stripped
    return None


def _parse_as_of_date(raw: Any) -> date | None:
    """Parse an iShares ``{d: "Jun 05, 2026", r: 20260605}`` as-of field.

    Prefers the ``r`` integer form (YYYYMMDD) because it's locale-free; falls
    back to parsing the display string with ``%b %d, %Y`` for resilience.
    """
    if not isinstance(raw, dict):
        return None
    r = raw.get("r")
    if isinstance(r, int) and 19000101 <= r <= 99991231:
        s = str(r)
        try:
            return date(int(s[0:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            pass
    d = raw.get("d")
    if isinstance(d, str):
        try:
            return datetime.strptime(d.strip(), "%b %d, %Y").date()
        except ValueError:
            return None
    return None


def parse_snapshots(
    payload: Any,
    *,
    watchlist_etfs: list[EtfTickerEntry],
) -> list[_FundSnapshot]:
    """Decode the iShares product-screener payload into snapshot rows.

    Filters to ``watchlist_etfs`` entries (already pre-filtered to
    ``issuer == "BlackRock"`` by the caller). Drops any feed entry where NAV
    or TNA is missing or non-positive — both are required to derive
    shares_outstanding, and silently writing rows with one zeroed-out would
    mask data-quality issues.

    Critically: ``navAmountAsOf`` and ``totalNetAssetsFundAsOf`` must match.
    When they differ (rare; observed once around prior-month roll-over), the
    row is skipped until iShares republishes aligned fields. Combining a NAV
    from one date with TNA from another would corrupt the ``(ticker,
    snapshot_date)`` natural key and the next net-flow calculation.
    """
    if not isinstance(payload, dict):
        raise ValueError(
            f"iShares product-screener payload is not a JSON object: "
            f"{type(payload).__name__}"
        )
    by_ticker: dict[str, EtfTickerEntry] = {e.ticker.upper(): e for e in watchlist_etfs}
    snapshots: list[_FundSnapshot] = []
    for _key, entry in payload.items():
        if not isinstance(entry, dict):
            continue
        ticker = _coerce_string(entry.get("localExchangeTicker"))
        if not ticker or ticker.upper() not in by_ticker:
            continue
        watchlist_entry = by_ticker[ticker.upper()]

        nav = _coerce_decimal(entry.get("navAmount"))
        tna = _coerce_decimal(entry.get("totalNetAssets"))
        nav_as_of = _parse_as_of_date(entry.get("navAmountAsOf"))
        tna_as_of = _parse_as_of_date(entry.get("totalNetAssetsFundAsOf"))

        if nav is None or tna is None or nav_as_of is None or tna_as_of is None:
            LOGGER.warning(
                "iShares %s: missing required snapshot fields "
                "(nav=%s tna=%s nav_as_of=%s tna_as_of=%s) — skipping",
                ticker,
                nav,
                tna,
                nav_as_of,
                tna_as_of,
            )
            continue
        if nav <= 0:
            LOGGER.warning(
                "iShares %s: non-positive NAV %s on %s — skipping",
                ticker,
                nav,
                nav_as_of,
            )
            continue
        if tna <= 0:
            LOGGER.warning(
                "iShares %s: non-positive TNA %s on %s — skipping",
                ticker,
                tna,
                tna_as_of,
            )
            continue
        if nav_as_of != tna_as_of:
            LOGGER.warning(
                "iShares %s: NAV as-of %s and TNA as-of %s differ — skipping",
                ticker,
                nav_as_of,
                tna_as_of,
            )
            continue

        snapshot_date = nav_as_of
        # Derive shares_outstanding as TNA / NAV. Quantize to 4 decimals to
        # match the schema; the divide can pick up fractional drift because
        # TNA is rounded to dollars upstream.
        shares = (tna / nav).quantize(Decimal("0.0001"))

        snapshots.append(
            _FundSnapshot(
                ticker=ticker.upper(),
                snapshot_date=snapshot_date,
                issuer=watchlist_entry.issuer,
                asset=watchlist_entry.asset.upper(),
                cusip=_coerce_string(entry.get("cusip")),
                isin=_coerce_string(entry.get("isin")),
                nav_per_share_usd=nav,
                total_net_assets_usd=tna,
                shares_outstanding=shares,
            )
        )
    return snapshots


def _snapshot_to_row(
    snap: _FundSnapshot,
    *,
    ingest_run_id: int,
    source_endpoint: str,
    fetched_at: datetime,
) -> dict[str, Any]:
    """Convert a _FundSnapshot to a bulk_upsert row dict."""
    return {
        "ticker": snap.ticker,
        "snapshot_date": snap.snapshot_date,
        "issuer": snap.issuer,
        "asset": snap.asset,
        "cusip": snap.cusip,
        "isin": snap.isin,
        "nav_per_share_usd": snap.nav_per_share_usd,
        "total_net_assets_usd": snap.total_net_assets_usd,
        "shares_outstanding": snap.shares_outstanding,
        "source_endpoint": source_endpoint,
        "fetched_at": fetched_at,
        "ingest_run_id": ingest_run_id,
    }


def collect(
    config_path: Path = DEFAULT_WATCHLIST_PATH,
    *,
    http: HttpClient | None = None,
) -> int:
    """Run the iShares collector once. Returns the meta.ingest_runs id.

    The product-screener feed only publishes the current snapshot — there is
    no ``backfill`` mode at this layer. To replay missed days you'd need a
    separate historical-NAV source, filed as v2.1.
    """
    watchlist = load_watchlist(config_path)
    blackrock_etfs = [
        e for e in watchlist.etf_tickers if e.issuer.strip().lower() == ISSUER_FILTER.lower()
    ]
    if not blackrock_etfs:
        raise SystemExit(
            "watchlists.yml has no etf_tickers with issuer=BlackRock — nothing to fetch."
        )

    owns_http = http is None
    if http is None:
        http = HttpClient(SOURCE_NAME, rate_limit=DEFAULT_RATE_LIMIT)

    try:
        with db.ingest_run(
            SOURCE_NAME,
            endpoint=COLLECT_ENDPOINT_LABEL,
            metadata={
                "watchlist_etfs": [e.ticker for e in blackrock_etfs],
            },
        ) as run:
            try:
                payload = http.get_json(PRODUCT_SCREENER_URL)
                fetched_at = datetime.now(timezone.utc)
            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.HTTPStatusError,
                json.JSONDecodeError,
            ) as exc:
                LOGGER.error("iShares product-screener fetch failed: %s", exc)
                db.record_partial_endpoints(
                    run.id,
                    [
                        {
                            "name": COLLECT_ENDPOINT_LABEL,
                            "url": PRODUCT_SCREENER_URL,
                            "error": str(exc),
                        }
                    ],
                )
                raise RuntimeError(
                    f"iShares product-screener fetch failed: {exc}"
                ) from exc

            db.store_raw_blob(run.id, COLLECT_ENDPOINT_LABEL, PRODUCT_SCREENER_URL, payload)
            snapshots = parse_snapshots(payload, watchlist_etfs=blackrock_etfs)
            if not snapshots:
                LOGGER.warning(
                    "iShares product-screener returned no matching snapshots for "
                    "BlackRock ETFs %s",
                    [e.ticker for e in blackrock_etfs],
                )
                run.add_rows(0)
                return run.id

            rows = [
                _snapshot_to_row(
                    snap,
                    ingest_run_id=run.id,
                    source_endpoint=PRODUCT_SCREENER_URL,
                    fetched_at=fetched_at,
                )
                for snap in snapshots
            ]
            with db.connection() as conn:
                written = db.bulk_upsert(
                    conn,
                    "etf.fund_snapshots",
                    rows,
                    conflict_keys=("ticker", "snapshot_date"),
                )
            run.add_rows(written)
            LOGGER.info(
                "iShares: +%s rows (%s snapshots, tickers=%s)",
                written,
                len(snapshots),
                [s.ticker for s in snapshots],
            )
            return run.id
    finally:
        if owns_http:
            http.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI flags for the collector entry point."""
    parser = argparse.ArgumentParser(
        description=(
            "Collect iShares spot crypto ETF daily snapshots into etf.fund_snapshots."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_WATCHLIST_PATH,
        help="Watchlist path.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the collector from ``python -m genkei.ingest.ishares``."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv if argv is not None else sys.argv[1:])
    run_id = collect(args.config)
    print(f"iShares collector wrote ingest_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
