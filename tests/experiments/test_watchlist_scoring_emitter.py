"""Unit tests for the watchlist-scoring band-entry emitter (B-097)."""

from __future__ import annotations

import unittest
from datetime import date, datetime, time, timezone
from decimal import Decimal

from genkei.experiments.emitters.watchlist_scoring_emitter import (
    STATE_BEARISH,
    STATE_BULLISH,
    STATE_NEUTRAL,
    BandEntry,
    ScorePoint,
    _build_event,
    _horizon_for_sleeve,
    _next_state,
    _strength_from_score,
    detect_band_entries,
)


def _pt(day: int, score: str, *, asset: str = "AAPL", sleeve: str = "equity-core",
        asset_class: str = "equity") -> ScorePoint:
    return ScorePoint(
        asset=asset,
        asset_class=asset_class,
        sleeve=sleeve,
        ts=date(2026, 6, day),
        score=Decimal(score),
    )


class NextStateHysteresisTests(unittest.TestCase):
    def test_neutral_enters_bands_on_full_threshold(self):
        self.assertEqual(_next_state(STATE_NEUTRAL, Decimal("4")), STATE_BULLISH)
        self.assertEqual(_next_state(STATE_NEUTRAL, Decimal("-4")), STATE_BEARISH)
        self.assertEqual(_next_state(STATE_NEUTRAL, Decimal("3")), STATE_NEUTRAL)
        self.assertEqual(_next_state(STATE_NEUTRAL, Decimal("-3")), STATE_NEUTRAL)

    def test_bullish_holds_through_dead_band(self):
        # +3 is below the +4 enter but above the +2 exit → stays bullish.
        self.assertEqual(_next_state(STATE_BULLISH, Decimal("3")), STATE_BULLISH)
        self.assertEqual(_next_state(STATE_BULLISH, Decimal("2")), STATE_BULLISH)

    def test_bullish_exits_below_exit_threshold(self):
        self.assertEqual(_next_state(STATE_BULLISH, Decimal("1")), STATE_NEUTRAL)

    def test_bearish_holds_through_dead_band(self):
        self.assertEqual(_next_state(STATE_BEARISH, Decimal("-3")), STATE_BEARISH)
        self.assertEqual(_next_state(STATE_BEARISH, Decimal("-2")), STATE_BEARISH)

    def test_bearish_exits_above_exit_threshold(self):
        self.assertEqual(_next_state(STATE_BEARISH, Decimal("-1")), STATE_NEUTRAL)

    def test_band_to_band_jump(self):
        self.assertEqual(_next_state(STATE_BULLISH, Decimal("-5")), STATE_BEARISH)
        self.assertEqual(_next_state(STATE_BEARISH, Decimal("5")), STATE_BULLISH)


class DetectBandEntriesTests(unittest.TestCase):
    def test_empty_series(self):
        self.assertEqual(detect_band_entries([]), [])

    def test_first_day_in_band_seeds_without_emitting(self):
        # No prior day to cross from → the opening observation only seeds.
        entries = detect_band_entries([_pt(1, "5"), _pt(2, "5")])
        self.assertEqual(entries, [])

    def test_entry_on_transition_into_bullish(self):
        entries = detect_band_entries([_pt(1, "2"), _pt(2, "5")])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].kind, "bullish_band_entry")
        self.assertEqual(entries[0].ts, date(2026, 6, 2))
        self.assertEqual(entries[0].from_state, STATE_NEUTRAL)

    def test_oscillation_in_dead_band_emits_once(self):
        # 5 → 3 → 5: enters bullish once, holds through the dead-band at 3,
        # does NOT re-emit. This is the hysteresis acceptance criterion.
        entries = detect_band_entries([_pt(1, "2"), _pt(2, "5"), _pt(3, "3"), _pt(4, "5")])
        self.assertEqual(len(entries), 1)

    def test_genuine_reentry_after_exiting_band_emits_again(self):
        # 5 → 1 (exits to neutral) → 5: two legitimate entries.
        entries = detect_band_entries([_pt(1, "2"), _pt(2, "5"), _pt(3, "1"), _pt(4, "5")])
        self.assertEqual(len(entries), 2)
        self.assertEqual([e.kind for e in entries], ["bullish_band_entry"] * 2)

    def test_transition_to_neutral_is_silent(self):
        entries = detect_band_entries([_pt(1, "5"), _pt(2, "0")])
        self.assertEqual(entries, [])

    def test_bearish_entry(self):
        entries = detect_band_entries([_pt(1, "0"), _pt(2, "-4")])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].kind, "bearish_band_entry")

    def test_band_to_band_jump_emits_new_band(self):
        entries = detect_band_entries([_pt(1, "2"), _pt(2, "5"), _pt(3, "-5")])
        self.assertEqual([e.kind for e in entries], ["bullish_band_entry", "bearish_band_entry"])

    def test_unsorted_input_is_sorted_by_ts(self):
        entries = detect_band_entries([_pt(2, "5"), _pt(1, "2")])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].ts, date(2026, 6, 2))


class BuildEventTests(unittest.TestCase):
    def _entry(self, kind: str, score: str, *, sleeve: str = "equity-core",
               asset_class: str = "equity", asset: str = "AAPL") -> BandEntry:
        return BandEntry(
            asset=asset,
            asset_class=asset_class,
            sleeve=sleeve,
            ts=date(2026, 6, 2),
            kind=kind,
            score=Decimal(score),
            from_state=STATE_NEUTRAL,
        )

    def test_bullish_envelope(self):
        ev = _build_event(self._entry("bullish_band_entry", "4"), rubric_version="v1")
        self.assertEqual(ev["asset"], "AAPL")
        self.assertEqual(ev["asset_class"], "equity")
        self.assertEqual(ev["horizon"], "equity:core")
        self.assertEqual(ev["source"], "watchlist_scoring")
        self.assertEqual(ev["signal_kind"], "bullish_band_entry")
        self.assertEqual(ev["direction"], "bullish")
        self.assertEqual(ev["strength"], Decimal("0.5"))  # 4 / 8
        self.assertEqual(ev["source_ref"], "AAPL:v1:bullish_band_entry:2026-06-02")
        self.assertEqual(
            ev["ts"], datetime.combine(date(2026, 6, 2), time(0, 0, tzinfo=timezone.utc))
        )

    def test_bearish_direction_and_payload(self):
        ev = _build_event(
            self._entry("bearish_band_entry", "-6", asset="solana",
                        asset_class="crypto", sleeve="crypto-tactical"),
            rubric_version="v1",
        )
        self.assertEqual(ev["direction"], "bearish")
        self.assertEqual(ev["horizon"], "crypto:tactical")
        self.assertEqual(ev["strength"], Decimal("0.75"))  # 6 / 8
        self.assertEqual(ev["payload"]["from_state"], STATE_NEUTRAL)
        self.assertEqual(ev["payload"]["composite_score"], "-6")


class HelperTests(unittest.TestCase):
    def test_horizon_for_sleeve(self):
        self.assertEqual(_horizon_for_sleeve("equity-core"), "equity:core")
        self.assertEqual(_horizon_for_sleeve("crypto-core"), "crypto:core")
        self.assertEqual(_horizon_for_sleeve("crypto-tactical"), "crypto:tactical")

    def test_strength_saturates_at_one(self):
        self.assertEqual(_strength_from_score(Decimal("8")), Decimal("1"))
        self.assertEqual(_strength_from_score(Decimal("12")), Decimal("1"))
        self.assertEqual(_strength_from_score(Decimal("-8")), Decimal("1"))


if __name__ == "__main__":
    unittest.main()
