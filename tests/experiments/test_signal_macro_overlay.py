"""Unit tests for the macro-regime overlay context (B-096 follow-up)."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from genkei.experiments.macro_regime import RegimeResult
from genkei.experiments.signal_macro_overlay import (
    ALIGNMENT_CONTRADICTS,
    ALIGNMENT_CORROBORATES,
    ALIGNMENT_NEUTRAL,
    ALIGNMENT_UNKNOWN,
    classify_alignment,
    compute_stack_macro_contexts,
)
from genkei.experiments.signal_store import Stack


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


_DEFAULT_WINDOW_START = _utc(2026, 6, 1)
_DEFAULT_WINDOW_END = _utc(2026, 6, 10)


def _stack(
    *,
    asset: str = "AAPL",
    asset_class: str = "equity",
    direction: str = "bearish",
    window_start: datetime = _DEFAULT_WINDOW_START,
    window_end: datetime = _DEFAULT_WINDOW_END,
    horizon: str = "equity:core",
) -> Stack:
    return Stack(
        rule_name="broad_exit",
        asset=asset,
        asset_class=asset_class,
        direction=direction,
        window_start=window_start,
        window_end=window_end,
        score=Decimal("2.0"),
        distinct_sources=2,
        event_count=2,
        horizon=horizon,
        events=[],
    )


def _regime(d: date, label: str) -> RegimeResult:
    return RegimeResult(
        ts=d,
        regime=label,
        horizon="macro:cross-sleeve:primary",
        available_inputs=4,
        dgs10=Decimal("4.2"),
        dgs10_30d_change=Decimal("0.1"),
        hy_oas=Decimal("3.2"),
        hy_oas_30d_change=Decimal("-0.05"),
        vix=Decimal("15.0"),
        usd_index=Decimal("103.0"),
        usd_index_30d_change=Decimal("-0.4"),
    )


class ClassifyAlignmentTests(unittest.TestCase):
    def test_bullish_stack_in_bullish_regime_corroborates(self) -> None:
        self.assertEqual(classify_alignment("risk_on", "bullish"), ALIGNMENT_CORROBORATES)
        self.assertEqual(classify_alignment("easing", "bullish"), ALIGNMENT_CORROBORATES)

    def test_bearish_stack_in_bearish_regime_corroborates(self) -> None:
        self.assertEqual(classify_alignment("risk_off", "bearish"), ALIGNMENT_CORROBORATES)
        self.assertEqual(
            classify_alignment("tightening_stress", "bearish"), ALIGNMENT_CORROBORATES
        )

    def test_opposing_bias_contradicts(self) -> None:
        self.assertEqual(classify_alignment("risk_on", "bearish"), ALIGNMENT_CONTRADICTS)
        self.assertEqual(classify_alignment("risk_off", "bullish"), ALIGNMENT_CONTRADICTS)

    def test_mixed_regime_is_neutral(self) -> None:
        self.assertEqual(classify_alignment("mixed", "bullish"), ALIGNMENT_NEUTRAL)
        self.assertEqual(classify_alignment("mixed", "bearish"), ALIGNMENT_NEUTRAL)

    def test_neutral_stack_is_neutral_regardless_of_regime(self) -> None:
        self.assertEqual(classify_alignment("risk_on", "neutral"), ALIGNMENT_NEUTRAL)
        self.assertEqual(classify_alignment("risk_off", "neutral"), ALIGNMENT_NEUTRAL)

    def test_no_regime_is_unknown(self) -> None:
        self.assertEqual(classify_alignment(None, "bullish"), ALIGNMENT_UNKNOWN)


class ComputeStackMacroContextsTests(unittest.TestCase):
    def test_empty_stacks_returns_empty(self) -> None:
        self.assertEqual(compute_stack_macro_contexts([]), [])

    def test_uses_regime_in_effect_at_window_end(self) -> None:
        # Regime flips to risk_off on 06-08; a stack ending 06-10 should pick
        # up risk_off (the latest row on-or-before window_end), not the
        # earlier risk_on.
        regimes = [
            _regime(date(2026, 6, 1), "risk_on"),
            _regime(date(2026, 6, 8), "risk_off"),
        ]
        stack = _stack(direction="bearish", window_end=_utc(2026, 6, 10))
        with patch(
            "genkei.experiments.signal_macro_overlay.load_regimes",
            return_value=regimes,
        ):
            (ctx,) = compute_stack_macro_contexts([stack])
        self.assertEqual(ctx.regime, "risk_off")
        self.assertEqual(ctx.as_of, date(2026, 6, 8))
        self.assertEqual(ctx.alignment, ALIGNMENT_CORROBORATES)

    def test_picks_latest_before_a_later_flip(self) -> None:
        # Stack ends 06-07, before the 06-08 flip → still risk_on.
        regimes = [
            _regime(date(2026, 6, 1), "risk_on"),
            _regime(date(2026, 6, 8), "risk_off"),
        ]
        stack = _stack(direction="bearish", window_end=_utc(2026, 6, 7))
        with patch(
            "genkei.experiments.signal_macro_overlay.load_regimes",
            return_value=regimes,
        ):
            (ctx,) = compute_stack_macro_contexts([stack])
        self.assertEqual(ctx.regime, "risk_on")
        self.assertEqual(ctx.alignment, ALIGNMENT_CONTRADICTS)  # bearish vs risk_on

    def test_no_regime_before_window_is_unknown(self) -> None:
        # Only a regime row AFTER the window → nothing on-or-before → unknown.
        regimes = [_regime(date(2026, 7, 1), "risk_on")]
        stack = _stack(window_end=_utc(2026, 6, 10))
        with patch(
            "genkei.experiments.signal_macro_overlay.load_regimes",
            return_value=regimes,
        ):
            (ctx,) = compute_stack_macro_contexts([stack])
        self.assertIsNone(ctx.regime)
        self.assertEqual(ctx.alignment, ALIGNMENT_UNKNOWN)

    def test_contexts_are_parallel_to_stacks(self) -> None:
        regimes = [_regime(date(2026, 6, 1), "risk_on")]
        stacks = [
            _stack(asset="AAPL", direction="bullish", window_end=_utc(2026, 6, 5)),
            _stack(asset="NVDA", direction="bearish", window_end=_utc(2026, 6, 6)),
        ]
        with patch(
            "genkei.experiments.signal_macro_overlay.load_regimes",
            return_value=regimes,
        ):
            contexts = compute_stack_macro_contexts(stacks)
        self.assertEqual([c.stack_index for c in contexts], [0, 1])
        self.assertEqual(contexts[0].alignment, ALIGNMENT_CORROBORATES)  # bullish/risk_on
        self.assertEqual(contexts[1].alignment, ALIGNMENT_CONTRADICTS)  # bearish/risk_on


if __name__ == "__main__":
    unittest.main()
