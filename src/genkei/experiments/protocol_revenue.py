"""Protocol revenue vs token price experiment (B-062).

Phase 5 experiment over ``defillama.protocol_fees`` (B-083) ×
``coingecko.market_data``. Surfaces the fundamental link between a
DeFi protocol's revenue stream and its token's market valuation.

Two roles in this module:

  * **Pure functions** (``build_snapshots``, ``diagnose_divergence``)
    operate on plain dataclasses. No DB, no CLI. Easy to test on
    synthetic series.
  * **Lake-loading helpers** (``load_fee_series``, ``load_price_series``)
    pull the underlying data from Postgres. Caller composes them with
    the pure functions.

The headline metric is the **P/F ratio** — fully-loaded market cap
divided by trailing-window fees annualized to a year. Lower P/F means
the token is cheaper per dollar of protocol fees collected; higher P/F
means the market is paying more per dollar of fee throughput. Tracking
P/F over time, alongside the trend in revenue itself, surfaces
divergences (price runs ahead of fundamentals, or fundamentals
deteriorate before price catches down) that are the experiment's
investable signal.

The trailing window (default 30d) smooths daily noise. The lookback
window (default 90d) is the comparison span for trend math; "+15% price
trend, -25% revenue trend" is the kind of divergence the LINK research
session was trying to detect manually.

Divergence categories:

  * **price-leads-up** — price up materially, revenue flat or down.
    Potential overvaluation; market is pricing in something the fee
    stream isn't yet showing.
  * **price-leads-down** — price down, revenue flat or up. Potential
    undervaluation; the fundamentals haven't degraded but the token
    has been marked down.
  * **aligned** — both move the same direction (price catches up to
    fundamentals or the other way).
  * **insufficient-data** — not enough history to compute either side.

``significance_pct`` controls how big a delta has to be on either side
to count toward the divergence call (default 10%). Smaller deltas are
treated as "flat" so the diagnosis doesn't flicker on noise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from genkei.common import db

# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FeeRevenuePoint:
    """One day's fees/revenue for a protocol from ``defillama.protocol_fees``."""

    ts: date
    fees_usd: Decimal | None
    revenue_usd: Decimal | None


@dataclass(frozen=True)
class PricePoint:
    """One day's market data for a token from ``coingecko.market_data``."""

    ts: date
    price_usd: Decimal | None
    market_cap_usd: Decimal | None


@dataclass(frozen=True)
class Snapshot:
    """A single day's joined view of revenue + price for one protocol/token."""

    ts: date
    market_cap_usd: Decimal | None
    trailing_fees_usd: Decimal | None
    trailing_revenue_usd: Decimal | None
    annualized_fees_usd: Decimal | None
    annualized_revenue_usd: Decimal | None
    pf_ratio: Decimal | None
    pr_ratio: Decimal | None
    price_usd: Decimal | None = None


@dataclass(frozen=True)
class DivergenceReport:
    """A trend-vs-trend comparison between price and revenue."""

    slug: str
    coingecko_id: str
    horizon: str
    as_of: date
    lookback_days: int
    window_days: int
    price_change_pct: Decimal | None
    revenue_change_pct: Decimal | None
    pf_ratio_now: Decimal | None
    pf_ratio_lookback: Decimal | None
    kind: str  # "price-leads-up" | "price-leads-down" | "aligned" | "insufficient-data"


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


DEFAULT_WINDOW_DAYS = 30
DEFAULT_LOOKBACK_DAYS = 90
DEFAULT_SIGNIFICANCE_PCT = Decimal("10")
DAYS_PER_YEAR = Decimal("365")


