"""Unit tests for the 8-K impact → signal_events emitter (B-094)."""

from __future__ import annotations

import unittest
from datetime import date, datetime, time, timezone
from decimal import Decimal
from unittest.mock import patch

from genkei.experiments.eight_k_impact import FilingEvent
from genkei.experiments.emitters.eight_k_emitter import (
    DEFAULT_ITEM_PROFILE,
    ITEM_CODE_PROFILES,
    _build_events,
    _event_ts,
    _profile_for_item,
    _signal_kind_for_item,
    _sleeve_by_ticker,
    emit_recent_filings,
)
from tests.helpers import (
    DEFAULT_EQUITY_WATCHLIST_YAML,
    FakeIngestRun,
    temporary_watchlist_path,
)

WATCHLIST_YAML = DEFAULT_EQUITY_WATCHLIST_YAML


def _sleeve_map() -> dict[str, str]:
    with temporary_watchlist_path(WATCHLIST_YAML) as path:
        return _sleeve_by_ticker(path)


def _filing(
    *,
    ticker: str = "AAPL",
    cik: str = "0000320193",
    filed_at: date = date(2026, 5, 7),
    accession_number: str = "0000320193-26-000042",
    item_codes: tuple[str, ...] = ("2.02",),
    accepted_at: datetime | None = None,
) -> FilingEvent:
    return FilingEvent(
        ticker=ticker,
        cik=cik,
        filed_at=filed_at,
        accession_number=accession_number,
        item_codes=item_codes,
        accepted_at=accepted_at,
    )


class SignalKindNormalizationTests(unittest.TestCase):
    def test_canonical_dotted_code(self) -> None:
        self.assertEqual(_signal_kind_for_item("1.01"), "item_1_01")
        self.assertEqual(_signal_kind_for_item("5.02"), "item_5_02")

    def test_two_digit_major_code(self) -> None:
        # Future-proof: SEC could add 10.01-style items.
        self.assertEqual(_signal_kind_for_item("10.01"), "item_10_01")

    def test_legacy_dotless_code_passes_through(self) -> None:
        # Pre-2009 filings sometimes have bare "5" instead of "5.01" /
        # "5.02" — parse_item_codes preserves them; the emitter normalizes
        # without losing the discriminator.
        self.assertEqual(_signal_kind_for_item("5"), "item_5")


class ProfileForItemTests(unittest.TestCase):
    def test_curated_bullish_codes_match_rules_yaml(self) -> None:
        # The two bullish codes referenced by smart_money_buy.
        direction_101, _ = _profile_for_item("1.01")
        direction_202, _ = _profile_for_item("2.02")
        self.assertEqual(direction_101, "bullish")
        self.assertEqual(direction_202, "bullish")

    def test_curated_bearish_codes_match_rules_yaml(self) -> None:
        # The two bearish codes referenced by deterioration_stack.
        direction_402, strength_402 = _profile_for_item("4.02")
        direction_502, strength_502 = _profile_for_item("5.02")
        self.assertEqual(direction_402, "bearish")
        self.assertEqual(direction_502, "bearish")
        # 4.02 (non-reliance) is the rarest + most consequential code; it
        # should be strictly stronger than 5.02 (officer departures).
        self.assertGreater(strength_402, strength_502)

    def test_routine_codes_are_lower_strength(self) -> None:
        # 9.01 (exhibits) is almost always co-filed with the substantive
        # item — it should be the weakest profile in the table.
        _, strength_901 = _profile_for_item("9.01")
        for code, (_, strength) in ITEM_CODE_PROFILES.items():
            if code == "9.01":
                continue
            self.assertGreaterEqual(strength, strength_901, f"{code} weaker than 9.01")

    def test_unknown_code_falls_through_to_default(self) -> None:
        # An item code we haven't curated should still emit (queryable for
        # future rules) but with the conservative neutral default profile.
        self.assertEqual(_profile_for_item("11.99"), DEFAULT_ITEM_PROFILE)
        direction, _ = _profile_for_item("11.99")
        self.assertEqual(direction, "neutral")

    def test_all_strengths_are_in_unit_interval(self) -> None:
        # meta.signal_events.strength CHECK constraint requires 0 <= s <= 1.
        for code, (direction, strength) in ITEM_CODE_PROFILES.items():
            self.assertIn(direction, {"bullish", "bearish", "neutral"}, code)
            self.assertGreaterEqual(strength, Decimal("0"), code)
            self.assertLessEqual(strength, Decimal("1"), code)


