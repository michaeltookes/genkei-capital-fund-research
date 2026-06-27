"""Unit tests for the shared data-freshness helpers (B-023)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from genkei.common.freshness import (
    DEFAULT_MAX_SNAPSHOT_AGE_HOURS,
    age_hours,
    ingest_run_freshness,
    snapshot_freshness,
    stale_banner,
)

NOW = datetime(2026, 6, 26, 12, 0, 0, tzinfo=timezone.utc)


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, row):
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._row)


class AgeHoursTests(unittest.TestCase):
    def test_none_passes_through(self) -> None:
        self.assertIsNone(age_hours(None, now=NOW))

    def test_aware_datetime(self) -> None:
        self.assertEqual(age_hours(NOW - timedelta(hours=10), now=NOW), 10.0)

    def test_naive_string_treated_as_utc(self) -> None:
        # No offset in the string → assumed UTC, not a crash.
        self.assertEqual(age_hours("2026-06-26T00:00:00", now=NOW), 12.0)

    def test_iso_string_with_offset(self) -> None:
        self.assertEqual(age_hours("2026-06-26T02:00:00+00:00", now=NOW), 10.0)

    def test_rounds_to_one_dp(self) -> None:
        self.assertEqual(age_hours(NOW - timedelta(minutes=90), now=NOW), 1.5)


class SnapshotFreshnessTests(unittest.TestCase):
    def test_fresh_under_threshold(self) -> None:
        f = snapshot_freshness(NOW - timedelta(hours=10), source="x", now=NOW)
        self.assertFalse(f["stale"])
        self.assertEqual(f["age_hours"], 10.0)

    def test_stale_over_threshold(self) -> None:
        f = snapshot_freshness(NOW - timedelta(hours=50), source="x", now=NOW)
        self.assertTrue(f["stale"])
        self.assertEqual(f["age_hours"], 50.0)

    def test_default_threshold_value(self) -> None:
        f = snapshot_freshness(NOW, source="x", now=NOW)
        self.assertEqual(f["max_age_hours"], DEFAULT_MAX_SNAPSHOT_AGE_HOURS)
        self.assertEqual(DEFAULT_MAX_SNAPSHOT_AGE_HOURS, 36.0)

    def test_threshold_override_suppresses_staleness(self) -> None:
        # 50h old but a 200h threshold → not stale.
        f = snapshot_freshness(
            NOW - timedelta(hours=50), source="x", max_age_hours=200, now=NOW
        )
        self.assertFalse(f["stale"])

    def test_none_ts_is_not_stale(self) -> None:
        f = snapshot_freshness(None, source="x", now=NOW)
        self.assertFalse(f["stale"])
        self.assertIsNone(f["age_hours"])
        self.assertIsNone(f["last_ts"])


class IngestRunFreshnessTests(unittest.TestCase):
    def test_recent_run_not_stale(self) -> None:
        row = (NOW - timedelta(hours=10),)
        with patch(
            "genkei.common.freshness.db.connection", return_value=_FakeConn(row)
        ):
            f = ingest_run_freshness("fred", "normalize", now=NOW)
        self.assertFalse(f["stale"])
        self.assertEqual(f["age_hours"], 10.0)
        self.assertEqual(f["source"], "fred/normalize")
        self.assertEqual(f["kind"], "ingest_run")

    def test_old_run_is_stale(self) -> None:
        row = (NOW - timedelta(hours=50),)
        with patch(
            "genkei.common.freshness.db.connection", return_value=_FakeConn(row)
        ):
            f = ingest_run_freshness("fred", "normalize", now=NOW)
        self.assertTrue(f["stale"])

    def test_no_runs_not_stale(self) -> None:
        with patch(
            "genkei.common.freshness.db.connection", return_value=_FakeConn((None,))
        ):
            f = ingest_run_freshness("fred", "normalize", now=NOW)
        self.assertFalse(f["stale"])
        self.assertIsNone(f["last_ts"])

    def test_db_failure_never_breaks_the_query(self) -> None:
        # A freshness probe must not propagate a DB error to the caller.
        def boom():
            raise RuntimeError("GENKEI_DATABASE_URL is not set")

        with patch("genkei.common.freshness.db.connection", side_effect=boom):
            f = ingest_run_freshness("fred", "normalize", now=NOW)
        self.assertFalse(f["stale"])
        self.assertIsNone(f["last_ts"])


class StaleBannerTests(unittest.TestCase):
    def test_banner_mentions_age_and_health_command(self) -> None:
        f = snapshot_freshness(
            NOW - timedelta(hours=50), source="coingecko.market_data", now=NOW
        )
        banner = stale_banner(f)
        self.assertIn("STALE", banner)
        self.assertIn("coingecko.market_data", banner)
        self.assertIn("50.0h", banner)
        self.assertIn("genkei watchlist health", banner)


if __name__ == "__main__":
    unittest.main()
