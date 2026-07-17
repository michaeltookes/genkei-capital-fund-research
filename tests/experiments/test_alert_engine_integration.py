"""End-to-end tests for meta.alerts persistence + the alert engine (B-068).

Pins the persistence contract against a live TimescaleDB container:

  * ``persist_alerts`` inserts candidates and returns only newly-created rows;
    a re-run over the same fingerprints inserts nothing (idempotent).
  * ``mark_notified`` stamps ``notified_at`` once.
  * ``query_alerts`` honors the severity / status / asset filters.
  * ``run_alert_engine`` ties correlate → evaluate → persist together over
    seeded ``meta.signal_events`` and retries unnotified rows.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from genkei.common import db
from genkei.experiments.alert_engine import (
    Alert,
    mark_notified,
    persist_alerts,
    query_alerts,
    run_alert_engine,
)
from genkei.experiments.alert_rules import parse_alert_rules
from genkei.experiments.signal_store import emit_signals_bulk
from tests._postgres import PostgresTestCase


def _candidate(
    *,
    alert_rule: str = "critical_equity_exit",
    correlation_rule: str = "broad_exit",
    asset: str = "NVDA",
    severity: str = "critical",
    score: str = "2.0",
    day: int = 10,
) -> Alert:
    ts = datetime(2026, 7, day, tzinfo=timezone.utc)
    fp = f"{alert_rule}:{correlation_rule}:{asset}:equity:core:2026-07-{day:02d}"
    return Alert(
        alert_rule=alert_rule,
        correlation_rule=correlation_rule,
        asset=asset,
        asset_class="equity",
        horizon="equity:core",
        direction="bearish",
        severity=severity,
        score=Decimal(score),
        distinct_sources=2,
        triggered_at=ts,
        fingerprint=fp,
        payload={"correlation_rule": correlation_rule},
    )


class PersistenceTests(PostgresTestCase):
    def _new_ingest_run(self) -> int:
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO meta.ingest_runs (source, endpoint, status) "
                "VALUES (%s, %s, %s) RETURNING id",
                ["alert_engine_test", "evaluate", "running"],
            )
            return int(cur.fetchone()[0])

    def test_persist_returns_new_only_and_is_idempotent(self) -> None:
        run_id = self._new_ingest_run()
        candidates = [_candidate(asset="NVDA"), _candidate(asset="AAPL")]
        with db.connection() as conn:
            created = persist_alerts(candidates, ingest_run_id=run_id, conn=conn)
            conn.commit()
        self.assertEqual({a.asset for a in created}, {"NVDA", "AAPL"})
        self.assertTrue(all(a.alert_id is not None for a in created))

        # Re-persist the same fingerprints → nothing new.
        with db.connection() as conn:
            again = persist_alerts(candidates, ingest_run_id=run_id, conn=conn)
            conn.commit()
        self.assertEqual(again, [])

        # A genuinely new stack (different day) does insert.
        with db.connection() as conn:
            fresh = persist_alerts(
                [_candidate(asset="NVDA", day=11)], ingest_run_id=run_id, conn=conn
            )
            conn.commit()
        self.assertEqual(len(fresh), 1)

    def test_mark_notified_stamps_once(self) -> None:
        run_id = self._new_ingest_run()
        with db.connection() as conn:
            created = persist_alerts([_candidate()], ingest_run_id=run_id, conn=conn)
            conn.commit()
        ids = [a.alert_id for a in created]
        with db.connection() as conn:
            first = mark_notified(conn, ids)
            conn.commit()
        self.assertEqual(first, 1)
        # Already-stamped rows aren't re-marked.
        with db.connection() as conn:
            second = mark_notified(conn, ids)
            conn.commit()
        self.assertEqual(second, 0)

    def test_query_filters(self) -> None:
        run_id = self._new_ingest_run()
        with db.connection() as conn:
            persist_alerts(
                [
                    _candidate(asset="NVDA", severity="critical"),
                    _candidate(
                        alert_rule="crypto_stress",
                        correlation_rule="crypto_tvl_stress_combo",
                        asset="SUI",
                        severity="warning",
                    ),
                ],
                ingest_run_id=run_id,
                conn=conn,
            )
            conn.commit()
        self.assertEqual({a.asset for a in query_alerts(severity="critical")}, {"NVDA"})
        self.assertEqual({a.asset for a in query_alerts(asset="SUI")}, {"SUI"})
        self.assertEqual(query_alerts(status="resolved"), [])
        self.assertEqual(len(query_alerts()), 2)


class EngineEndToEndTests(PostgresTestCase):
    _RULES = {
        "version": 1,
        "rules": [
            {
                "name": "broad_exit",
                "description": "test",
                "direction": "bearish",
                "horizon": "equity:core",
                "window_days": 90,
                "min_score": "1.5",
                "min_distinct_sources": 2,
                "components": [
                    {"source": "insider_clusters", "signal_kind": "sell_cluster", "weight": "1.0"},
                    {"source": "crowding", "signal_kind": "crowding_exit", "weight": "1.0"},
                ],
            }
        ],
    }
    _ALERTS = parse_alert_rules(
        {
            "version": 1,
            "alerts": [
                {
                    "name": "critical_equity_exit",
                    "severity": "critical",
                    "match": {"asset_class": ["equity"], "direction": ["bearish"]},
                    "min_score": "1.8",
                    "min_distinct_sources": 2,
                }
            ],
        }
    )

    def _seed_stack(self) -> None:
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO meta.ingest_runs (source, endpoint, status) "
                "VALUES (%s, %s, %s) RETURNING id",
                ["seed", "seed", "running"],
            )
            run_id = int(cur.fetchone()[0])
        ts = datetime(2026, 7, 10, tzinfo=timezone.utc)
        rows = [
            {
                "asset": "NVDA", "asset_class": "equity", "horizon": "equity:core",
                "ts": ts, "source": "insider_clusters", "signal_kind": "sell_cluster",
                "direction": "bearish", "strength": Decimal("1.0"),
                "payload": {}, "source_ref": "s1",
            },
            {
                "asset": "NVDA", "asset_class": "equity", "horizon": "equity:core",
                "ts": ts, "source": "crowding", "signal_kind": "crowding_exit",
                "direction": "bearish", "strength": Decimal("1.0"),
                "payload": {}, "source_ref": "c1",
            },
        ]
        emit_signals_bulk(rows, ingest_run_id=run_id)

    def test_run_engine_persists_alert_from_seeded_events(self) -> None:
        from unittest.mock import patch

        self._seed_stack()
        with patch(
            "genkei.experiments.alert_engine.load_rules",
            return_value=_parse_rules(self._RULES),
        ), patch(
            "genkei.experiments.alert_rules.load_alert_rules", return_value=self._ALERTS
        ):
            result = run_alert_engine(since=None, until=None, notify=False)
        self.assertGreaterEqual(result.stacks_seen, 1)
        self.assertEqual(len(result.new_alerts), 1)
        self.assertEqual(result.new_alerts[0].asset, "NVDA")
        self.assertEqual(result.new_alerts[0].severity, "critical")
        # Re-run is idempotent — no new alerts the second time.
        with patch(
            "genkei.experiments.alert_engine.load_rules",
            return_value=_parse_rules(self._RULES),
        ), patch(
            "genkei.experiments.alert_rules.load_alert_rules", return_value=self._ALERTS
        ):
            again = run_alert_engine(since=None, until=None, notify=False)
        self.assertEqual(len(again.new_alerts), 0)

    def test_run_engine_retries_unnotified_alerts_from_current_window(self) -> None:
        from unittest.mock import patch

        self._seed_stack()
        since = date(2026, 7, 1)
        until = date(2026, 7, 31)

        with patch(
            "genkei.experiments.alert_engine.load_rules",
            return_value=_parse_rules(self._RULES),
        ), patch(
            "genkei.experiments.alert_rules.load_alert_rules", return_value=self._ALERTS
        ):
            first = run_alert_engine(
                since=since,
                until=until,
                notify=True,
                webhook_url=None,
            )
        self.assertEqual(len(first.new_alerts), 1)
        self.assertEqual(first.notified, 0)
        self.assertIsNone(query_alerts()[0].notified_at)

        delivered_assets: list[str] = []

        def _deliver(alerts: list[Alert], *, webhook_url: str | None) -> list[Alert]:
            delivered_assets.extend(a.asset for a in alerts)
            return list(alerts)

        with patch(
            "genkei.experiments.alert_engine.load_rules",
            return_value=_parse_rules(self._RULES),
        ), patch(
            "genkei.experiments.alert_rules.load_alert_rules", return_value=self._ALERTS
        ), patch(
            "genkei.experiments.alert_notify.post_alert_batches",
            side_effect=_deliver,
        ):
            again = run_alert_engine(
                since=since,
                until=until,
                notify=True,
                webhook_url="https://discord/webhook",
            )

        self.assertEqual(len(again.new_alerts), 0)
        self.assertEqual(again.notified, 1)
        self.assertEqual(delivered_assets, ["NVDA"])
        self.assertIsNotNone(query_alerts()[0].notified_at)


def _parse_rules(doc: dict) -> list:
    from genkei.experiments.signal_rules import parse_rules

    return parse_rules(doc)


if __name__ == "__main__":
    unittest.main()
