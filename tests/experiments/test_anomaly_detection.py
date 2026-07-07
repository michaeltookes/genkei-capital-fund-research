"""Unit tests for the pure rolling anomaly detector (B-069)."""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from decimal import Decimal

from genkei.experiments.anomaly_detection import (
    Anomaly,
    SeriesPoint,
    detect_anomalies,
    to_returns,
)

_D0 = date(2026, 1, 1)


def _pt(i: int, value: str) -> SeriesPoint:
    return SeriesPoint(ts=_D0 + timedelta(days=i), value=Decimal(value))


def _series(values: list[str]) -> list[SeriesPoint]:
    return [_pt(i, v) for i, v in enumerate(values)]


class ToReturnsTests(unittest.TestCase):
    def test_simple_pct_change(self) -> None:
        pts = _series(["100", "110", "99"])
        rets = to_returns(pts)
        self.assertEqual([r.value for r in rets], [Decimal("0.1"), Decimal("-0.1")])
        # Dates align to the *later* point of each pair.
        self.assertEqual([r.ts for r in rets], [_pt(1, "0").ts, _pt(2, "0").ts])

    def test_non_positive_prior_price_skipped(self) -> None:
        # A zero prior price can't form a return; that pair is dropped.
        pts = _series(["0", "10", "12"])
        rets = to_returns(pts)
        self.assertEqual([r.value for r in rets], [Decimal("0.2")])

    def test_empty_and_singleton(self) -> None:
        self.assertEqual(to_returns([]), [])
        self.assertEqual(to_returns(_series(["100"])), [])


class DetectAnomaliesTests(unittest.TestCase):
    def test_flags_a_clear_spike(self) -> None:
        # 40 tiny alternating returns (a calm window), then one huge one.
        values = ["0.001" if i % 2 == 0 else "-0.001" for i in range(40)]
        values.append("0.20")  # the anomaly
        returns = [_pt(i, v) for i, v in enumerate(values)]
        out = detect_anomalies(returns, min_window=20, window=90, threshold=Decimal("3.5"))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].ts, returns[-1].ts)
        self.assertEqual(out[0].direction, "spike_up")
        self.assertEqual(out[0].method, "modified_zscore")
        self.assertGreater(abs(out[0].score), Decimal("3.5"))

    def test_calm_series_flags_nothing(self) -> None:
        values = ["0.001" if i % 2 == 0 else "-0.001" for i in range(60)]
        out = detect_anomalies(
            [_pt(i, v) for i, v in enumerate(values)],
            min_window=20,
            threshold=Decimal("3.5"),
        )
        self.assertEqual(out, [])

    def test_downward_spike_direction(self) -> None:
        values = ["0.001" if i % 2 == 0 else "-0.001" for i in range(40)]
        values.append("-0.25")
        out = detect_anomalies(
            [_pt(i, v) for i, v in enumerate(values)], min_window=20, threshold=Decimal("3.5")
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].direction, "spike_down")
        self.assertLess(out[0].score, Decimal("0"))

    def test_min_window_gates_early_points(self) -> None:
        # A spike inside the min_window warm-up must not flag (unstable stats).
        values = ["0.001", "0.30"] + ["0.001"] * 40
        out = detect_anomalies(
            [_pt(i, v) for i, v in enumerate(values)], min_window=20, threshold=Decimal("3.5")
        )
        # The early 0.30 (index 1) is gated; nothing after it is anomalous.
        self.assertEqual([a.ts for a in out], [])

    def test_majority_flat_window_uses_zscore_fallback(self) -> None:
        # A strict majority of the window equal the median → MAD == 0, but the
        # minority keep std > 0, so the detector falls back to the classic
        # mean/std z-score (a perfectly flat window has std == 0 and is
        # skipped — covered separately).
        values = ["0.000"] * 30 + ["0.010"] * 10 + ["0.050"]
        pts = [_pt(i, v) for i, v in enumerate(values)]
        out = detect_anomalies(pts, min_window=20, threshold=Decimal("3.5"))
        last = [a for a in out if a.ts == pts[-1].ts]
        self.assertEqual(len(last), 1)
        self.assertEqual(last[0].method, "zscore")
        self.assertIsNone(last[0].mad)
        self.assertIsNone(last[0].median)

    def test_perfectly_flat_series_never_flags(self) -> None:
        # MAD == 0 AND std == 0 → nothing to be anomalous against.
        out = detect_anomalies(
            [_pt(i, "0.01") for i in range(50)], min_window=20, threshold=Decimal("3.5")
        )
        self.assertEqual(out, [])

    def test_window_bounds_the_lookback(self) -> None:
        # With a short window, a point is judged only against its recent past,
        # not the whole series — a regime that shifted long ago doesn't count.
        calm = ["0.001" if i % 2 == 0 else "-0.001" for i in range(10)]
        loud = ["0.05" if i % 2 == 0 else "-0.05" for i in range(40)]
        values = calm + loud
        # window=20 means the last point sees only recent loud returns → normal.
        out = detect_anomalies(
            [_pt(i, v) for i, v in enumerate(values)],
            min_window=10,
            window=20,
            threshold=Decimal("3.5"),
        )
        # The transition from calm→loud may flag once, but the tail (fully
        # inside the loud regime) should not.
        tail_flags = [a for a in out if a.ts >= _pt(30, "0").ts]
        self.assertEqual(tail_flags, [])

    def test_threshold_is_respected(self) -> None:
        values = ["0.001" if i % 2 == 0 else "-0.001" for i in range(40)]
        values.append("0.02")
        pts = [_pt(i, v) for i, v in enumerate(values)]
        strict = detect_anomalies(pts, min_window=20, threshold=Decimal("50"))
        loose = detect_anomalies(pts, min_window=20, threshold=Decimal("3.5"))
        self.assertEqual(strict, [])
        self.assertEqual(len(loose), 1)

    def test_returns_are_anomaly_instances(self) -> None:
        values = ["0.001" if i % 2 == 0 else "-0.001" for i in range(40)] + ["0.20"]
        out = detect_anomalies(
            [_pt(i, v) for i, v in enumerate(values)], min_window=20, threshold=Decimal("3.5")
        )
        self.assertIsInstance(out[0], Anomaly)
        self.assertEqual(out[0].window, 90)
        self.assertEqual(out[0].threshold, Decimal("3.5"))


if __name__ == "__main__":
    unittest.main()
