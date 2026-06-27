"""Offline tests for the ingest-health report renderer (B-053)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from genkei.common.freshness import DEFAULT_MAX_SNAPSHOT_AGE_HOURS
from genkei.reports.ingest_health import (
    DEFAULT_STALE_HOURS,
    render_health_report,
    write_report,
)

GEN = datetime(2026, 6, 26, 7, 13, 0, tzinfo=timezone.utc)


def _run(source, endpoint, status, age, last="2026-06-26T05:00:00+00:00", error=None):
    return {
        "source": source,
        "endpoint": endpoint,
        "status": "success" if status == "OK" else status,
        "last_started_at": last,
        "last_finished_at": last,
        "age_hours": age,
        "error": error,
        "health_status": status,
    }


def _table(source, table, has_rows, status):
    return {
        "source": source,
        "table": table,
        "has_rows": has_rows,
        "error": None,
        "health_status": status,
    }


def _drift(source, endpoint_kind, kind, detail):
    return {
        "source": source,
        "endpoint": endpoint_kind,
        "endpoint_kind": endpoint_kind,
        "drift_kind": kind,
        "detail": detail,
        "error": detail,
        "sample_endpoint_name": None,
        "health_status": "DRIFT",
    }


class HealthyRosterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [
            _run("coingecko", "collect", "OK", 12.0),
            _run("coingecko", "normalize", "OK", 12.0),
            _run("fred", "collect", "OK", 13.0),
            _table("coingecko", "coingecko.market_data", True, "OK"),
        ]
        self.md = render_health_report(self.rows, generated_at=GEN, stale_hours=36.0)

    def test_lists_every_endpoint(self) -> None:
        # Full roster — healthy sources included, not just the broken ones.
        self.assertIn("coingecko", self.md)
        self.assertIn("fred", self.md)
        self.assertIn("| coingecko | collect | OK |", self.md)

    def test_all_healthy_banner(self) -> None:
        self.assertIn("All sources healthy", self.md)
        self.assertIn("No schema drift detected", self.md)

    def test_header_has_generated_and_cutoff(self) -> None:
        self.assertIn("2026-06-26T07:13:00Z", self.md)
        self.assertIn("36h", self.md)


class DefaultThresholdTests(unittest.TestCase):
    def test_default_stale_hours_reuses_canonical_freshness_constant(self) -> None:
        self.assertEqual(DEFAULT_STALE_HOURS, DEFAULT_MAX_SNAPSHOT_AGE_HOURS)


class MixedRosterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [
            _run("coingecko", "collect", "OK", 12.0),
            _run("gdelt", "collect", "STALE", 200.0, last="2026-06-18T00:00:00+00:00"),
            _run("bea", "collect", "MISSING", None, last=None),
            _run("eia", "normalize", "FAIL", 5.0, error="psycopg OperationalError: boom"),
            _table("gdelt", "gdelt.gkg", False, "EMPTY"),
            _table("coingecko", "coingecko.market_data", True, "OK"),
            _drift("defillama", "protocol_fees", "MISSING_KEY", "totalFees absent"),
        ]
        self.md = render_health_report(self.rows, generated_at=GEN, stale_hours=36.0)

    def test_action_needed_banner(self) -> None:
        self.assertIn("Action needed", self.md)

    def test_surfaces_each_status(self) -> None:
        for tag in ("STALE", "MISSING", "FAIL", "EMPTY", "DRIFT"):
            self.assertIn(tag, self.md)

    def test_stale_row_carries_timestamp_for_traceability(self) -> None:
        self.assertIn("2026-06-18T00:00:00+00:00", self.md)

    def test_error_note_truncated_into_row(self) -> None:
        self.assertIn("psycopg OperationalError", self.md)

    def test_drift_detail_rendered(self) -> None:
        self.assertIn("totalFees absent", self.md)
        self.assertIn("MISSING_KEY", self.md)

    def test_summary_counts(self) -> None:
        # 4 ingest endpoints, 1 OK / 3 need attention.
        self.assertIn("**4** ingest endpoint(s): 1 OK · 3 need attention", self.md)
        self.assertIn("**1** schema-drift finding(s)", self.md)


class EmptyRosterTests(unittest.TestCase):
    def test_none_rows_renders_unavailable_not_crash(self) -> None:
        md = render_health_report(None, generated_at=GEN, stale_hours=36.0)
        self.assertIn("Health snapshot unavailable", md)

    def test_empty_list_renders_zero_summary(self) -> None:
        md = render_health_report([], generated_at=GEN, stale_hours=36.0)
        self.assertIn("Health snapshot unavailable", md)


class WriteReportTests(unittest.TestCase):
    def test_writes_dated_file(self) -> None:
        import tempfile
        from datetime import date
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            path = write_report("# hi\n", date(2026, 6, 26), output_dir=Path(d))
            self.assertTrue(path.exists())
            self.assertEqual(path.name, "ingest-health-2026-06-26.md")
            self.assertEqual(path.read_text(), "# hi\n")


if __name__ == "__main__":
    unittest.main()
