"""Unit tests for the TVL drawdown experiment (B-058).

Tests are scoped to the pure functions (engineer_features,
classifier_fires, evaluate). The lake loader + run_chain_evaluation
are exercised in live smoke during development; not in CI.
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from decimal import Decimal

from genkei.experiments.tvl_drawdown import (
    AlignedRow,
    FeatureRow,
    classifier_fires,
    engineer_features,
    evaluate,
)


def _make_aligned(
    days: int,
    *,
    tvl_path: list[Decimal] | None = None,
    price_path: list[Decimal] | None = None,
    start: date = date(2024, 1, 1),
) -> list[AlignedRow]:
    """Build a synthetic AlignedRow sequence. Defaults to flat-100 series."""
    tvl_path = tvl_path or [Decimal("100")] * days
    price_path = price_path or [Decimal("100")] * days
    assert len(tvl_path) == days and len(price_path) == days
    return [
        AlignedRow(ts=start + timedelta(days=i), tvl_usd=tvl_path[i], price_usd=price_path[i])
        for i in range(days)
    ]


class FeatureEngineeringTests(unittest.TestCase):
    def test_flat_series_yields_zero_percent_changes(self) -> None:
        # 150 days of flat TVL + price. Pick day 95 — past all
        # lookback windows (90+ days) and with 30+ days of forward
        # window remaining.
        aligned = _make_aligned(150)
        features = engineer_features(aligned, forward_window_days=30)
        mid = features[95]
        self.assertEqual(mid.tvl_change_7d_pct, Decimal("0"))
        self.assertEqual(mid.tvl_change_30d_pct, Decimal("0"))
        self.assertEqual(mid.tvl_change_90d_pct, Decimal("0"))
        self.assertEqual(mid.tvl_drawdown_from_peak_90d_pct, Decimal("0"))
        # Forward window is also flat → no drawdown.
        self.assertIsNotNone(mid.forward_drawdown_pct)
        self.assertEqual(mid.forward_drawdown_pct, Decimal("0"))

    def test_lookback_windows_are_none_when_history_too_short(self) -> None:
        # Day 5: no 7d lookback yet → tvl_change_7d_pct = None.
        aligned = _make_aligned(10)
        features = engineer_features(aligned)
        self.assertIsNone(features[5].tvl_change_7d_pct)
        self.assertIsNone(features[5].tvl_change_30d_pct)
        self.assertIsNone(features[5].tvl_zscore_90d)

    def test_forward_drawdown_label_picks_worst_drop_in_window(self) -> None:
        # Day 0 price = 100; day 5 price = 80 (-20%); day 30 = 110.
        prices = [Decimal("100")] + [Decimal("100")] * 4 + [Decimal("80")] + [
            Decimal("100")
        ] * 24 + [Decimal("110")] + [Decimal("100")] * 5
        aligned = _make_aligned(36, price_path=prices)
        features = engineer_features(aligned, forward_window_days=30)
        # Day 0's lookahead window is days 1..30. The min in that
        # window is 80, so the forward drawdown is 20%.
        self.assertEqual(features[0].forward_drawdown_pct, Decimal("20"))

    def test_tvl_drawdown_from_peak_tracks_trailing_max(self) -> None:
        # TVL rises to 200 at day 50, falls to 100 by day 100.
        # On day 100, 90d-peak = 200, current = 100 → drawdown 50%.
        tvl = (
            [Decimal("100")] * 30  # baseline
            + [Decimal(str(100 + i * 5)) for i in range(30)]  # rises 100→245
            + [Decimal("200")]  # peak at day 60
            + [Decimal(str(200 - i * 5)) for i in range(39)]  # falls
        )
        # 100 days total.
        self.assertEqual(len(tvl), 100)
        aligned = _make_aligned(100, tvl_path=tvl)
        features = engineer_features(aligned)
        # At day 95 the trailing window includes the peak (200) and
        # the current value (200 - 35*5 = 25), so drawdown ≈ 87.5%.
        # Much steeper than what we'd see in real data, but the math
        # checks out — we just verify it's > 80% to absorb off-by-one
        # differences in window semantics.
        row = features[95]
        self.assertIsNotNone(row.tvl_drawdown_from_peak_90d_pct)
        self.assertGreater(row.tvl_drawdown_from_peak_90d_pct, Decimal("80"))

    def test_zscore_negative_when_current_is_below_recent_average(self) -> None:
        # TVL stable at 100 for 60 days, then drops to 60 for 30 days
        # → on the last day, current = 60 vs trailing 90d window
        # spanning all 90 days including the drop → mean ≈ 86.7, σ ≈ 19,
        # z = (60 - 86.7) / 19 ≈ -1.4.
        tvl = [Decimal("100")] * 60 + [Decimal("60")] * 30
        aligned = _make_aligned(90, tvl_path=tvl)
        features = engineer_features(aligned)
        last = features[89]
        self.assertIsNotNone(last.tvl_zscore_90d)
        self.assertLess(last.tvl_zscore_90d, Decimal("-1"))

    def test_short_input_yields_empty_output(self) -> None:
        self.assertEqual(engineer_features([], forward_window_days=30), [])

    def test_forward_label_is_none_near_end_of_series(self) -> None:
        # Last row of a 50-day series with 30-day lookahead: window
        # doesn't fit → forward_drawdown_pct is None.
        aligned = _make_aligned(50)
        features = engineer_features(aligned, forward_window_days=30)
        # The last 30 rows don't have a full lookahead window.
        self.assertIsNone(features[-1].forward_drawdown_pct)
        self.assertIsNone(features[-15].forward_drawdown_pct)
        # 35 days before the end: window fits.
        self.assertIsNotNone(features[14].forward_drawdown_pct)


class ClassifierFiresTests(unittest.TestCase):
    def _row(self, **overrides) -> FeatureRow:
        base = {
            "ts": date(2024, 6, 1),
            "tvl_usd": Decimal("100"),
            "price_usd": Decimal("100"),
            "tvl_change_7d_pct": Decimal("0"),
            "tvl_change_30d_pct": Decimal("0"),
            "tvl_change_90d_pct": Decimal("0"),
            "tvl_drawdown_from_peak_90d_pct": Decimal("0"),
            "tvl_zscore_90d": Decimal("0"),
            "forward_drawdown_pct": Decimal("0"),
        }
        base.update(overrides)
        return FeatureRow(**base)

    def test_all_three_conditions_must_fire(self) -> None:
        # Set defaults: 30d change -15, drawdown 20, z -1.5 — all three
        # trigger → rule fires.
        row = self._row(
            tvl_change_30d_pct=Decimal("-15"),
            tvl_drawdown_from_peak_90d_pct=Decimal("20"),
            tvl_zscore_90d=Decimal("-1.5"),
        )
        self.assertTrue(classifier_fires(row))

    def test_missing_30d_change_blocks_fire(self) -> None:
        # 30d change is None (insufficient history) → can't fire even
        # if the other two would.
        row = self._row(
            tvl_change_30d_pct=None,
            tvl_drawdown_from_peak_90d_pct=Decimal("20"),
            tvl_zscore_90d=Decimal("-1.5"),
        )
        self.assertFalse(classifier_fires(row))

    def test_only_one_condition_below_default_does_not_fire(self) -> None:
        # Only the z-score is extreme; 30d change and drawdown stay benign.
        row = self._row(
            tvl_change_30d_pct=Decimal("0"),
            tvl_drawdown_from_peak_90d_pct=Decimal("5"),
            tvl_zscore_90d=Decimal("-3"),
        )
        self.assertFalse(classifier_fires(row))

    def test_threshold_overrides_relax_the_rule(self) -> None:
        # Mildly stressed inputs that wouldn't fire under defaults.
        row = self._row(
            tvl_change_30d_pct=Decimal("-3"),
            tvl_drawdown_from_peak_90d_pct=Decimal("8"),
            tvl_zscore_90d=Decimal("-0.3"),
        )
        self.assertFalse(classifier_fires(row))
        # Same row, relaxed thresholds → fires.
        self.assertTrue(
            classifier_fires(
                row,
                tvl_change_30d_threshold_pct=Decimal("0"),
                tvl_drawdown_threshold_pct=Decimal("5"),
                tvl_zscore_threshold=Decimal("0"),
            )
        )


class EvaluateTests(unittest.TestCase):
    def _row(
        self,
        ts: date,
        *,
        forward_drawdown: Decimal | None,
        fires: bool,
    ) -> FeatureRow:
        """Build a row that fires the rule iff `fires=True`."""
        if fires:
            return FeatureRow(
                ts=ts,
                tvl_usd=Decimal("100"),
                price_usd=Decimal("100"),
                tvl_change_7d_pct=Decimal("-5"),
                tvl_change_30d_pct=Decimal("-15"),
                tvl_change_90d_pct=Decimal("-25"),
                tvl_drawdown_from_peak_90d_pct=Decimal("20"),
                tvl_zscore_90d=Decimal("-1.5"),
                forward_drawdown_pct=forward_drawdown,
            )
        return FeatureRow(
            ts=ts,
            tvl_usd=Decimal("100"),
            price_usd=Decimal("100"),
            tvl_change_7d_pct=Decimal("0"),
            tvl_change_30d_pct=Decimal("0"),
            tvl_change_90d_pct=Decimal("0"),
            tvl_drawdown_from_peak_90d_pct=Decimal("0"),
            tvl_zscore_90d=Decimal("0"),
            forward_drawdown_pct=forward_drawdown,
        )

    def test_perfect_classifier_yields_100_precision_and_recall(self) -> None:
        # 5 firing rows, all with forward drawdown > 15%.
        # 5 quiet rows, none with forward drawdown > 15%.
        rows = []
        for i in range(5):
            rows.append(self._row(date(2024, 1, 1 + i), forward_drawdown=Decimal("25"), fires=True))
        for i in range(5):
            rows.append(self._row(date(2024, 1, 6 + i), forward_drawdown=Decimal("5"), fires=False))
        r = evaluate(
            rows,
            chain="Test",
            product="TST-USD",
            period_start=date(2024, 1, 1),
            period_end=date(2024, 1, 31),
        )
        self.assertEqual(r.days_evaluated, 10)
        self.assertEqual(r.true_positives, 5)
        self.assertEqual(r.false_positives, 0)
        self.assertEqual(r.true_negatives, 5)
        self.assertEqual(r.false_negatives, 0)
        self.assertEqual(r.precision_pct, Decimal("100"))
        self.assertEqual(r.recall_pct, Decimal("100"))

    def test_lift_above_one_when_classifier_beats_base_rate(self) -> None:
        # 4 firing rows, 3 are true positives (precision 75%).
        # 12 quiet rows, of which 3 had forward drawdowns (so 6/16 days
        # had drawdowns → base rate 37.5%).
        rows = []
        rows.append(self._row(date(2024, 1, 1), forward_drawdown=Decimal("25"), fires=True))
        rows.append(self._row(date(2024, 1, 2), forward_drawdown=Decimal("25"), fires=True))
        rows.append(self._row(date(2024, 1, 3), forward_drawdown=Decimal("25"), fires=True))
        rows.append(self._row(date(2024, 1, 4), forward_drawdown=Decimal("5"), fires=True))
        for i in range(9):
            rows.append(self._row(date(2024, 2, 1 + i), forward_drawdown=Decimal("5"), fires=False))
        for i in range(3):
            rows.append(self._row(date(2024, 3, 1 + i), forward_drawdown=Decimal("25"), fires=False))
        r = evaluate(
            rows,
            chain="Test",
            product="TST-USD",
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
        )
        self.assertEqual(r.days_evaluated, 16)
        self.assertEqual(r.precision_pct, Decimal("75"))
        self.assertEqual(r.base_rate_pct, Decimal("37.5"))
        self.assertGreater(r.lift, Decimal("1"))

    def test_empty_period_returns_zero_result(self) -> None:
        r = evaluate(
            [],
            chain="Test",
            product="TST-USD",
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
        )
        self.assertEqual(r.days_evaluated, 0)
        self.assertEqual(r.precision_pct, Decimal("0"))
        self.assertEqual(r.recall_pct, Decimal("0"))

    def test_rows_outside_period_are_excluded(self) -> None:
        # Row in Dec 2023 (before period_start) shouldn't count.
        rows = [
            self._row(date(2023, 12, 15), forward_drawdown=Decimal("25"), fires=True),
            self._row(date(2024, 6, 15), forward_drawdown=Decimal("25"), fires=True),
            self._row(date(2024, 6, 16), forward_drawdown=Decimal("5"), fires=False),
        ]
        r = evaluate(
            rows,
            chain="Test",
            product="TST-USD",
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
        )
        # Only the 2024 rows are evaluable.
        self.assertEqual(r.days_evaluated, 2)

    def test_rows_with_missing_forward_label_are_excluded(self) -> None:
        # End-of-series rows (no forward window) should be dropped.
        rows = [
            self._row(date(2024, 1, 1), forward_drawdown=Decimal("25"), fires=True),
            self._row(date(2024, 1, 2), forward_drawdown=None, fires=False),
        ]
        r = evaluate(
            rows,
            chain="Test",
            product="TST-USD",
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
        )
        self.assertEqual(r.days_evaluated, 1)


if __name__ == "__main__":
    unittest.main()
