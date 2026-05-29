"""Unit tests for the insider-clusters → signal_events emitter (B-064)."""

from __future__ import annotations

import unittest
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from genkei.common.watchlist import EquityEntry, load_watchlist
from genkei.experiments.emitters.insider_clusters_emitter import (
    STRENGTH_SATURATION_REPORTERS,
    _build_events,
    _strength_from_reporter_count,
    _ticker_by_cik,
    _window_end_to_ts,
)
from genkei.experiments.insider_clusters import Cluster, ReporterSummary

WATCHLIST_YAML = (
    "equities:\n"
    "  primary:\n"
    "    - symbol: AAPL\n"
    "      cik: \"0000320193\"\n"
    "      name: Apple Inc.\n"
)


def _watchlist_ticker_map() -> dict[str, list[EquityEntry]]:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "watchlists.yml"
        path.write_text(WATCHLIST_YAML, encoding="utf-8")
        w = load_watchlist(path)
    out: dict[str, list[EquityEntry]] = {}
    for entry in w.equities:
        if entry.cik:
            out.setdefault(entry.cik, []).append(entry)
    return out


def _reporter(cik: str = "0000111111", name: str = "Alice") -> ReporterSummary:
    return ReporterSummary(
        reporter_cik=cik,
        reporter_name=name,
        shares=Decimal("100"),
        value_usd=Decimal("10000"),
        is_officer=True,
        is_director=False,
        is_ten_percent_owner=False,
        officer_title="CFO",
    )


def _cluster(
    *,
    issuer_cik: str = "0000320193",
    direction: str = "buy",
    reporter_count: int = 2,
    total_value_usd: Decimal | None = Decimal("100000"),
) -> Cluster:
    return Cluster(
        issuer_cik=issuer_cik,
        direction=direction,
        window_start=date(2026, 5, 1),
        window_end=date(2026, 5, 7),
        reporter_count=reporter_count,
        total_shares=Decimal("1000"),
        total_value_usd=total_value_usd,
        reporters=[_reporter(f"00000001{i:02d}", f"Reporter {i}") for i in range(reporter_count)],
    )


class StrengthRampTests(unittest.TestCase):
    def test_zero_reporters_clamps_to_zero(self) -> None:
        self.assertEqual(_strength_from_reporter_count(0), Decimal("0"))

    def test_below_saturation_is_proportional(self) -> None:
        # 2 reporters → 2/5 = 0.4
        self.assertEqual(_strength_from_reporter_count(2), Decimal("0.4"))

    def test_saturation_point_hits_one(self) -> None:
        # 5 reporters → 1.0
        self.assertEqual(
            _strength_from_reporter_count(int(STRENGTH_SATURATION_REPORTERS)),
            Decimal("1"),
        )

    def test_above_saturation_clamps_to_one(self) -> None:
        self.assertEqual(_strength_from_reporter_count(20), Decimal("1"))


class WindowEndToTsTests(unittest.TestCase):
    def test_converts_to_utc_midnight(self) -> None:
        result = _window_end_to_ts(date(2026, 5, 7))
        self.assertEqual(result, datetime(2026, 5, 7, tzinfo=timezone.utc))
        self.assertEqual(result.time(), time(0, 0))


class BuildEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ticker_map = _watchlist_ticker_map()

    def test_builds_canonical_bullish_buy_cluster(self) -> None:
        cluster = _cluster()
        event = _build_events(cluster, self.ticker_map)[0]
        self.assertEqual(event["asset"], "AAPL")
        self.assertEqual(event["asset_class"], "equity")
        self.assertEqual(event["horizon"], "equity:core")
        self.assertEqual(event["source"], "insider_clusters")
        self.assertEqual(event["signal_kind"], "buy_cluster")
        self.assertEqual(event["direction"], "bullish")
        self.assertEqual(event["strength"], Decimal("0.4"))  # 2/5 reporters
        self.assertEqual(event["ts"], datetime(2026, 5, 7, tzinfo=timezone.utc))
        # source_ref stays stable if reporter_count changes after late filings.
        self.assertEqual(event["source_ref"], "0000320193:2026-05-07")
        # payload includes per-reporter detail with Decimal serialized as
        # string (matches the project convention from B-079)
        self.assertEqual(event["payload"]["ticker"], "AAPL")
        self.assertEqual(event["payload"]["tier"], "primary")
        self.assertEqual(event["payload"]["reporter_count"], 2)
        self.assertEqual(event["payload"]["total_value_usd"], "100000")
        self.assertEqual(len(event["payload"]["reporters"]), 2)

    def test_maps_sell_cluster_to_bearish(self) -> None:
        cluster = _cluster(direction="sell")
        event = _build_events(cluster, self.ticker_map)[0]
        self.assertEqual(event["signal_kind"], "sell_cluster")
        self.assertEqual(event["direction"], "bearish")

    def test_non_watchlist_issuer_returns_none(self) -> None:
        cluster = _cluster(issuer_cik="9999999999")  # not in watchlist
        self.assertEqual(_build_events(cluster, self.ticker_map), [])

    def test_null_total_value_becomes_null_payload_field(self) -> None:
        cluster = _cluster(total_value_usd=None)
        event = _build_events(cluster, self.ticker_map)[0]
        self.assertIsNone(event["payload"]["total_value_usd"])

    def test_emits_one_event_per_ticker_for_shared_cik(self) -> None:
        cluster = _cluster(issuer_cik="0001652044")
        ticker_map = {
            "0001652044": [
                EquityEntry(
                    symbol="GOOG",
                    name="Alphabet Inc.",
                    cik="0001652044",
                    tier="primary",
                    sleeve="core",
                ),
                EquityEntry(
                    symbol="GOOGL",
                    name="Alphabet Inc.",
                    cik="0001652044",
                    tier="primary",
                    sleeve="core",
                ),
            ]
        }
        events = _build_events(cluster, ticker_map)
        self.assertEqual([event["asset"] for event in events], ["GOOG", "GOOGL"])
        self.assertTrue(all(event["horizon"] == "equity:core" for event in events))


class TickerByCikTests(unittest.TestCase):
    def test_preserves_multiple_tickers_for_shared_cik(self) -> None:
        yaml_text = (
            "equities:\n"
            "  primary:\n"
            "    - symbol: GOOG\n"
            "      cik: \"0001652044\"\n"
            "      name: Alphabet Inc.\n"
            "    - symbol: GOOGL\n"
            "      cik: \"0001652044\"\n"
            "      name: Alphabet Inc.\n"
        )
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(yaml_text, encoding="utf-8")
            watchlist = load_watchlist(path)

        mapping = _ticker_by_cik(watchlist)

        self.assertEqual(
            [entry.symbol for entry in mapping["0001652044"]],
            ["GOOG", "GOOGL"],
        )


if __name__ == "__main__":
    unittest.main()