class EventTsTests(unittest.TestCase):
    def test_filed_during_market_hours_uses_filing_date(self) -> None:
        # Filed 2026-05-07 (Thursday) at 10am ET — event_date stays the same
        # trading day, ts is that date at UTC midnight.
        filing = _filing(
            filed_at=date(2026, 5, 7),
            accepted_at=datetime(2026, 5, 7, 14, 0, tzinfo=timezone.utc),  # 10am ET
        )
        self.assertEqual(_event_ts(filing), datetime(2026, 5, 7, tzinfo=timezone.utc))

    def test_after_hours_filing_rolls_to_next_trading_day(self) -> None:
        # Filed 2026-05-07 (Thursday) at 5pm ET — after market close, so
        # event_date rolls to 2026-05-08 (Friday).
        filing = _filing(
            filed_at=date(2026, 5, 7),
            accepted_at=datetime(2026, 5, 7, 21, 30, tzinfo=timezone.utc),  # 5:30pm ET
        )
        self.assertEqual(_event_ts(filing), datetime(2026, 5, 8, tzinfo=timezone.utc))

    def test_friday_after_hours_rolls_past_weekend(self) -> None:
        # Filed Friday 2026-05-08 after close → event_date is Monday 2026-05-11.
        filing = _filing(
            filed_at=date(2026, 5, 8),
            accepted_at=datetime(2026, 5, 8, 21, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(_event_ts(filing), datetime(2026, 5, 11, tzinfo=timezone.utc))


class SleeveByTickerTests(unittest.TestCase):
    def test_maps_each_equity_to_its_sleeve(self) -> None:
        mapping = _sleeve_map()
        self.assertEqual(mapping.get("AAPL"), "core")
        self.assertEqual(mapping.get("NVDA"), "core")


class BuildEventsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sleeves = _sleeve_map()

    def test_builds_canonical_bullish_event_for_2_02(self) -> None:
        filing = _filing(item_codes=("2.02",))
        events = _build_events(filing, sleeve_by_ticker=self.sleeves)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["asset"], "AAPL")
        self.assertEqual(event["asset_class"], "equity")
        self.assertEqual(event["horizon"], "equity:core")
        self.assertEqual(event["source"], "eight_k_impact")
        self.assertEqual(event["signal_kind"], "item_2_02")
        self.assertEqual(event["direction"], "bullish")
        self.assertEqual(event["strength"], Decimal("0.5"))
        self.assertEqual(event["source_ref"], "0000320193-26-000042")
        self.assertEqual(event["payload"]["ticker"], "AAPL")
        self.assertEqual(event["payload"]["cik"], "0000320193")
        self.assertEqual(event["payload"]["item_code"], "2.02")
        self.assertEqual(event["payload"]["all_item_codes"], ["2.02"])
        self.assertEqual(event["payload"]["accession_number"], "0000320193-26-000042")

    def test_builds_bearish_event_for_4_02(self) -> None:
        filing = _filing(item_codes=("4.02",))
        events = _build_events(filing, sleeve_by_ticker=self.sleeves)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["signal_kind"], "item_4_02")
        self.assertEqual(event["direction"], "bearish")
        # 4.02 is the strongest curated code.
        self.assertEqual(event["strength"], Decimal("0.9"))

    def test_multi_item_filing_fans_into_multiple_events(self) -> None:
        # A 2.02 earnings filing typically co-files 9.01 exhibits. Both
        # should emit as distinct events under the same source_ref.
        filing = _filing(item_codes=("2.02", "9.01"))
        events = _build_events(filing, sleeve_by_ticker=self.sleeves)
        self.assertEqual(len(events), 2)
        kinds = [e["signal_kind"] for e in events]
        self.assertEqual(kinds, ["item_2_02", "item_9_01"])
        # Same source_ref (accession_number) — UNIQUE key includes
        # signal_kind so the two events coexist as distinct rows.
        self.assertEqual({e["source_ref"] for e in events}, {"0000320193-26-000042"})
        # Same ts because both items share one filing.
        self.assertEqual({e["ts"] for e in events}, {events[0]["ts"]})
        # Each event carries the same all_item_codes context for inspection.
        self.assertEqual(events[0]["payload"]["all_item_codes"], ["2.02", "9.01"])
        self.assertEqual(events[1]["payload"]["all_item_codes"], ["2.02", "9.01"])

    def test_uncurated_code_uses_neutral_default(self) -> None:
        filing = _filing(item_codes=("11.99",))
        events = _build_events(filing, sleeve_by_ticker=self.sleeves)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["direction"], "neutral")
        self.assertEqual(events[0]["strength"], DEFAULT_ITEM_PROFILE[1])
        self.assertEqual(events[0]["signal_kind"], "item_11_99")

    def test_no_items_returns_empty(self) -> None:
        filing = _filing(item_codes=())
        self.assertEqual(_build_events(filing, sleeve_by_ticker=self.sleeves), [])

    def test_unknown_ticker_falls_back_to_core_sleeve(self) -> None:
        # Defensive: load_filing_events should never produce non-watchlist
        # tickers, but the lookup must not crash if it does.
        filing = _filing(ticker="UNKNOWN")
        events = _build_events(filing, sleeve_by_ticker=self.sleeves)
        self.assertEqual(events[0]["horizon"], "equity:core")

    def test_accepted_at_none_serializes_as_null(self) -> None:
        # Some legacy filings don't have an accepted_at — payload should
        # carry an explicit null rather than crashing on isoformat.
        filing = _filing(accepted_at=None)
        event = _build_events(filing, sleeve_by_ticker=self.sleeves)[0]
        self.assertIsNone(event["payload"]["accepted_at"])

    def test_accepted_at_present_serializes_to_iso(self) -> None:
        accepted = datetime(2026, 5, 7, 21, 30, tzinfo=timezone.utc)
        filing = _filing(accepted_at=accepted)
        event = _build_events(filing, sleeve_by_ticker=self.sleeves)[0]
        self.assertEqual(event["payload"]["accepted_at"], accepted.isoformat())

    def test_event_ts_uses_event_date_not_filed_at(self) -> None:
        # After-hours Friday filing should land Monday at UTC midnight.
        filing = _filing(
            filed_at=date(2026, 5, 8),
            accepted_at=datetime(2026, 5, 8, 21, 30, tzinfo=timezone.utc),
        )
        event = _build_events(filing, sleeve_by_ticker=self.sleeves)[0]
        self.assertEqual(event["ts"], datetime(2026, 5, 11, tzinfo=timezone.utc))
        self.assertEqual(event["ts"].time(), time(0, 0))


