"""Unit tests for the macro-regime → signal_events emitter (B-096).

Pure tests only — transition detection and direction mapping carry the
logic; the DB-touching ``emit_regime_transitions`` is exercised via the
emitter integration path, so this module stays offline + deterministic.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, time, timezone
from decimal import Decimal

from genkei.experiments.emitters.macro_regime_emitter import (
    ASSET_CLASS,
    EMITTER_SOURCE,
    SENTINEL_ASSET,
    _build_event,
    detect_transitions,
    direction_for_regime,
)
from genkei.experiments.macro_regime import RegimeResult


def _regime(d: date, label: str, *, horizon: str = "macro:cross-sleeve:primary") -> RegimeResult:
    return RegimeResult(
        ts=d,
        regime=label,
        horizon=horizon,
        available_inputs=4,
        dgs10=Decimal("4.20"),
        dgs10_30d_change=Decimal("0.10"),
        hy_oas=Decimal("3.20"),
        hy_oas_30d_change=Decimal("-0.05"),
        vix=Decimal("15.0"),
        usd_index=Decimal("103.0"),
        usd_index_30d_change=Decimal("-0.40"),
    )


class DirectionMappingTests(unittest.TestCase):
    def test_risk_on_and_easing_are_bullish(self) -> None:
        self.assertEqual(direction_for_regime("risk_on"), "bullish")
        self.assertEqual(direction_for_regime("easing"), "bullish")

    def test_risk_off_and_tightening_stress_are_bearish(self) -> None:
        self.assertEqual(direction_for_regime("risk_off"), "bearish")
        self.assertEqual(direction_for_regime("tightening_stress"), "bearish")

    def test_mixed_is_neutral(self) -> None:
        self.assertEqual(direction_for_regime("mixed"), "neutral")

    def test_unknown_label_defaults_neutral(self) -> None:
        self.assertEqual(direction_for_regime("something_new"), "neutral")


class TransitionDetectionTests(unittest.TestCase):
    def test_empty_series_yields_no_transitions(self) -> None:
        self.assertEqual(detect_transitions([]), [])

    def test_single_row_is_never_a_transition(self) -> None:
        self.assertEqual(detect_transitions([_regime(date(2026, 6, 1), "risk_on")]), [])

    def test_same_regime_run_emits_nothing(self) -> None:
        rows = [
            _regime(date(2026, 6, 1), "risk_on"),
            _regime(date(2026, 6, 2), "risk_on"),
            _regime(date(2026, 6, 3), "risk_on"),
        ]
        self.assertEqual(detect_transitions(rows), [])

    def test_one_transition_per_boundary(self) -> None:
        rows = [
            _regime(date(2026, 6, 1), "risk_on"),
            _regime(date(2026, 6, 2), "risk_on"),
            _regime(date(2026, 6, 3), "risk_off"),  # boundary 1
            _regime(date(2026, 6, 4), "risk_off"),
            _regime(date(2026, 6, 5), "easing"),  # boundary 2
        ]
        transitions = detect_transitions(rows)
        self.assertEqual(len(transitions), 2)
        self.assertEqual(
            [(t.ts, t.from_regime, t.to_regime) for t in transitions],
            [
                (date(2026, 6, 3), "risk_on", "risk_off"),
                (date(2026, 6, 5), "risk_off", "easing"),
            ],
        )

    def test_transition_fires_on_the_later_day(self) -> None:
        rows = [
            _regime(date(2026, 6, 10), "mixed"),
            _regime(date(2026, 6, 11), "risk_on"),
        ]
        transitions = detect_transitions(rows)
        self.assertEqual(len(transitions), 1)
        self.assertEqual(transitions[0].ts, date(2026, 6, 11))


class BuildEventTests(unittest.TestCase):
    def _transition(self, frm: str, to: str):
        rows = [_regime(date(2026, 6, 1), frm), _regime(date(2026, 6, 2), to)]
        (transition,) = detect_transitions(rows)
        return transition

    def test_event_envelope_fields(self) -> None:
        ev = _build_event(self._transition("risk_on", "risk_off"))
        self.assertEqual(ev["asset"], SENTINEL_ASSET)
        self.assertEqual(ev["asset_class"], ASSET_CLASS)
        self.assertEqual(ev["source"], EMITTER_SOURCE)
        self.assertEqual(ev["horizon"], "macro:cross-sleeve:primary")
        # signal_kind is the NEW regime so rules can target a specific entry.
        self.assertEqual(ev["signal_kind"], "risk_off")
        self.assertEqual(ev["direction"], "bearish")
        # No natural strength axis for a regime label.
        self.assertIsNone(ev["strength"])

    def test_ts_is_utc_midnight_of_boundary_date(self) -> None:
        ev = _build_event(self._transition("risk_off", "risk_on"))
        self.assertEqual(
            ev["ts"], datetime.combine(date(2026, 6, 2), time(0, 0, tzinfo=timezone.utc))
        )

    def test_source_ref_is_date_and_new_regime_for_idempotency(self) -> None:
        ev = _build_event(self._transition("mixed", "easing"))
        self.assertEqual(ev["source_ref"], "2026-06-02:easing")

    def test_payload_carries_from_and_to_and_metric_snapshot(self) -> None:
        ev = _build_event(self._transition("risk_on", "mixed"))
        self.assertEqual(ev["payload"]["from_regime"], "risk_on")
        self.assertEqual(ev["payload"]["to_regime"], "mixed")
        self.assertEqual(ev["payload"]["vix"], "15.0")
        self.assertEqual(ev["payload"]["available_inputs"], 4)
        # mixed -> neutral direction
        self.assertEqual(ev["direction"], "neutral")


if __name__ == "__main__":
    unittest.main()
