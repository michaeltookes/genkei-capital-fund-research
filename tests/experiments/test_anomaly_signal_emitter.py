"""Unit tests for the return-anomaly → signal_events emitter (B-069 follow-up)."""

from __future__ import annotations

import unittest
from datetime import date, timezone
from decimal import Decimal
from unittest.mock import patch

from genkei.common.watchlist import CryptoEntry, EquityEntry, Watchlist
from genkei.experiments.emitters import anomaly_signal_emitter as ase


def _watchlist() -> Watchlist:
    return Watchlist(
        crypto=[
            CryptoEntry(
                symbol="ETH",
                name="Ethereum",
                coingecko_id="ethereum",
                tier="primary",
                sleeve="core",
                coinbase_product="ETH-USD",
            ),
            CryptoEntry(
                symbol="SUI",
                name="Sui",
                coingecko_id="sui",
                tier="primary",
                sleeve="tactical",
                coinbase_product="SUI-USD",
            ),
        ],
        equities=[
            EquityEntry(
                symbol="AAPL", name="Apple", cik="0000320193", tier="primary",
                sleeve="core",
            )
        ],
        macro=[],
        protocols=[],
        filers=[],
    )


def _flag(**kw) -> ase._AnomalyFlag:
    base = dict(
        asset="ethereum",
        asset_class="crypto",
        ts=date(2026, 6, 3),
        value=Decimal("-0.105"),
        score=Decimal("-4.24"),
        method="modified_zscore",
        direction="spike_down",
        window_days=90,
        threshold=Decimal("3.5"),
    )
    base.update(kw)
    return ase._AnomalyFlag(**base)


class StrengthTests(unittest.TestCase):
    def test_threshold_edge_carries_meaningful_strength(self) -> None:
        self.assertEqual(ase._strength_from_score(Decimal("3.5")), Decimal("0.70"))

    def test_saturates_at_one(self) -> None:
        self.assertEqual(ase._strength_from_score(Decimal("5")), Decimal("1"))
        self.assertEqual(ase._strength_from_score(Decimal("9")), Decimal("1"))

    def test_uses_absolute_value(self) -> None:
        self.assertEqual(
            ase._strength_from_score(Decimal("-4")),
            ase._strength_from_score(Decimal("4")),
        )


class DirectionTests(unittest.TestCase):
    def test_spike_down_is_bearish(self) -> None:
        ev = ase._build_event(_flag(direction="spike_down"), horizon="crypto:core")
        self.assertEqual(ev["direction"], "bearish")

    def test_spike_up_is_bullish(self) -> None:
        ev = ase._build_event(
            _flag(direction="spike_up", score=Decimal("4.9")), horizon="crypto:core"
        )
        self.assertEqual(ev["direction"], "bullish")


class ResolveHorizonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.wl = _watchlist()
        self.cg = ase._crypto_by_coingecko_id(self.wl)

    def test_crypto_core_by_coingecko_id(self) -> None:
        h = ase._resolve_horizon(_flag(asset="ethereum"), crypto_by_cgid=self.cg, watchlist=self.wl)
        self.assertEqual(h, "crypto:core")

    def test_crypto_tactical_sleeve(self) -> None:
        h = ase._resolve_horizon(
            _flag(asset="sui"), crypto_by_cgid=self.cg, watchlist=self.wl
        )
        self.assertEqual(h, "crypto:tactical")

    def test_equity_by_ticker(self) -> None:
        h = ase._resolve_horizon(
            _flag(asset="AAPL", asset_class="equity"),
            crypto_by_cgid=self.cg,
            watchlist=self.wl,
        )
        self.assertEqual(h, "equity:core")

    def test_non_watchlist_asset_is_none(self) -> None:
        # A yahoo benchmark / ETF ticker not in the watchlist → skip.
        h = ase._resolve_horizon(
            _flag(asset="SPY", asset_class="equity"),
            crypto_by_cgid=self.cg,
            watchlist=self.wl,
        )
        self.assertIsNone(h)


class BuildEventTests(unittest.TestCase):
    def test_event_shape(self) -> None:
        ev = ase._build_event(_flag(), horizon="crypto:core")
        self.assertEqual(ev["source"], "return_anomaly")
        self.assertEqual(ev["signal_kind"], "return_spike")
        self.assertEqual(ev["asset"], "ethereum")
        self.assertEqual(ev["asset_class"], "crypto")
        self.assertEqual(ev["source_ref"], "ethereum:2026-06-03")
        self.assertEqual(ev["ts"].tzinfo, timezone.utc)
        self.assertEqual(ev["payload"]["method"], "modified_zscore")
        self.assertEqual(ev["payload"]["return_pct"], "-10.500")


class EmitFlowTests(unittest.TestCase):
    def test_skips_flags_without_a_watchlist_horizon(self) -> None:
        flags = [
            _flag(asset="ethereum", asset_class="crypto"),
            _flag(asset="SPY", asset_class="equity"),  # not in watchlist
        ]
        captured: dict = {}

        def fake_emit(events, *, ingest_run_id):
            captured["events"] = events
            return len(events)

        class _Run:
            id = 7

            def add_rows(self, n):
                pass

        from contextlib import contextmanager

        @contextmanager
        def fake_ingest_run(*a, **k):
            yield _Run()

        with (
            patch.object(ase, "_load_flags", return_value=flags),
            patch.object(ase, "load_watchlist", return_value=_watchlist()),
            patch.object(ase, "emit_signals_bulk", side_effect=fake_emit),
            patch.object(ase.db, "ingest_run", fake_ingest_run),
        ):
            result = ase.emit_return_anomaly_signals()

        self.assertEqual(result.flags_seen, 2)
        self.assertEqual(result.flags_skipped_no_horizon, 1)
        self.assertEqual(result.events_emitted, 1)
        self.assertEqual(captured["events"][0]["asset"], "ethereum")


if __name__ == "__main__":
    unittest.main()