class EmitRecentFilingsTests(unittest.TestCase):
    def test_orchestrator_wraps_in_ingest_run_and_skips_no_item_filings(self) -> None:
        good = _filing(item_codes=("2.02",))
        empty = _filing(
            accession_number="0000320193-26-000099",
            item_codes=(),
        )

        with (
            temporary_watchlist_path(WATCHLIST_YAML) as path,
            patch(
                "genkei.experiments.emitters.eight_k_emitter.db.ingest_run",
                return_value=FakeIngestRun(),
            ),
            patch(
                "genkei.experiments.emitters.eight_k_emitter.load_filing_events",
                return_value=[good, empty],
            ) as load_mock,
            patch(
                "genkei.experiments.emitters.eight_k_emitter.emit_signals_bulk",
                return_value=1,
            ) as emit_mock,
        ):
            result = emit_recent_filings(
                since=date(2026, 5, 1),
                until=date(2026, 5, 31),
                config=path,
            )

        self.assertEqual(result.events_emitted, 1)
        self.assertEqual(result.filings_seen, 2)
        self.assertEqual(result.filings_skipped_no_items, 1)
        load_mock.assert_called_once_with(
            since=date(2026, 5, 1),
            until=date(2026, 5, 31),
            config=path,
        )
        # Only the good filing's event was forwarded to emit_signals_bulk.
        emitted = emit_mock.call_args.args[0]
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0]["source_ref"], "0000320193-26-000042")

    def test_multi_item_filing_emits_multiple_rows(self) -> None:
        filing = _filing(item_codes=("2.02", "9.01"))

        with (
            temporary_watchlist_path(WATCHLIST_YAML) as path,
            patch(
                "genkei.experiments.emitters.eight_k_emitter.db.ingest_run",
                return_value=FakeIngestRun(7),
            ),
            patch(
                "genkei.experiments.emitters.eight_k_emitter.load_filing_events",
                return_value=[filing],
            ) as load_mock,
            patch(
                "genkei.experiments.emitters.eight_k_emitter.emit_signals_bulk",
                return_value=2,
            ) as emit_mock,
        ):
            result = emit_recent_filings(config=path)

        self.assertEqual(result.events_emitted, 2)
        self.assertEqual(result.filings_seen, 1)
        self.assertEqual(result.filings_skipped_no_items, 0)
        load_mock.assert_called_once_with(since=None, until=None, config=path)
        emitted = emit_mock.call_args.args[0]
        self.assertEqual([e["signal_kind"] for e in emitted], ["item_2_02", "item_9_01"])


if __name__ == "__main__":
    unittest.main()
