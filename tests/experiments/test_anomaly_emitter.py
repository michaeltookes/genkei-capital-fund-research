"""Unit tests for the anomaly-detection emitter (B-069)."""

from __future__ import annotations

import unittest
from datetime import date, datetime, time, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from genkei.experiments.emitters.anomaly_emitter import (
    METRIC_DAILY_RETURN,
    ScanTarget,
    TargetDetection,
    _delete_refreshed_anomalies,
    _detect_for_target,
    parse_args,
    run_anomaly_detection,
)


def _ts(day: date) -> datetime:
    return datetime.combine(day, time(0, 0, tzinfo=timezone.utc))


class DetectForTargetTests(unittest.TestCase):
    """Cover per-target slice bounds used for stale-row reconciliation."""

    def test_reports_refresh_bounds_even_when_no_anomalies_flag(self) -> None:
        closes = [
            (date(2026, 1, 1), Decimal("100")),
            (date(2026, 1, 2), Decimal("101")),
            (date(2026, 1, 3), Decimal("102")),
            (date(2026, 1, 4), Decimal("103")),
            (date(2026, 1, 5), Decimal("104")),
        ]
        with patch(
            "genkei.experiments.emitters.anomaly_emitter.load_close_series",
            return_value=closes,
        ):
            result = _detect_for_target(
                ScanTarget(asset="BTC", asset_class="crypto"),
                since=date(2026, 1, 3),
                until=date(2026, 1, 4),
                window=3,
                threshold=Decimal("999"),
                min_window=1,
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.anomalies, [])
        self.assertEqual(result.refresh_since, date(2026, 1, 3))
        self.assertEqual(result.refresh_until, date(2026, 1, 4))


class DeleteRefreshedAnomaliesTests(unittest.TestCase):
    """Cover stale anomaly cleanup before fresh flags are upserted."""

    def test_deletes_asset_metric_date_slice(self) -> None:
        cursor = MagicMock()
        cursor.rowcount = 2
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        target = ScanTarget(asset="bitcoin", asset_class="crypto")
        detection = TargetDetection(
            anomalies=[],
            refresh_since=date(2026, 1, 3),
            refresh_until=date(2026, 1, 5),
        )

        deleted = _delete_refreshed_anomalies(conn, [(target, detection)])

        sql, params = cursor.execute.call_args.args
        self.assertIn("DELETE FROM meta.anomalies", sql)
        self.assertIn("asset_class = %s", sql)
        self.assertEqual(
            params,
            [
                "bitcoin",
                "crypto",
                METRIC_DAILY_RETURN,
                _ts(date(2026, 1, 3)),
                _ts(date(2026, 1, 5)),
            ],
        )
        self.assertEqual(deleted, 2)

    def test_skips_targets_without_refreshed_dates(self) -> None:
        cursor = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        target = ScanTarget(asset="bitcoin", asset_class="crypto")
        detection = TargetDetection(anomalies=[], refresh_since=None, refresh_until=None)

        deleted = _delete_refreshed_anomalies(conn, [(target, detection)])

        cursor.execute.assert_not_called()
        self.assertEqual(deleted, 0)


class ParseArgsTests(unittest.TestCase):
    """Cover CLI defaults that protect ad-hoc emitter runs."""

    def test_until_defaults_to_last_completed_utc_date(self) -> None:
        with patch(
            "genkei.experiments.emitters.anomaly_emitter._last_completed_utc_date",
            return_value=date(2026, 1, 5),
        ):
            args = parse_args([])

        self.assertEqual(args.until, date(2026, 1, 5))


class RunAnomalyDetectionTests(unittest.TestCase):
    """Cover orchestration around cleanup plus optional upsert."""

    def test_defaults_until_to_last_completed_utc_date(self) -> None:
        run = MagicMock()
        run.id = 42
        run.__enter__.return_value = run
        run.__exit__.return_value = None
        target = ScanTarget(asset="bitcoin", asset_class="crypto")

        with (
            patch(
                "genkei.experiments.emitters.anomaly_emitter._last_completed_utc_date",
                return_value=date(2026, 1, 5),
            ),
            patch(
                "genkei.experiments.emitters.anomaly_emitter._scan_targets",
                return_value=[target],
            ),
            patch(
                "genkei.experiments.emitters.anomaly_emitter._detect_for_target",
                return_value=None,
            ) as detect,
            patch(
                "genkei.experiments.emitters.anomaly_emitter.db.ingest_run",
                return_value=run,
            ) as ingest_run,
        ):
            result = run_anomaly_detection(
                since=date(2026, 1, 3),
                until=None,
                window=3,
                threshold=Decimal("999"),
                min_window=1,
            )

        detect.assert_called_once_with(
            target,
            since=date(2026, 1, 3),
            until=date(2026, 1, 5),
            window=3,
            threshold=Decimal("999"),
            min_window=1,
        )
        self.assertEqual(ingest_run.call_args.kwargs["metadata"]["until"], "2026-01-05")
        run.add_rows.assert_called_once_with(0)
        self.assertEqual(result.targets_skipped_no_data, 1)

    def test_deletes_stale_rows_even_when_fresh_slice_has_no_flags(self) -> None:
        class FakeRun:
            id = 42

            def __init__(self) -> None:
                self.rows_added: int | None = None

            def __enter__(self) -> FakeRun:
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def add_rows(self, rows: int) -> None:
                self.rows_added = rows

        cursor = MagicMock()
        cursor.rowcount = 1
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        connection_cm = MagicMock()
        connection_cm.__enter__.return_value = conn
        run = FakeRun()
        target = ScanTarget(asset="bitcoin", asset_class="crypto")
        detection = TargetDetection(
            anomalies=[],
            refresh_since=date(2026, 1, 3),
            refresh_until=date(2026, 1, 5),
        )

        with (
            patch(
                "genkei.experiments.emitters.anomaly_emitter._scan_targets",
                return_value=[target],
            ),
            patch(
                "genkei.experiments.emitters.anomaly_emitter._detect_for_target",
                return_value=detection,
            ),
            patch(
                "genkei.experiments.emitters.anomaly_emitter.db.ingest_run",
                return_value=run,
            ),
            patch(
                "genkei.experiments.emitters.anomaly_emitter.db.connection",
                return_value=connection_cm,
            ),
            patch("genkei.experiments.emitters.anomaly_emitter.db.bulk_upsert") as upsert,
        ):
            result = run_anomaly_detection(
                since=date(2026, 1, 3),
                until=date(2026, 1, 5),
                window=3,
                threshold=Decimal("999"),
                min_window=1,
            )

        upsert.assert_not_called()
        cursor.execute.assert_called_once()
        self.assertEqual(result.anomalies_written, 0)
        self.assertEqual(run.rows_added, 0)


if __name__ == "__main__":
    unittest.main()
