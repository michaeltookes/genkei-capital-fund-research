"""SEC 10-Q / 10-K quarter-end ETF shares-outstanding backfill (B-114).

The iShares product-screener feed (B-107, ``genkei.ingest.ishares``) only
publishes the *current* daily snapshot — there is no historical NAV path from
that endpoint, so ``etf.fund_snapshots`` starts the day the daily cron first
ran. But every spot-crypto ETF trust files quarterly (10-Q) and annual (10-K)
reports whose XBRL financial statements carry point-in-time
**shares-outstanding** and **net assets** as of each period-end — 4 checkpoints
per year per fund, back to inception. Extracting those reconstructs the
pre-feed AUM trajectory (quarterly drift, not daily flows) and lets the daily
snapshots be triangulated against an independent SEC-filed figure.

This is a self-contained single-step collector writing into the same
``etf.fund_snapshots`` table as the iShares / Bitwise daily ingesters (one more
writer to the shared ETF fact stream, exactly like ``bitwise.py`` sits beside
``ishares.py``). Per watchlist ``etf_tickers`` entry that carries a ``cik``, it:

  1. Fetches the trust's XBRL ``companyfacts`` JSON (entire fact history, one
     request per CIK) via the SEC endpoint the equity ingester already uses.
  2. Extracts the quarter-end **shares** and **net-assets** facts, deduping
     each period-end to the fact *first filed* for it (the original
     current-period report; later filings repeat it as a prior-period
     comparative with the same ``end`` date).
  3. Joins the two on period-end, derives ``nav = net_assets / shares``, and
     lands one row per ``(ticker, period_end)`` with a distinctive
     ``source_endpoint`` marker (:data:`SOURCE_ENDPOINT_MARKER`).

**Precedence — never clobber the daily feed.** Rows are upserted
``ON CONFLICT (ticker, snapshot_date) DO NOTHING``, so a 10-Q checkpoint that
lands on a date the authoritative iShares daily feed already covers is skipped;
the daily row (published NAV + TNA from the fund administrator) wins. The
distinctive ``source_endpoint`` also lets the net-flow query exclude these
quarterly checkpoints so they never enter the daily-flow ``LAG`` (they're AUM
checkpoints, not daily flows — see ``genkei.cli.etf_flows``).

**NAV is derived, not read.** The explicit ``NetAssetValuePerShare`` XBRL tag
is sparse (only tagged from ~2026), but ``FairValueNetAssetLiability`` /
``TemporaryEquitySharesOutstanding`` are both point-in-time balance-sheet facts
at the same period-end instant, so ``TNA / shares`` reconstructs NAV uniformly
and — verified against the funds that do tag it — reconciles to the published
figure within rounding (IBIT 2024-12-31: derived $53.09 vs tagged $53.09).
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
from genkei.common.numeric import safe_decimal
from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    EtfTickerEntry,
    load_watchlist,
)
from genkei.ingest.sec import build_companyfacts_url, resolve_user_agent

SOURCE_NAME = "sec_etf_shares"
# "collect" matches the recurring-endpoint convention pinned by
# test_every_source_expects_at_least_a_collect_endpoint.
COLLECT_ENDPOINT_LABEL = "collect"
# Stamped into etf.fund_snapshots.source_endpoint so 10-Q-derived rows are
# distinguishable from daily-feed rows (and excludable from the net-flow LAG).
SOURCE_ENDPOINT_MARKER = "sec_10q_xbrl"

# XBRL concept candidates, in priority order. The first concept that yields any
# period-report facts for a fund is used. BlackRock trusts (IBIT / ETHA / ETHB,
# the B-114 v1 funds) tag shares as TemporaryEquitySharesOutstanding and net
# assets as FairValueNetAssetLiability; the lists are the extension point when a
# future issuer (Bitwise / Grayscale) uses a different tag.
SHARE_CONCEPTS: tuple[tuple[str, str, str], ...] = (
    ("us-gaap", "TemporaryEquitySharesOutstanding", "shares"),
)
NET_ASSET_CONCEPTS: tuple[tuple[str, str, str], ...] = (
    ("us-gaap", "FairValueNetAssetLiability", "USD"),
)
# Period reports whose balance-sheet facts are period-end checkpoints. 10-K
# carries the year-end (Q4) checkpoint that no 10-Q covers.
PERIOD_FORMS = frozenset({"10-Q", "10-K"})

# Polite ceiling; the SEC data API tolerates ~10 req/s but this is a 3-CIK
# daily-cron job, so one req/s is courteous.
DEFAULT_RATE_LIMIT = RateLimit.per_second(1)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _QuarterEndSnapshot:
    """One period-end checkpoint destined for etf.fund_snapshots."""

    ticker: str
    snapshot_date: date
    issuer: str
    asset: str
    nav_per_share_usd: Decimal
    total_net_assets_usd: Decimal
    shares_outstanding: Decimal


def _parse_end_date(raw: Any) -> date | None:
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


def extract_checkpoints(
    facts: dict[str, Any],
    concepts: tuple[tuple[str, str, str], ...],
) -> dict[date, Decimal]:
    """Return ``{period_end: value}`` for the first concept that has any facts.

    For each period-end date, keeps the value from the fact *first filed* — the
    original current-period report. Later filings repeat the same ``end`` as a
    prior-period comparative (identical ``end``, later ``filed``); the
    earliest-filed rule ignores those, so no calendar / fiscal-year assumption
    is needed. Only 10-Q / 10-K facts count (period reports).
    """
    for taxonomy, concept, unit in concepts:
        rows = (
            facts.get(taxonomy, {})
            .get(concept, {})
            .get("units", {})
            .get(unit, [])
        )
        if not rows:
            continue
        best: dict[date, tuple[str, Decimal]] = {}
        for row in rows:
            if row.get("form") not in PERIOD_FORMS:
                continue
            end = _parse_end_date(row.get("end"))
            filed = row.get("filed")
            value = safe_decimal(row.get("val"), field=f"{concept}.val")
            if end is None or not isinstance(filed, str) or value is None:
                continue
            existing = best.get(end)
            if existing is None or filed < existing[0]:
                best[end] = (filed, value)
        if best:
            return {end: value for end, (_filed, value) in best.items()}
    return {}


def build_snapshots(
    payload: dict[str, Any],
    *,
    entry: EtfTickerEntry,
) -> list[_QuarterEndSnapshot]:
    """Decode one fund's companyfacts payload into quarter-end snapshot rows.

    Joins the shares and net-asset checkpoints on period-end; a period-end that
    has one fact but not the other is dropped (both are needed to derive NAV).
    Rows before the fund's ``launch_date`` (seed-capital / registration facts)
    are dropped. Zero or negative shares are dropped (can't derive NAV).
    """
    facts = payload.get("facts")
    if not isinstance(facts, dict):
        raise ValueError(
            f"companyfacts for {entry.ticker} has no 'facts' object "
            f"(got {type(payload.get('facts')).__name__})"
        )
    shares = extract_checkpoints(facts, SHARE_CONCEPTS)
    net_assets = extract_checkpoints(facts, NET_ASSET_CONCEPTS)
    if not shares:
        LOGGER.warning(
            "sec_etf_shares %s: no shares-outstanding facts in %s — no known "
            "share concept matched; extend SHARE_CONCEPTS if the issuer tags it "
            "differently",
            entry.ticker,
            [c[1] for c in SHARE_CONCEPTS],
        )
        return []

    launch = _parse_end_date(entry.launch_date) if entry.launch_date else None
    snapshots: list[_QuarterEndSnapshot] = []
    for end in sorted(set(shares) & set(net_assets)):
        if launch is not None and end < launch:
            continue
        share_val = shares[end]
        tna_val = net_assets[end]
        if share_val <= 0 or tna_val < 0:
            continue
        nav = (tna_val / share_val).quantize(Decimal("0.00000001"))
        snapshots.append(
            _QuarterEndSnapshot(
                ticker=entry.ticker.upper(),
                snapshot_date=end,
                issuer=entry.issuer,
                asset=entry.asset.upper(),
                nav_per_share_usd=nav,
                total_net_assets_usd=tna_val.quantize(Decimal("0.01")),
                shares_outstanding=share_val.quantize(Decimal("0.0001")),
            )
        )
    return snapshots


def _snapshot_to_row(
    snap: _QuarterEndSnapshot,
    *,
    ingest_run_id: int,
    fetched_at: datetime,
) -> dict[str, Any]:
    return {
        "ticker": snap.ticker,
        "snapshot_date": snap.snapshot_date,
        "issuer": snap.issuer,
        "asset": snap.asset,
        "cusip": None,
        "isin": None,
        "nav_per_share_usd": snap.nav_per_share_usd,
        "total_net_assets_usd": snap.total_net_assets_usd,
        "shares_outstanding": snap.shares_outstanding,
        "source_endpoint": SOURCE_ENDPOINT_MARKER,
        "fetched_at": fetched_at,
        "ingest_run_id": ingest_run_id,
    }


def _funds_with_cik(config_path: Path) -> list[EtfTickerEntry]:
    watchlist = load_watchlist(config_path)
    return [e for e in watchlist.etf_tickers if e.cik]


def collect(
    config_path: Path = DEFAULT_WATCHLIST_PATH,
    *,
    http: HttpClient | None = None,
) -> int:
    """Backfill quarter-end shares-outstanding for every CIK-tagged ETF.

    Returns the meta.ingest_runs id. Idempotent: ``DO NOTHING`` on the
    ``(ticker, snapshot_date)`` PK means a re-run inserts only genuinely new
    period-ends and never overwrites an existing (daily or 10-Q) row.
    """
    funds = _funds_with_cik(config_path)
    if not funds:
        raise SystemExit(
            "watchlists.yml has no etf_tickers with a `cik` — nothing to backfill."
        )

    owns_http = http is None
    if http is None:
        http = HttpClient(
            SOURCE_NAME,
            user_agent=resolve_user_agent(),
            rate_limit=DEFAULT_RATE_LIMIT,
        )

    try:
        with db.ingest_run(
            SOURCE_NAME,
            endpoint=COLLECT_ENDPOINT_LABEL,
            metadata={"funds": [e.ticker for e in funds]},
        ) as run:
            fetched_at = datetime.now(timezone.utc)
            all_rows: list[dict[str, Any]] = []
            partial: list[dict[str, str]] = []
            for entry in funds:
                url = build_companyfacts_url(entry.cik or "")
                try:
                    payload = http.get_json(url)
                except (
                    httpx.TimeoutException,
                    httpx.NetworkError,
                    httpx.HTTPStatusError,
                    json.JSONDecodeError,
                ) as exc:
                    LOGGER.error(
                        "sec_etf_shares %s: companyfacts fetch failed: %s",
                        entry.ticker,
                        exc,
                    )
                    partial.append(
                        {"name": entry.ticker, "url": url, "error": str(exc)}
                    )
                    continue
                db.store_raw_blob(run.id, f"companyfacts_{entry.cik}", url, payload)
                snapshots = build_snapshots(payload, entry=entry)
                all_rows.extend(
                    _snapshot_to_row(s, ingest_run_id=run.id, fetched_at=fetched_at)
                    for s in snapshots
                )
                LOGGER.info(
                    "sec_etf_shares %s: %s quarter-end checkpoints",
                    entry.ticker,
                    len(snapshots),
                )

            if partial:
                db.record_partial_endpoints(run.id, partial)

            written = 0
            if all_rows:
                with db.connection() as conn:
                    # DO NOTHING (update_cols=[]) — never clobber an existing
                    # daily-feed row, and idempotent on re-backfill.
                    written = db.bulk_upsert(
                        conn,
                        "etf.fund_snapshots",
                        all_rows,
                        conflict_keys=("ticker", "snapshot_date"),
                        update_cols=[],
                    )
            run.add_rows(written)
            LOGGER.info(
                "sec_etf_shares: +%s new rows from %s checkpoints across %s funds",
                written,
                len(all_rows),
                len(funds),
            )
            if partial and len(partial) == len(funds) and not all_rows:
                raise RuntimeError(
                    f"sec_etf_shares: all {len(funds)} companyfacts fetches failed"
                )
            if not all_rows:
                raise RuntimeError(
                    "sec_etf_shares: no SEC quarter-end checkpoints parsed for "
                    f"{len(funds)} configured CIK-tagged fund(s); check SEC XBRL "
                    "concept mappings"
                )
            return run.id
    finally:
        if owns_http:
            http.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill quarter-end ETF shares-outstanding from SEC 10-Q/10-K XBRL "
            "into etf.fund_snapshots (B-114)."
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
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv if argv is not None else sys.argv[1:])
    run_id = collect(args.config)
    print(f"sec_etf_shares collector wrote ingest_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
