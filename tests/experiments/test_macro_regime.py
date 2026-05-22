"""Unit tests for the macro regime classifier (B-059).

Tests are scoped to the pure ``classify`` function — no DB. The
SQL-vs-Python parity check (which would need a live DB) is exercised
separately by the live-smoke during development; not in CI.
"""

import unittest
from datetime import date
from decimal import Decimal

from genkei.experiments.macro_regime import (
    REGIME_LABELS,
    RegimeInputs,
    classify,
    summarize,
)


def _inputs(**overrides) -> RegimeInputs:
    """Build a RegimeInputs with sensible defaults; override what matters."""
    base = {
        "ts": date(2024, 6, 1),
        "dgs10": Decimal("4.0"),
        "hy_oas": Decimal("4.0"),
        "vix": Decimal("20"),
        "usd_index": Decimal("100"),
        "dgs10_30d_ago": Decimal("4.0"),
        "hy_oas_30d_ago": Decimal("4.0"),
        "usd_index_30d_ago": Decimal("100"),
    }
    base.update(overrides)
    return RegimeInputs(**base)


class RiskOffTests(unittest.TestCase):
    def test_high_vix_alone_fires_risk_off(self) -> None:
        r = classify(_inputs(vix=Decimal("30")))
        self.assertEqual(r.regime, "risk_off")

    def test_wide_hy_alone_fires_risk_off(self) -> None:
        r = classify(_inputs(hy_oas=Decimal("6.0")))
        self.assertEqual(r.regime, "risk_off")

    def test_lehman_window_october_2008(self) -> None:
        # 2008-10-15 actuals: VIX 69.25, no HY (pre-2023), DGS10 3.99,
        # 30d change +0.52. Should still fire risk_off via VIX alone.
        r = classify(
            _inputs(
                ts=date(2008, 10, 15),
                dgs10=Decimal("3.99"),
                dgs10_30d_ago=Decimal("3.47"),
                hy_oas=None,
                hy_oas_30d_ago=None,
                vix=Decimal("69.25"),
                usd_index=Decimal("110"),
                usd_index_30d_ago=Decimal("108"),
            )
        )
        # 3 of 4 inputs (no HY); VIX > 25 → risk_off.
        self.assertEqual(r.regime, "risk_off")
        self.assertEqual(r.available_inputs, 3)


class TighteningStressTests(unittest.TestCase):
    def test_rates_up_credit_widening_vol_elevated(self) -> None:
        # The worst regime — needs all three to fire.
        r = classify(
            _inputs(
                dgs10=Decimal("4.5"),
                dgs10_30d_ago=Decimal("4.0"),  # +0.5 > 0.3
                hy_oas=Decimal("5.5"),
                hy_oas_30d_ago=Decimal("4.5"),  # +1.0 > 0.3
                vix=Decimal("30"),  # > 25
            )
        )
        self.assertEqual(r.regime, "tightening_stress")

    def test_priority_over_risk_off(self) -> None:
        # When all of tightening_stress's conditions hold, it wins
        # over the simpler risk_off that would otherwise apply.
        r = classify(
            _inputs(
                dgs10=Decimal("4.5"),
                dgs10_30d_ago=Decimal("4.0"),
                hy_oas=Decimal("5.5"),  # would also fire risk_off
                hy_oas_30d_ago=Decimal("4.5"),
                vix=Decimal("30"),  # would also fire risk_off
            )
        )
        # tightening_stress is priority 1, risk_off is priority 2.
        self.assertEqual(r.regime, "tightening_stress")


class EasingTests(unittest.TestCase):
    def test_significant_rate_drop_fires_easing(self) -> None:
        # > 0.5pp drop over 30d → easing.
        r = classify(
            _inputs(
                dgs10=Decimal("3.5"),
                dgs10_30d_ago=Decimal("4.2"),  # -0.7
            )
        )
        self.assertEqual(r.regime, "easing")

    def test_moderate_rate_drop_does_not_fire_easing(self) -> None:
        # -0.4pp is not enough for easing (threshold is -0.5).
        r = classify(
            _inputs(
                dgs10=Decimal("3.8"),
                dgs10_30d_ago=Decimal("4.2"),  # -0.4
            )
        )
        self.assertNotEqual(r.regime, "easing")

    def test_easing_loses_to_risk_off(self) -> None:
        # Easing condition holds AND VIX is elevated. risk_off wins.
        r = classify(
            _inputs(
                dgs10=Decimal("3.5"),
                dgs10_30d_ago=Decimal("4.2"),
                vix=Decimal("30"),
            )
        )
        self.assertEqual(r.regime, "risk_off")


