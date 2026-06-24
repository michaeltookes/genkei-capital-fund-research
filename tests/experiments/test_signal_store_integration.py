"""End-to-end test for meta.signal_events emit + query (B-064).

Pins the persistence contract against a live TimescaleDB container:

  * ``emit_signals_bulk`` writes rows + returns the count.
  * The UNIQUE ``(asset, ts, source, signal_kind, source_ref)`` clause
    is honored — re-emitting the same event with a fresh payload
    *updates* the existing row rather than inserting a duplicate.
  * ``query_events`` returns rows with the same shape the correlator
    expects + the same Decimal precision the SQL layer preserves.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from genkei.common import db
from genkei.experiments.signal_store import (
    SignalEvent,
    emit_signals_bulk,
    query_events,
)
from tests._postgres import PostgresTestCase


def _event_row(
    *,
    asset: str = "AAPL",
    ts: datetime,
    source: str = "insider_clusters",
    signal_kind: str = "buy_cluster",
    direction: str = "bullish",
    strength: Decimal | None = Decimal("0.6"),
    source_ref: str | None = "0000320193:2026-05-07:2",
) -> dict:
    return {
        "asset": asset,
        "asset_class": "equity",
        "horizon": "equity:core",
        "ts": ts,
        "source": source,
        "signal_kind": signal_kind,
        "direction": direction,
        "strength": strength,
        "payload": {"hello": "world"},
        "source_ref": source_ref,
    }


class SignalStoreIntegrationTests(PostgresTestCase):
    def _new_ingest_run(self) -> int:
        """Insert a placeholder meta.ingest_runs row and return its id."""
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO meta.ingest_runs (source, endpoint, status) "
                "VALUES (%s, %s, %s) RETURNING id",
                ["signal_emitter_test", "insider_clusters", "running"],
            )
            return int(cur.fetchone()[0])

    def test_bulk_emit_and_query(self) -> None:
        run_id = self._new_ingest_run()
        ts = datetime(2026, 5, 7, tzinfo=timezone.utc)
        rows = [
            _event_row(ts=ts, source_ref="cluster-1"),
            _event_row(
                ts=ts,
                source="crowding",
                signal_kind="crowding_add",
                strength=Decimal("0.8"),
                source_ref="crowding-1",
            ),
        ]
        written = emit_signals_bulk(rows, ingest_run_id=run_id)
        self.assertEqual(written, 2)

        events = query_events(asset="AAPL")
        self.assertEqual(len(events), 2)
        kinds = sorted(e.signal_kind for e in events)
        self.assertEqual(kinds, ["buy_cluster", "crowding_add"])
        # Strength precision survives the round-trip.
        by_kind = {e.signal_kind: e for e in events}
        self.assertEqual(by_kind["buy_cluster"].strength, Decimal("0.6"))
        self.assertEqual(by_kind["crowding_add"].strength, Decimal("0.8"))
        self.assertEqual(by_kind["buy_cluster"].horizon, "equity:core")
        # Payload round-trips as a dict.
        self.assertEqual(by_kind["buy_cluster"].payload, {"hello": "world"})

    def test_unique_constraint_upserts_on_conflict(self) -> None:
        """Re-emit with the same natural key — must UPDATE, not duplicate."""
        run_id = self._new_ingest_run()
        ts = datetime(2026, 5, 7, tzinfo=timezone.utc)

        emit_signals_bulk(
            [_event_row(ts=ts, strength=Decimal("0.4"))],
            ingest_run_id=run_id,
        )
        # Re-emit with the same source_ref but a stronger strength.
        emit_signals_bulk(
            [_event_row(ts=ts, strength=Decimal("0.9"))],
            ingest_run_id=run_id,
        )
        events = query_events(asset="AAPL")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].strength, Decimal("0.9"))

    def test_null_source_ref_is_still_idempotent(self) -> None:
        run_id = self._new_ingest_run()
        ts = datetime(2026, 5, 7, tzinfo=timezone.utc)

        emit_signals_bulk(
            [_event_row(ts=ts, strength=Decimal("0.4"), source_ref=None)],
            ingest_run_id=run_id,
        )
        emit_signals_bulk(
            [_event_row(ts=ts, strength=Decimal("0.9"), source_ref=None)],
            ingest_run_id=run_id,
        )
        events = query_events(asset="AAPL")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].strength, Decimal("0.9"))
        self.assertEqual(events[0].source_ref, "")

    def test_query_filters(self) -> None:
        run_id = self._new_ingest_run()
        early = datetime(2026, 5, 1, tzinfo=timezone.utc)
        late = datetime(2026, 5, 20, tzinfo=timezone.utc)
        rows = [
            _event_row(
                asset="AAPL",
                ts=early,
                source_ref="a",
                strength=Decimal("0.5"),
            ),
            _event_row(
                asset="MSFT",
                ts=late,
                source="crowding",
                signal_kind="crowding_add",
                source_ref="m",
                strength=Decimal("0.7"),
            ),
        ]
        emit_signals_bulk(rows, ingest_run_id=run_id)

        # Asset filter
        aapl_only: list[SignalEvent] = query_events(asset="AAPL")
        self.assertEqual({e.asset for e in aapl_only}, {"AAPL"})

        # Source filter
        crowd: list[SignalEvent] = query_events(source="crowding")
        self.assertEqual({e.signal_kind for e in crowd}, {"crowding_add"})

        # Since filter
        recent = query_events(
            since=datetime(2026, 5, 10, tzinfo=timezone.utc)
        )
        self.assertEqual({e.asset for e in recent}, {"MSFT"})

    def test_check_constraint_rejects_unknown_direction(self) -> None:
        run_id = self._new_ingest_run()
        ts = datetime(2026, 5, 7, tzinfo=timezone.utc)
        # Python-level guard fires before SQL hits the DB; assert that.
        with self.assertRaises(ValueError):
            emit_signals_bulk(
                [_event_row(ts=ts, direction="moonbound")],
                ingest_run_id=run_id,
            )


if __name__ == "__main__":
    unittest.main()
