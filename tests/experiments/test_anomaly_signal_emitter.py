"""Unit tests for the return-anomaly → signal_events emitter (B-069 follow-up)."""

from __future__ import annotations

import unittest
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

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


def _ts(day: date) -> datetime:
    return datetime.combine(day, time(0, 0, tzinfo=timezone.utc))


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


class DeleteRefreshedSignalEventsTests(unittest.TestCase):
    def test_deletes_return_anomaly_events_in_requested_slice(self) -> None:
        cursor = MagicMock()
        cursor.rowcount = 2
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor

        deleted = ase._delete_refreshed_signal_events(
            conn, since=date(2026, 1, 3), until=date(2026, 1, 5)
        )

        sql, params = cursor.execute.call_args.args
        self.assertIn("DELETE FROM meta.signal_events", sql)
        self.assertIn("source = %s", sql)
        self.assertIn("signal_kind = %s", sql)
        self.assertIn("ts >= %s", sql)
        self.assertIn("ts < %s", sql)
        self.assertEqual(
            params,
            [
                ase.EMITTER_SOURCE,
                ase.SIGNAL_KIND,
                _ts(date(2026, 1, 3)),
                _ts(date(2026, 1, 5) + timedelta(days=1)),
            ],
        )
        self.assertEqual(deleted, 2)

    def test_unbounded_refresh_deletes_all_return_anomaly_events(self) -> None:
        cursor = MagicMock()
        cursor.rowcount = 4
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor

        deleted = ase._delete_refreshed_signal_events(conn, since=None, until=None)

        sql, params = cursor.execute.call_args.args
        self.assertNotIn("ts >=", sql)
        self.assertNotIn("ts <", sql)
        self.assertEqual(params, [ase.EMITTER_SOURCE, ase.SIGNAL_KIND])
        self.assertEqual(deleted, 4)


class EmitFlowTests(unittest.TestCase):
    def test_skips_flags_without_a_watchlist_horizon(self) -> None:
        flags = [
            _flag(asset="ethereum", asset_class="crypto"),
            _flag(asset="SPY", asset_class="equity"),  # not in watchlist
        ]
        captured: dict = {}

        def fake_emit(events, *, ingest_run_id, conn=None):
            captured["events"] = events
            captured["conn"] = conn
            return len(events)

        class _Run:
            id = 7

            def add_rows(self, n):
                pass

        from contextlib import contextmanager

        @contextmanager
        def fake_ingest_run(*a, **k):
            yield _Run()

        cursor = MagicMock()
        cursor.rowcount = 0
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        connection_cm = MagicMock()
        connection_cm.__enter__.return_value = conn

        with (
            patch.object(ase, "_load_flags", return_value=flags),
            patch.object(ase, "load_watchlist", return_value=_watchlist()),
            patch.object(ase, "emit_signals_bulk", side_effect=fake_emit),
            patch.object(ase.db, "ingest_run", fake_ingest_run),
            patch.object(ase.db, "connection", return_value=connection_cm),
        ):
            result = ase.emit_return_anomaly_signals()

        self.assertEqual(result.flags_seen, 2)
        self.assertEqual(result.flags_skipped_no_horizon, 1)
        self.assertEqual(result.events_emitted, 1)
        self.assertEqual(captured["events"][0]["asset"], "ethereum")
        self.assertIs(captured["conn"], conn)

    def test_deletes_refreshed_events_before_emitting(self) -> None:
        calls: list[str] = []
        db_conn = MagicMock()
        connection_cm = MagicMock()
        connection_cm.__enter__.return_value = db_conn

        def fake_delete(conn_arg, *, since, until):
            self.assertIs(conn_arg, db_conn)
            self.assertEqual(since, date(2026, 6, 1))
            self.assertEqual(until, date(2026, 6, 7))
            calls.append("delete")
            return 1

        def fake_emit(events, *, ingest_run_id, conn=None):
            self.assertIs(conn, db_conn)
            calls.append("emit")
            return len(events)

        class _Run:
            id = 11

            def add_rows(self, n):
                pass

        from contextlib import contextmanager

        @contextmanager
        def fake_ingest_run(*a, **k):
            yield _Run()

        with (
            patch.object(ase, "_load_flags", return_value=[_flag()]),
            patch.object(ase, "load_watchlist", return_value=_watchlist()),
            patch.object(ase, "_delete_refreshed_signal_events", side_effect=fake_delete),
            patch.object(ase, "emit_signals_bulk", side_effect=fake_emit),
            patch.object(ase.db, "ingest_run", fake_ingest_run),
            patch.object(ase.db, "connection", return_value=connection_cm),
        ):
            result = ase.emit_return_anomaly_signals(
                since=date(2026, 6, 1), until=date(2026, 6, 7)
            )

        self.assertEqual(calls, ["delete", "emit"])
        self.assertEqual(result.events_emitted, 1)


if __name__ == "__main__":
    unittest.main()
