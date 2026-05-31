"""Unit tests for the 13F crowding → signal_events emitter (B-093)."""

from __future__ import annotations

import unittest
from datetime import date, datetime, time, timezone
from decimal import Decimal
from unittest.mock import patch

from genkei.common.watchlist import EquityEntry
from genkei.experiments.crowding_monitor import CrowdingRow
from genkei.experiments.emitters.crowding_emitter import (
    DEFAULT_JUMP_THRESHOLD,
    STRENGTH_SATURATION_NET_CHANGE,
    _build_events,
    _classify_kinds,
    _equities_by_cusip,
    _period_to_ts,
    _strength_from_net_change,
    emit_recent_crowding,
)
from tests.helpers import (
    DEFAULT_EQUITY_WATCHLIST_YAML,
    FakeIngestRun,
    make_watchlist,
    temporary_watchlist_path,
)

# AAPL's real CUSIP — handy as a stable, recognizable fixture.
AAPL_CUSIP = "037833100"
WATCHLIST_YAML = DEFAULT_EQUITY_WATCHLIST_YAML


def _watchlist_cusip_map() -> dict[str, list[EquityEntry]]:
    return _equities_by_cusip(make_watchlist(WATCHLIST_YAML))


def _row(
    *,
    cusip: str = AAPL_CUSIP,
    period: date = date(2026, 3, 31),
    holder_count: int = 4,
    prior_holder_count: int | None = 1,
    net_change: int | None = 3,
    total_value_usd: Decimal | None = Decimal("250000000"),
) -> CrowdingRow:
    return CrowdingRow(
        period_of_report=period,
        cusip=cusip,
        issuer_name="Apple Inc.",
        holder_count=holder_count,
        holder_ciks=["0000111111", "0000222222"],
        holder_names=["Fund A", "Fund B"],
        total_value_usd=total_value_usd,
        total_shares=Decimal("1000000"),
        prior_holder_count=prior_holder_count,
        new_entrants=["0000222222"],
        exits=[],
        net_change=net_change,
    )


class StrengthRampTests(unittest.TestCase):
    def test_zero_change_is_zero(self) -> None:
        self.assertEqual(_strength_from_net_change(0), Decimal("0"))

    def test_below_saturation_is_proportional(self) -> None:
        self.assertEqual(_strength_from_net_change(1), Decimal("0.25"))
        self.assertEqual(_strength_from_net_change(2), Decimal("0.5"))
        self.assertEqual(_strength_from_net_change(3), Decimal("0.75"))

    def test_saturation_point_hits_one(self) -> None:
        self.assertEqual(
            _strength_from_net_change(int(STRENGTH_SATURATION_NET_CHANGE)),
            Decimal("1"),
        )

    def test_above_saturation_clamps_to_one(self) -> None:
        self.assertEqual(_strength_from_net_change(20), Decimal("1"))

    def test_negative_change_uses_magnitude(self) -> None:
        # Exits scale the same way adds do.
        self.assertEqual(_strength_from_net_change(-2), Decimal("0.5"))
        self.assertEqual(_strength_from_net_change(-8), Decimal("1"))


class ClassifyKindsTests(unittest.TestCase):
    def test_none_change_yields_nothing(self) -> None:
        # First-observed period for a CUSIP — no prior comparison.
        self.assertEqual(_classify_kinds(None, jump_threshold=3), [])

    def test_zero_change_yields_nothing(self) -> None:
        self.assertEqual(_classify_kinds(0, jump_threshold=3), [])

    def test_small_positive_is_add_only(self) -> None:
        self.assertEqual(
            _classify_kinds(2, jump_threshold=3),
            [("crowding_add", "bullish")],
        )

    def test_at_threshold_emits_add_and_jump(self) -> None:
        self.assertEqual(
            _classify_kinds(3, jump_threshold=3),
            [("crowding_add", "bullish"), ("crowding_jump", "bullish")],
        )

    def test_above_threshold_emits_add_and_jump(self) -> None:
        self.assertEqual(
            _classify_kinds(5, jump_threshold=3),
            [("crowding_add", "bullish"), ("crowding_jump", "bullish")],
        )

    def test_negative_is_exit(self) -> None:
        self.assertEqual(
            _classify_kinds(-2, jump_threshold=3),
            [("crowding_exit", "bearish")],
        )

    def test_jump_threshold_is_configurable(self) -> None:
        # A +3 row with a higher threshold is an add but not a jump.
        self.assertEqual(
            _classify_kinds(3, jump_threshold=5),
            [("crowding_add", "bullish")],
        )


class PeriodToTsTests(unittest.TestCase):
    def test_converts_to_utc_midnight(self) -> None:
        result = _period_to_ts(date(2026, 3, 31))
        self.assertEqual(result, datetime(2026, 3, 31, tzinfo=timezone.utc))
        self.assertEqual(result.time(), time(0, 0))


class EquitiesByCusipTests(unittest.TestCase):
    def test_maps_cusip_to_equity(self) -> None:
        mapping = _watchlist_cusip_map()
        self.assertIn(AAPL_CUSIP, mapping)
        self.assertEqual([e.symbol for e in mapping[AAPL_CUSIP]], ["AAPL"])

    def test_skips_equities_without_cusip(self) -> None:
        yaml_text = (
            "equities:\n"
            "  primary:\n"
            "    - symbol: NVDA\n"
            "      cik: \"0001045810\"\n"
            "      name: NVIDIA\n"  # no cusip
        )
        watchlist = make_watchlist(yaml_text)
        self.assertEqual(_equities_by_cusip(watchlist), {})


class BuildEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cusip_map = _watchlist_cusip_map()

    def test_builds_canonical_add_event(self) -> None:
        # net_change=2 → add only (below the default jump threshold of 3).
        row = _row(net_change=2, holder_count=3, prior_holder_count=1)
        events = _build_events(
            row, self.cusip_map, jump_threshold=DEFAULT_JUMP_THRESHOLD
        )
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["asset"], "AAPL")
        self.assertEqual(event["asset_class"], "equity")
        self.assertEqual(event["horizon"], "equity:core")
        self.assertEqual(event["source"], "crowding")
        self.assertEqual(event["signal_kind"], "crowding_add")
        self.assertEqual(event["direction"], "bullish")
        self.assertEqual(event["strength"], Decimal("0.5"))  # 2/4
        self.assertEqual(event["ts"], datetime(2026, 3, 31, tzinfo=timezone.utc))
        self.assertEqual(event["source_ref"], f"{AAPL_CUSIP}:2026-03-31")
        self.assertEqual(event["payload"]["ticker"], "AAPL")
        self.assertEqual(event["payload"]["net_change"], 2)
        self.assertEqual(event["payload"]["holder_count"], 3)
        self.assertEqual(event["payload"]["total_value_usd"], "250000000")

    def test_jump_emits_both_add_and_jump(self) -> None:
        row = _row(net_change=3)  # at threshold
        events = _build_events(
            row, self.cusip_map, jump_threshold=DEFAULT_JUMP_THRESHOLD
        )
        self.assertEqual(
            [e["signal_kind"] for e in events],
            ["crowding_add", "crowding_jump"],
        )
        # Both share the same source_ref / ts so the UNIQUE key keeps them as
        # two distinct rows (signal_kind differentiates).
        self.assertEqual({e["source_ref"] for e in events}, {f"{AAPL_CUSIP}:2026-03-31"})
        self.assertTrue(all(e["direction"] == "bullish" for e in events))
        self.assertTrue(all(e["strength"] == Decimal("0.75") for e in events))

    def test_exit_is_bearish(self) -> None:
        row = _row(net_change=-2, holder_count=2, prior_holder_count=4)
        events = _build_events(
            row, self.cusip_map, jump_threshold=DEFAULT_JUMP_THRESHOLD
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["signal_kind"], "crowding_exit")
        self.assertEqual(events[0]["direction"], "bearish")
        self.assertEqual(events[0]["strength"], Decimal("0.5"))

    def test_no_delta_returns_empty(self) -> None:
        self.assertEqual(
            _build_events(
                _row(net_change=None, prior_holder_count=None),
                self.cusip_map,
                jump_threshold=DEFAULT_JUMP_THRESHOLD,
            ),
            [],
        )
        self.assertEqual(
            _build_events(
                _row(net_change=0),
                self.cusip_map,
                jump_threshold=DEFAULT_JUMP_THRESHOLD,
            ),
            [],
        )

    def test_non_watchlist_cusip_returns_empty(self) -> None:
        row = _row(cusip="999999999")  # not on the watchlist
        self.assertEqual(
            _build_events(
                row, self.cusip_map, jump_threshold=DEFAULT_JUMP_THRESHOLD
            ),
            [],
        )

    def test_null_total_value_becomes_null_payload_field(self) -> None:
        row = _row(net_change=2, total_value_usd=None)
        event = _build_events(
            row, self.cusip_map, jump_threshold=DEFAULT_JUMP_THRESHOLD
        )[0]
        self.assertIsNone(event["payload"]["total_value_usd"])

    def test_payload_carries_entrant_and_exit_detail(self) -> None:
        row = _row(net_change=2)
        event = _build_events(
            row, self.cusip_map, jump_threshold=DEFAULT_JUMP_THRESHOLD
        )[0]
        self.assertEqual(event["payload"]["new_entrants"], ["0000222222"])
        self.assertEqual(event["payload"]["exits"], [])
        self.assertEqual(event["payload"]["holder_names"], ["Fund A", "Fund B"])


class EmitRecentCrowdingTests(unittest.TestCase):
    def test_skips_immature_periods_before_bulk_emit(self) -> None:
        with temporary_watchlist_path(WATCHLIST_YAML) as path:
            mature = _row(period=date(2026, 3, 31), net_change=2)
            immature = _row(period=date(2026, 6, 30), net_change=3)
            with (
                patch(
                    "genkei.experiments.emitters.crowding_emitter.db.ingest_run",
                    return_value=FakeIngestRun(),
                ),
                patch(
                    "genkei.experiments.emitters.crowding_emitter.load_positions",
                    return_value=[],
                ),
                patch(
                    "genkei.experiments.emitters.crowding_emitter.compute_crowding",
                    return_value=[mature, immature],
                ),
                patch(
                    "genkei.experiments.emitters.crowding_emitter.emit_signals_bulk",
                    return_value=1,
                ) as emit_mock,
            ):
                result = emit_recent_crowding(
                    config=path,
                    as_of=date(2026, 7, 15),
                )

        self.assertEqual(result.events_emitted, 1)
        self.assertEqual(result.rows_skipped_immature, 1)
        emitted_events = emit_mock.call_args.args[0]
        self.assertEqual(len(emitted_events), 1)
        self.assertEqual(emitted_events[0]["ts"].date(), date(2026, 3, 31))


if __name__ == "__main__":
    unittest.main()