def build_snapshots(
    fee_series: list[FeeRevenuePoint],
    price_series: list[PricePoint],
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> list[Snapshot]:
    """Join fees + price into one row per calendar day with trailing aggregates.

    For each day present in ``price_series``, computes the trailing-window
    fees and revenue sum from ``fee_series`` (window is *inclusive* of the
    snapshot day), annualizes to a year, and divides market cap by the
    annualized figure to produce the P/F and P/R ratios. Days where the
    price series carries no market_cap (some early CoinGecko rows) or
    where the trailing window has no fee data at all are emitted with
    ``None`` ratios so callers can see the gap rather than a misleading
    zero.
    """
    if window_days < 1:
        raise ValueError("window_days must be >= 1")

    fees_by_date = {p.ts: p for p in fee_series}
    fee_dates = sorted(fees_by_date.keys())
    price_sorted = sorted(price_series, key=lambda p: p.ts)

    snapshots: list[Snapshot] = []
    for point in price_sorted:
        window_start = point.ts - timedelta(days=window_days - 1)
        window_fees = Decimal(0)
        window_revenue = Decimal(0)
        fee_count = 0
        revenue_count = 0
        for d in fee_dates:
            if d < window_start:
                continue
            if d > point.ts:
                break
            fee_row = fees_by_date[d]
            if fee_row.fees_usd is not None:
                window_fees += fee_row.fees_usd
                fee_count += 1
            if fee_row.revenue_usd is not None:
                window_revenue += fee_row.revenue_usd
                revenue_count += 1

        trailing_fees = window_fees if fee_count > 0 else None
        trailing_revenue = window_revenue if revenue_count > 0 else None
        annualized_fees = (
            (trailing_fees * DAYS_PER_YEAR) / Decimal(window_days)
            if trailing_fees is not None
            else None
        )
        annualized_revenue = (
            (trailing_revenue * DAYS_PER_YEAR) / Decimal(window_days)
            if trailing_revenue is not None
            else None
        )
        pf_ratio = _safe_ratio(point.market_cap_usd, annualized_fees)
        pr_ratio = _safe_ratio(point.market_cap_usd, annualized_revenue)
        snapshots.append(
            Snapshot(
                ts=point.ts,
                market_cap_usd=point.market_cap_usd,
                trailing_fees_usd=trailing_fees,
                trailing_revenue_usd=trailing_revenue,
                annualized_fees_usd=annualized_fees,
                annualized_revenue_usd=annualized_revenue,
                pf_ratio=pf_ratio,
                pr_ratio=pr_ratio,
                price_usd=point.price_usd,
            )
        )
    return snapshots


def diagnose_divergence(
    snapshots: list[Snapshot],
    *,
    slug: str,
    coingecko_id: str,
    horizon: str = "crypto:core",
    window_days: int = DEFAULT_WINDOW_DAYS,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    significance_pct: Decimal = DEFAULT_SIGNIFICANCE_PCT,
) -> DivergenceReport:
    """Compare the latest snapshot against one ~lookback_days ago.

    The "now" snapshot is the last in the list (assumed time-ordered).
    The "lookback" snapshot is the latest snapshot whose ``ts`` is on
    or before ``now.ts - lookback_days``. Returns a report with the
    percentage change in token price (``price_usd``), annualized
    revenue across that span, and a divergence kind. Market cap remains
    available for P/F and P/R ratios, but not ``price_change_pct``.
    """
    if lookback_days < 1:
        raise ValueError("lookback_days must be >= 1")
    if significance_pct < 0:
        raise ValueError("significance_pct must be >= 0")
    if not snapshots:
        return DivergenceReport(
            slug=slug,
            coingecko_id=coingecko_id,
            horizon=horizon,
            as_of=date.today(),
            lookback_days=lookback_days,
            window_days=window_days,
            price_change_pct=None,
            revenue_change_pct=None,
            pf_ratio_now=None,
            pf_ratio_lookback=None,
            kind="insufficient-data",
        )

    snapshots_sorted = sorted(snapshots, key=lambda s: s.ts)
    now = snapshots_sorted[-1]
    cutoff = now.ts - timedelta(days=lookback_days)
    lookback: Snapshot | None = None
    for snap in reversed(snapshots_sorted[:-1]):
        if snap.ts <= cutoff:
            lookback = snap
            break

    if lookback is None:
        return DivergenceReport(
            slug=slug,
            coingecko_id=coingecko_id,
            horizon=horizon,
            as_of=now.ts,
            lookback_days=lookback_days,
            window_days=window_days,
            price_change_pct=None,
            revenue_change_pct=None,
            pf_ratio_now=now.pf_ratio,
            pf_ratio_lookback=None,
            kind="insufficient-data",
        )

    price_change = _pct_change(lookback.price_usd, now.price_usd)
    lookback_revenue = (
        lookback.annualized_revenue_usd
        if lookback.annualized_revenue_usd is not None
        else lookback.annualized_fees_usd
    )
    now_revenue = (
        now.annualized_revenue_usd
        if now.annualized_revenue_usd is not None
        else now.annualized_fees_usd
    )
    revenue_change = _pct_change(
        lookback_revenue,
        now_revenue,
    )
    kind = _classify(
        price_change=price_change,
        revenue_change=revenue_change,
        significance_pct=significance_pct,
    )
    return DivergenceReport(
        slug=slug,
        coingecko_id=coingecko_id,
        horizon=horizon,
        as_of=now.ts,
        lookback_days=lookback_days,
        window_days=window_days,
        price_change_pct=price_change,
        revenue_change_pct=revenue_change,
        pf_ratio_now=now.pf_ratio,
        pf_ratio_lookback=lookback.pf_ratio,
        kind=kind,
    )


def _classify(
    *,
    price_change: Decimal | None,
    revenue_change: Decimal | None,
    significance_pct: Decimal,
) -> str:
    """Categorize the price-vs-revenue trend pair.

    ``price-leads-up`` covers "price moved up faster than fundamentals"
    — explicit (price up, revenue down) OR implicit (price up, revenue
    flat) OR market-hasn't-repriced-bad-news (price flat, revenue
    down). Symmetric for ``price-leads-down``. ``aligned`` is the
    everyday case where both move the same direction or both are flat.
    """
    if price_change is None or revenue_change is None:
        return "insufficient-data"
    price_material = abs(price_change) >= significance_pct
    revenue_material = abs(revenue_change) >= significance_pct
    if not price_material and not revenue_material:
        return "aligned"
    if price_material and revenue_material:
        same_sign = (price_change >= 0) == (revenue_change >= 0)
        if same_sign:
            return "aligned"
        return "price-leads-up" if price_change > 0 else "price-leads-down"
    if price_material:
        return "price-leads-up" if price_change > 0 else "price-leads-down"
    # Only revenue is material. Revenue up + price flat → market hasn't
    # repriced the upside ("price-leads-down" in the undervaluation sense).
    return "price-leads-down" if revenue_change > 0 else "price-leads-up"


def _safe_ratio(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _pct_change(base: Decimal | None, current: Decimal | None) -> Decimal | None:
    if base is None or current is None or base == 0:
        return None
    return ((current - base) / base) * Decimal(100)


# ---------------------------------------------------------------------------
# Lake-loading helpers
# ---------------------------------------------------------------------------


def load_fee_series(
    slug: str,
    *,
    since: date | None = None,
    until: date | None = None,
) -> list[FeeRevenuePoint]:
    """Pull every fees/revenue row for ``slug`` from defillama.protocol_fees."""
    if since is not None and until is not None and since > until:
        raise ValueError(f"since must be on or before until: {since} > {until}")
    sql = (
        "SELECT ts, fees_usd, revenue_usd FROM defillama.protocol_fees "
        "WHERE slug = %s"
    )
    params: list[Any] = [slug]
    if since is not None:
        sql += " AND ts >= %s"
        params.append(_utc_start(since))
    if until is not None:
        sql += " AND ts <= %s"
        params.append(_utc_end(until))
    sql += " ORDER BY ts"
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        FeeRevenuePoint(ts=_to_date(ts), fees_usd=fees, revenue_usd=revenue)
        for (ts, fees, revenue) in rows
    ]


def load_price_series(
    coingecko_id: str,
    *,
    since: date | None = None,
    until: date | None = None,
) -> list[PricePoint]:
    """Pull every price/market_cap row for ``coingecko_id`` from coingecko.market_data."""
    if since is not None and until is not None and since > until:
        raise ValueError(f"since must be on or before until: {since} > {until}")
    sql = (
        "SELECT ts, price_usd, market_cap_usd FROM coingecko.market_data "
        "WHERE coingecko_id = %s"
    )
    params: list[Any] = [coingecko_id]
    if since is not None:
        sql += " AND ts >= %s"
        params.append(_utc_start(since))
    if until is not None:
        sql += " AND ts <= %s"
        params.append(_utc_end(until))
    sql += " ORDER BY ts"
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        PricePoint(ts=_to_date(ts), price_usd=price, market_cap_usd=mcap)
        for (ts, price, mcap) in rows
    ]


def _utc_start(d: date) -> datetime:
    return datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)


def _utc_end(d: date) -> datetime:
    return datetime.combine(d, datetime.max.time(), tzinfo=timezone.utc)


def _to_date(ts: Any) -> date:
    """Coerce Postgres ``timestamptz`` to a calendar date."""
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc).date()
        return ts.astimezone(timezone.utc).date()
    return ts
