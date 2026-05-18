"""Unit tests for the protocol-revenue-vs-price experiment (B-062).

Pure-algorithm tests on synthetic ``FeeRevenuePoint`` and ``PricePoint``
series. No DB, no network — the lake-loading helpers
(``load_fee_series`` / ``load_price_series``) are exercised via the
CLI tests with mocked DB connections.
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from decimal import Decimal

from genkei.experiments.protocol_revenue import (
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_WINDOW_DAYS,
    FeeRevenuePoint,
    PricePoint,
    Snapshot,
    _classify,
    _pct_change,
    _safe_ratio,
    build_snapshots,
    diagnose_divergence,
)


def _fee(day: date, fees: float | None, revenue: float | None = None) -> FeeRevenuePoint:
    return FeeRevenuePoint(
        ts=day,
        fees_usd=Decimal(str(fees)) if fees is not None else None,
        revenue_usd=Decimal(str(revenue)) if revenue is not None else None,
    )


def _price(day: date, price: float | None, mcap: float | None) -> PricePoint:
    return PricePoint(
        ts=day,
        price_usd=Decimal(str(price)) if price is not None else None,
        market_cap_usd=Decimal(str(mcap)) if mcap is not None else None,
    )


def _series(start: date, days: int) -> list[date]:
    return [start + timedelta(days=i) for i in range(days)]


class BuildSnapshotsTests(unittest.TestCase):
    def test_emits_one_snapshot_per_price_day(self) -> None:
        start = date(2026, 1, 1)
        days = _series(start, 5)
        fees = [_fee(d, 1_000.0) for d in days]
        prices = [_price(d, 10.0, 1_000_000.0) for d in days]
        snapshots = build_snapshots(fees, prices, window_days=3)
        self.assertEqual(len(snapshots), 5)
        self.assertEqual([s.ts for s in snapshots], days)

    def test_trailing_window_is_inclusive_of_snapshot_day(self) -> None:
        start = date(2026, 1, 1)
        days = _series(start, 5)
        # 100, 200, 300, 400, 500 across day-1..day-5
        fees = [_fee(d, (i + 1) * 100.0) for i, d in enumerate(days)]
        prices = [_price(d, 10.0, 1_000_000.0) for d in days]
        snapshots = build_snapshots(fees, prices, window_days=3)
        # day 5: trailing 3 days = day-3 + day-4 + day-5 = 300 + 400 + 500
        self.assertEqual(snapshots[-1].trailing_fees_usd, Decimal("1200"))

    def test_annualizes_trailing_window(self) -> None:
        # 30d window, 100/day for 30 days → annualized 100 * 365
        days = _series(date(2026, 1, 1), 30)
        fees = [_fee(d, 100.0) for d in days]
        prices = [_price(d, 10.0, 1_000_000.0) for d in days]
        snapshots = build_snapshots(fees, prices, window_days=30)
        latest = snapshots[-1]
        self.assertEqual(latest.trailing_fees_usd, Decimal("3000"))
        expected = (Decimal("3000") * Decimal("365")) / Decimal("30")
        self.assertEqual(latest.annualized_fees_usd, expected)

    def test_pf_ratio_is_market_cap_over_annualized_fees(self) -> None:
        # 365 daily rows of $100 fees → annualized = 100 * 365 / 365 = $36,500.
        # market cap 365,000 → P/F = 10x exactly (no Decimal rounding).
        days = _series(date(2026, 1, 1), 365)
        fees = [_fee(d, 100.0) for d in days]
        prices = [_price(d, 10.0, 365_000.0) for d in days]
        snapshots = build_snapshots(fees, prices, window_days=365)
        self.assertEqual(snapshots[-1].pf_ratio, Decimal("10"))

    def test_missing_revenue_yields_none_pr_ratio_but_preserves_pf(self) -> None:
        # chainlink-requests case: fees populated, revenue missing.
        days = _series(date(2026, 1, 1), 365)
        fees = [_fee(d, 100.0, revenue=None) for d in days]
        prices = [_price(d, 10.0, 365_000.0) for d in days]
        snapshots = build_snapshots(fees, prices, window_days=365)
        latest = snapshots[-1]
        self.assertEqual(latest.pf_ratio, Decimal("10"))
        self.assertIsNone(latest.pr_ratio)
        self.assertIsNone(latest.annualized_revenue_usd)

    def test_missing_market_cap_yields_none_ratios(self) -> None:
        days = _series(date(2026, 1, 1), 30)
        fees = [_fee(d, 100.0, revenue=50.0) for d in days]
        prices = [_price(d, 10.0, None) for d in days]
        snapshots = build_snapshots(fees, prices, window_days=30)
        latest = snapshots[-1]
        self.assertIsNone(latest.pf_ratio)
        self.assertIsNone(latest.pr_ratio)
        # Trailing aggregates still computed.
        self.assertEqual(latest.trailing_fees_usd, Decimal("3000"))

    def test_zero_annualized_fees_avoids_divide_by_zero(self) -> None:
        days = _series(date(2026, 1, 1), 30)
        fees = [_fee(d, 0.0) for d in days]
        prices = [_price(d, 10.0, 1_000_000.0) for d in days]
        snapshots = build_snapshots(fees, prices, window_days=30)
        self.assertIsNone(snapshots[-1].pf_ratio)
        # Trailing total is 0 but recorded (vs None) because fee rows existed.
        self.assertEqual(snapshots[-1].trailing_fees_usd, Decimal("0"))

    def test_no_fee_data_in_window_yields_none_trailing(self) -> None:
        # Fees stopped 50 days before the snapshot day.
        old_days = _series(date(2026, 1, 1), 10)
        fees = [_fee(d, 100.0) for d in old_days]
        # Price runs much later.
        future = _series(date(2026, 3, 1), 5)
        prices = [_price(d, 10.0, 1_000_000.0) for d in future]
        snapshots = build_snapshots(fees, prices, window_days=30)
        self.assertEqual(len(snapshots), 5)
        for snap in snapshots:
            self.assertIsNone(snap.trailing_fees_usd)
            self.assertIsNone(snap.pf_ratio)

    def test_rejects_zero_window_days(self) -> None:
        with self.assertRaises(ValueError):
            build_snapshots([], [], window_days=0)


class DiagnoseDivergenceTests(unittest.TestCase):
    def _snapshots_with_changes(
        self, *, price_pct: float, revenue_pct: float, days_apart: int = 90
    ) -> list[Snapshot]:
        """Build two synthetic snapshots N days apart with the given % changes."""
        base_ts = date(2026, 1, 1)
        base_mcap = Decimal("1_000_000")
        base_rev = Decimal("100_000")
        now_ts = base_ts + timedelta(days=days_apart)
        now_mcap = base_mcap * (Decimal("1") + Decimal(str(price_pct / 100)))
        now_rev = base_rev * (Decimal("1") + Decimal(str(revenue_pct / 100)))
        return [
            Snapshot(
                ts=base_ts,
                market_cap_usd=base_mcap,
                trailing_fees_usd=base_rev,
                trailing_revenue_usd=base_rev,
                annualized_fees_usd=base_rev,
                annualized_revenue_usd=base_rev,
                pf_ratio=base_mcap / base_rev,
                pr_ratio=base_mcap / base_rev,
            ),
            Snapshot(
                ts=now_ts,
                market_cap_usd=now_mcap,
                trailing_fees_usd=now_rev,
                trailing_revenue_usd=now_rev,
                annualized_fees_usd=now_rev,
                annualized_revenue_usd=now_rev,
                pf_ratio=now_mcap / now_rev,
                pr_ratio=now_mcap / now_rev,
            ),
        ]

    def test_price_up_revenue_down_flags_price_leads_up(self) -> None:
        snaps = self._snapshots_with_changes(price_pct=30, revenue_pct=-30)
        report = diagnose_divergence(snaps, slug="x", coingecko_id="y", lookback_days=90)
        self.assertEqual(report.kind, "price-leads-up")
        self.assertEqual(report.price_change_pct, Decimal("30"))
        self.assertEqual(report.revenue_change_pct, Decimal("-30"))

    def test_price_down_revenue_up_flags_price_leads_down(self) -> None:
        snaps = self._snapshots_with_changes(price_pct=-25, revenue_pct=40)
        report = diagnose_divergence(snaps, slug="x", coingecko_id="y", lookback_days=90)
        self.assertEqual(report.kind, "price-leads-down")

    def test_aligned_when_both_move_same_direction(self) -> None:
        snaps = self._snapshots_with_changes(price_pct=20, revenue_pct=25)
        report = diagnose_divergence(snaps, slug="x", coingecko_id="y", lookback_days=90)
        self.assertEqual(report.kind, "aligned")

    def test_aligned_when_both_changes_below_significance(self) -> None:
        snaps = self._snapshots_with_changes(price_pct=3, revenue_pct=-2)
        report = diagnose_divergence(
            snaps, slug="x", coingecko_id="y", lookback_days=90, significance_pct=Decimal("10")
        )
        self.assertEqual(report.kind, "aligned")

    def test_insufficient_data_when_no_lookback_snapshot_far_enough_back(self) -> None:
        snaps = self._snapshots_with_changes(price_pct=50, revenue_pct=-50, days_apart=10)
        report = diagnose_divergence(snaps, slug="x", coingecko_id="y", lookback_days=90)
        self.assertEqual(report.kind, "insufficient-data")
        self.assertIsNone(report.price_change_pct)
        self.assertIsNotNone(report.pf_ratio_now)

    def test_empty_snapshots_returns_insufficient_data(self) -> None:
        report = diagnose_divergence([], slug="x", coingecko_id="y", lookback_days=90)
        self.assertEqual(report.kind, "insufficient-data")
        self.assertIsNone(report.pf_ratio_now)

    def test_uses_oldest_qualifying_snapshot_as_baseline(self) -> None:
        # Three snapshots: day 0, day 50, day 100. lookback=60 → baseline must
        # be day 0 (the latest snapshot whose ts <= now-60d = day 40).
        snaps_base = self._snapshots_with_changes(price_pct=20, revenue_pct=-30, days_apart=100)
        mid = Snapshot(
            ts=date(2026, 1, 1) + timedelta(days=50),
            market_cap_usd=Decimal("1_100_000"),
            trailing_fees_usd=Decimal("80_000"),
            trailing_revenue_usd=Decimal("80_000"),
            annualized_fees_usd=Decimal("80_000"),
            annualized_revenue_usd=Decimal("80_000"),
            pf_ratio=Decimal("13.75"),
            pr_ratio=Decimal("13.75"),
        )
        snaps = [snaps_base[0], mid, snaps_base[1]]
        report = diagnose_divergence(snaps, slug="x", coingecko_id="y", lookback_days=60)
        # base is day 0 (mcap=1_000_000, rev=100_000); now is day 100
        # (mcap=1_200_000, rev=70_000) → price +20%, revenue -30%.
        self.assertEqual(report.price_change_pct, Decimal("20"))
        self.assertEqual(report.revenue_change_pct, Decimal("-30"))
        self.assertEqual(report.kind, "price-leads-up")

    def test_rejects_zero_lookback_days(self) -> None:
        with self.assertRaises(ValueError):
            diagnose_divergence([], slug="x", coingecko_id="y", lookback_days=0)

    def test_rejects_negative_significance(self) -> None:
        with self.assertRaises(ValueError):
            diagnose_divergence(
                [], slug="x", coingecko_id="y", significance_pct=Decimal("-1")
            )


class ClassifyHelperTests(unittest.TestCase):
    def test_both_none_is_insufficient(self) -> None:
        self.assertEqual(
            _classify(price_change=None, revenue_change=None, significance_pct=Decimal("10")),
            "insufficient-data",
        )

    def test_one_side_none_is_insufficient(self) -> None:
        self.assertEqual(
            _classify(
                price_change=Decimal("20"),
                revenue_change=None,
                significance_pct=Decimal("10"),
            ),
            "insufficient-data",
        )


class PctChangeHelperTests(unittest.TestCase):
    def test_basic_increase(self) -> None:
        self.assertEqual(_pct_change(Decimal("100"), Decimal("150")), Decimal("50"))

    def test_basic_decrease(self) -> None:
        self.assertEqual(_pct_change(Decimal("100"), Decimal("80")), Decimal("-20"))

    def test_zero_base_returns_none(self) -> None:
        self.assertIsNone(_pct_change(Decimal("0"), Decimal("100")))

    def test_none_args_returns_none(self) -> None:
        self.assertIsNone(_pct_change(None, Decimal("100")))
        self.assertIsNone(_pct_change(Decimal("100"), None))


class SafeRatioHelperTests(unittest.TestCase):
    def test_basic(self) -> None:
        self.assertEqual(_safe_ratio(Decimal("10"), Decimal("2")), Decimal("5"))

    def test_zero_denominator(self) -> None:
        self.assertIsNone(_safe_ratio(Decimal("10"), Decimal("0")))

    def test_none_args(self) -> None:
        self.assertIsNone(_safe_ratio(None, Decimal("2")))
        self.assertIsNone(_safe_ratio(Decimal("10"), None))


class DefaultConstantsTests(unittest.TestCase):
    """Pins the headline defaults so silent changes show up in CI."""

    def test_defaults(self) -> None:
        self.assertEqual(DEFAULT_WINDOW_DAYS, 30)
        self.assertEqual(DEFAULT_LOOKBACK_DAYS, 90)
