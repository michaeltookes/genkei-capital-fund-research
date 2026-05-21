"""Unit tests for the crypto peer relative-strength signal (B-090).

Pure-algorithm tests on synthetic ``PricePoint`` series. The lake
loader (``load_relative_strength``) is exercised via the CLI tests
with a mocked ``db.connection`` cursor.
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from decimal import Decimal

from genkei.experiments.relative_strength import (
    DEFAULT_WINDOWS,
    PricePoint,
    compute_relative_strength,
    compute_return_pct,
)


def _series(start: date, prices: list[float]) -> list[PricePoint]:
    return [
        PricePoint(ts=start + timedelta(days=i), price_usd=Decimal(str(p)))
        for i, p in enumerate(prices)
    ]


class ComputeReturnPctTests(unittest.TestCase):
    def test_basic_return(self) -> None:
        # 31 days, price doubles linearly. At 30d window the lookback
        # is day 0 (price=10), latest is day 30 (price=20).
        series = _series(date(2026, 1, 1), [10 + i * (10 / 30) for i in range(31)])
        ret, latest, lookback = compute_return_pct(series, window_days=30)
        self.assertIsNotNone(latest)
        self.assertIsNotNone(lookback)
        self.assertEqual(latest.ts, date(2026, 1, 31))
        self.assertEqual(lookback.ts, date(2026, 1, 1))
        # (20 - 10) / 10 * 100 = 100%
        self.assertEqual(ret, Decimal(100))

    def test_negative_return(self) -> None:
        series = _series(date(2026, 1, 1), [100, 90])
        series = [series[0], PricePoint(ts=date(2026, 1, 31), price_usd=Decimal("75"))]
        ret, _, lookback = compute_return_pct(series, window_days=30)
        self.assertEqual(lookback.ts, date(2026, 1, 1))
        self.assertEqual(ret, Decimal("-25"))

    def test_empty_series_yields_all_none(self) -> None:
        ret, latest, lookback = compute_return_pct([], window_days=7)
        self.assertIsNone(ret)
        self.assertIsNone(latest)
        self.assertIsNone(lookback)

    def test_insufficient_history_returns_none(self) -> None:
        # 5 days of data, asking for 30d return. Latest exists, lookback
        # does not — return is None.
        series = _series(date(2026, 1, 1), [10, 11, 12, 13, 14])
        ret, latest, lookback = compute_return_pct(series, window_days=30)
        self.assertIsNone(ret)
        self.assertIsNotNone(latest)  # latest still picked
        self.assertIsNone(lookback)

    def test_lookback_picks_most_recent_at_or_before_target(self) -> None:
        # Daily prices, asking for 7d return. Latest = day 10. Target =
        # day 3. The most recent observation at-or-before day 3 is day 3 itself.
        series = _series(date(2026, 1, 1), [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20])
        _, latest, lookback = compute_return_pct(series, window_days=7)
        self.assertEqual(latest.ts, date(2026, 1, 11))
        self.assertEqual(lookback.ts, date(2026, 1, 4))  # exactly 7 days before
        self.assertEqual(lookback.price_usd, Decimal("13"))

    def test_zero_lookback_price_returns_none(self) -> None:
        series = [
            PricePoint(ts=date(2026, 1, 1), price_usd=Decimal(0)),
            PricePoint(ts=date(2026, 2, 1), price_usd=Decimal(100)),
        ]
        ret, _, lookback = compute_return_pct(series, window_days=30)
        self.assertEqual(lookback.price_usd, Decimal(0))
        self.assertIsNone(ret)

    def test_rejects_zero_window_days(self) -> None:
        series = _series(date(2026, 1, 1), [10, 20])
        with self.assertRaises(ValueError):
            compute_return_pct(series, window_days=0)


class ComputeRelativeStrengthTests(unittest.TestCase):
    def test_basic_pair(self) -> None:
        # Asset doubles, peer flat → asset return +100%, peer +0%,
        # relative_strength_pct = +100.
        asset = _series(date(2026, 1, 1), [10] + [10 + i * (10 / 30) for i in range(1, 31)])
        peer = _series(date(2026, 1, 1), [50] * 31)
        rows = compute_relative_strength(
            asset, peer, asset="sui", peer="solana", windows=(30,)
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.asset, "sui")
        self.assertEqual(row.peer, "solana")
        self.assertEqual(row.window_days, 30)
        self.assertEqual(row.asset_return_pct, Decimal(100))
        self.assertEqual(row.peer_return_pct, Decimal(0))
        self.assertEqual(row.relative_strength_pct, Decimal(100))

    def test_default_windows_emits_five_rows(self) -> None:
        # Both sides have 400 days of flat prices — every window resolves.
        asset = _series(date(2025, 1, 1), [10] * 400)
        peer = _series(date(2025, 1, 1), [20] * 400)
        rows = compute_relative_strength(asset, peer, asset="a", peer="p")
        self.assertEqual(len(rows), len(DEFAULT_WINDOWS))
        self.assertEqual({r.window_days for r in rows}, set(DEFAULT_WINDOWS))
        for row in rows:
            self.assertEqual(row.relative_strength_pct, Decimal(0))

    def test_insufficient_history_propagates_to_relative_strength(self) -> None:
        # Only 10 days of data — 30d / 90d windows yield None on both
        # sides and the relative_strength is None too.
        asset = _series(date(2026, 1, 1), [10] * 10)
        peer = _series(date(2026, 1, 1), [20] * 10)
        rows = compute_relative_strength(
            asset, peer, asset="a", peer="p", windows=(7, 30, 90)
        )
        for_7d = next(r for r in rows if r.window_days == 7)
        for_30d = next(r for r in rows if r.window_days == 30)
        for_90d = next(r for r in rows if r.window_days == 90)
        # 7d resolves (asset and peer both have 9 prior days)
        self.assertEqual(for_7d.relative_strength_pct, Decimal(0))
        # 30d, 90d don't resolve — relative_strength is None
        self.assertIsNone(for_30d.relative_strength_pct)
        self.assertIsNone(for_90d.relative_strength_pct)

    def test_one_sided_history_yields_none_relative_strength(self) -> None:
        # Asset has 90d of data, peer only has 10d. At the 30d window
        # asset_return resolves but peer_return doesn't → relative_strength None.
        asset = _series(date(2026, 1, 1), [10] * 90)
        peer = _series(date(2026, 3, 1), [20] * 10)
        rows = compute_relative_strength(
            asset, peer, asset="a", peer="p", windows=(30,)
        )
        row = rows[0]
        self.assertEqual(row.asset_return_pct, Decimal(0))
        self.assertIsNone(row.peer_return_pct)
        self.assertIsNone(row.relative_strength_pct)

    def test_sui_vs_sol_synthetic_reproduces_session_numbers(self) -> None:
        # Synthetic version of the morning SUI session's 365d call.
        # SUI: $3.86 → $1.05 over a year. SOL: $168 → $84 over a year.
        # asset_return = (1.05 - 3.86) / 3.86 * 100 ≈ -72.8%
        # peer_return  = (84 - 168) / 168 * 100 ≈ -50.0%
        # relative_strength ≈ -22.8pp
        start = date(2025, 5, 21)
        end = date(2026, 5, 21)
        sui = [
            PricePoint(ts=start, price_usd=Decimal("3.86")),
            PricePoint(ts=end, price_usd=Decimal("1.05")),
        ]
        sol = [
            PricePoint(ts=start, price_usd=Decimal("168")),
            PricePoint(ts=end, price_usd=Decimal("84")),
        ]
        rows = compute_relative_strength(
            sui, sol, asset="sui", peer="solana", windows=(365,)
        )
        row = rows[0]
        self.assertIsNotNone(row.relative_strength_pct)
        # Round to 1 decimal place for stability across Decimal precision.
        rounded = row.relative_strength_pct.quantize(Decimal("0.1"))
        self.assertEqual(rounded, Decimal("-22.8"))

    def test_rejects_invalid_window_in_tuple(self) -> None:
        series = _series(date(2026, 1, 1), [10, 20])
        with self.assertRaises(ValueError):
            compute_relative_strength(
                series, series, asset="a", peer="p", windows=(7, 0, 30)
            )


class DefaultWindowsTests(unittest.TestCase):
    """Pin the headline default-window set so a silent change shows in CI."""

    def test_default_windows(self) -> None:
        self.assertEqual(DEFAULT_WINDOWS, (7, 30, 90, 180, 365))