class RiskOnTests(unittest.TestCase):
    def test_two_bullish_inputs_fire_risk_on(self) -> None:
        # HY tight + VIX benign = 2 inputs → risk_on.
        r = classify(
            _inputs(
                hy_oas=Decimal("3.0"),  # tight
                vix=Decimal("15"),  # benign
            )
        )
        self.assertEqual(r.regime, "risk_on")

    def test_one_bullish_input_falls_through_to_mixed(self) -> None:
        # Only HY tight, nothing else bull-leaning.
        r = classify(_inputs(hy_oas=Decimal("3.0")))
        self.assertEqual(r.regime, "mixed")

    def test_usd_weakening_counts(self) -> None:
        # HY tight + USD weakening (-1.5 over 30d) = 2 inputs → risk_on.
        r = classify(
            _inputs(
                hy_oas=Decimal("3.0"),
                usd_index=Decimal("98.5"),
                usd_index_30d_ago=Decimal("100"),  # -1.5
            )
        )
        self.assertEqual(r.regime, "risk_on")


class MixedTests(unittest.TestCase):
    def test_truly_average_market_is_mixed(self) -> None:
        # The defaults: HY 4.0, VIX 20, USD flat. None of the bull or
        # bear thresholds fire.
        r = classify(_inputs())
        self.assertEqual(r.regime, "mixed")


class AvailableInputDegradationTests(unittest.TestCase):
    def test_only_one_input_degrades_to_mixed(self) -> None:
        # Pre-1990: only DGS10. Classifier shouldn't extrapolate.
        r = classify(
            _inputs(
                hy_oas=None,
                hy_oas_30d_ago=None,
                vix=None,
                usd_index=None,
                usd_index_30d_ago=None,
            )
        )
        self.assertEqual(r.regime, "mixed")
        self.assertEqual(r.available_inputs, 1)

    def test_two_inputs_still_degrades_to_mixed(self) -> None:
        # The 3-of-4 threshold is a deliberate honest-output rule.
        r = classify(
            _inputs(
                vix=Decimal("30"),  # would normally fire risk_off
                hy_oas=None,
                hy_oas_30d_ago=None,
                usd_index=None,
                usd_index_30d_ago=None,
            )
        )
        self.assertEqual(r.regime, "mixed")
        self.assertEqual(r.available_inputs, 2)

    def test_three_inputs_enables_classification(self) -> None:
        # 3-of-4 is the minimum. Missing HY (pre-2023) is the common
        # case for historical analysis.
        r = classify(
            _inputs(
                vix=Decimal("30"),
                hy_oas=None,
                hy_oas_30d_ago=None,
            )
        )
        self.assertEqual(r.regime, "risk_off")
        self.assertEqual(r.available_inputs, 3)


class SummarizeTests(unittest.TestCase):
    def test_returns_all_labels_even_when_zero(self) -> None:
        from genkei.experiments.macro_regime import RegimeResult

        results = [
            RegimeResult(
                ts=date(2024, 1, 1),
                regime="risk_off",
                available_inputs=4,
                dgs10=None,
                dgs10_30d_change=None,
                hy_oas=None,
                hy_oas_30d_change=None,
                vix=None,
                usd_index=None,
                usd_index_30d_change=None,
            )
        ]
        counts = summarize(results)
        # Every known label should appear, including zero-counts.
        self.assertEqual(set(counts.keys()), set(REGIME_LABELS))
        self.assertEqual(counts["risk_off"], 1)
        self.assertEqual(counts["risk_on"], 0)
        self.assertEqual(counts["mixed"], 0)

    def test_empty_input_yields_all_zeros(self) -> None:
        counts = summarize([])
        self.assertEqual(set(counts.keys()), set(REGIME_LABELS))
        self.assertTrue(all(v == 0 for v in counts.values()))


class RegimeLabelsContractTests(unittest.TestCase):
    """Pin the regime label set so a silent change forces a deliberate update.

    Every consumer that depends on these labels (B-065 v2 macro
    component, B-066 CLI surfacing) is keyed on these strings. Adding
    or renaming one should be a deliberate, reviewed change.
    """

    def test_label_set_is_pinned(self) -> None:
        self.assertEqual(
            set(REGIME_LABELS),
            {"tightening_stress", "risk_off", "easing", "risk_on", "mixed"},
        )

    def test_classify_only_emits_known_labels(self) -> None:
        # Exhaustive over a small grid — any regime the classifier
        # can emit must be in REGIME_LABELS.
        for vix in (Decimal("10"), Decimal("20"), Decimal("30")):
            for hy in (Decimal("2.5"), Decimal("4.0"), Decimal("6.0")):
                for dgs10_chg in (Decimal("-1"), Decimal("0"), Decimal("1")):
                    r = classify(
                        _inputs(
                            vix=vix,
                            hy_oas=hy,
                            dgs10=Decimal("4") + dgs10_chg,
                            dgs10_30d_ago=Decimal("4"),
                        )
                    )
                    self.assertIn(r.regime, REGIME_LABELS)


if __name__ == "__main__":
    unittest.main()
