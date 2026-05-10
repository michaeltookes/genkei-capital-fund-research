"""Unit tests for the FRED normalizer (offline)."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from genkei.normalize.fred import (
    normalize_observations,
    normalize_series,
    parse_fred_date,
    parse_fred_datetime,
    parse_fred_value,
)

NOW = datetime(2026, 5, 10, 11, 0, tzinfo=timezone.utc)


class HelperTests(unittest.TestCase):
    def test_parse_fred_date_accepts_iso(self) -> None:
        self.assertEqual(parse_fred_date("2024-01-15"), date(2024, 1, 15))

    def test_parse_fred_date_rejects_garbage(self) -> None:
        self.assertIsNone(parse_fred_date("not a date"))
        self.assertIsNone(parse_fred_date(None))

    def test_parse_fred_datetime_handles_short_offset(self) -> None:
        # FRED uses `-05` (no minutes); parser should normalise.
        parsed = parse_fred_datetime("2024-05-30 08:30:00-05")
        assert parsed is not None
        self.assertEqual(parsed.utcoffset().total_seconds(), -5 * 3600)

    def test_parse_fred_datetime_falls_back_to_date(self) -> None:
        parsed = parse_fred_datetime("2024-01-15")
        assert parsed is not None
        self.assertEqual(parsed.tzinfo, timezone.utc)
        self.assertEqual(parsed.date(), date(2024, 1, 15))

    def test_parse_fred_value_treats_dot_as_missing(self) -> None:
        self.assertIsNone(parse_fred_value("."))
        self.assertIsNone(parse_fred_value(""))
        self.assertEqual(parse_fred_value("3.14"), 3.14)
        self.assertEqual(parse_fred_value(7), 7.0)
        self.assertIsNone(parse_fred_value(True))


class NormalizeSeriesTests(unittest.TestCase):
    def test_extracts_metadata_into_row(self) -> None:
        payload = {
            "seriess": [
                {
                    "id": "DGS10",
                    "title": "Market Yield on U.S. Treasury Securities at 10-Year",
                    "units": "Percent",
                    "units_short": "%",
                    "frequency": "Daily",
                    "frequency_short": "D",
                    "seasonal_adjustment": "Not Seasonally Adjusted",
                    "seasonal_adjustment_short": "NSA",
                    "notes": "Yield on the 10-year constant maturity Treasury",
                    "popularity": 95,
                    "observation_start": "1962-01-02",
                    "observation_end": "2026-05-09",
                    "last_updated": "2026-05-09 15:18:01-05",
                }
            ]
        }
        row = normalize_series(
            payload,
            series_id="DGS10",
            source_endpoint="https://api.stlouisfed.org/fred/series?series_id=DGS10&api_key=***",
            ingest_run_id=42,
            fetched_at=NOW,
        )
        assert row is not None
        self.assertEqual(row["series_id"], "DGS10")
        self.assertEqual(row["frequency"], "Daily")
        self.assertEqual(row["popularity"], 95)
        self.assertEqual(row["observation_start"], date(1962, 1, 2))
        self.assertIsNotNone(row["last_updated"])
        self.assertEqual(row["fetched_at"], NOW)
        self.assertEqual(row["ingest_run_id"], 42)

    def test_returns_none_when_seriess_missing(self) -> None:
        self.assertIsNone(
            normalize_series(
                {"seriess": []}, series_id="X", source_endpoint="x", ingest_run_id=1, fetched_at=NOW
            )
        )
        self.assertIsNone(
            normalize_series(
                {}, series_id="X", source_endpoint="x", ingest_run_id=1, fetched_at=NOW
            )
        )


class NormalizeObservationsTests(unittest.TestCase):
    def test_emits_one_row_per_vintage(self) -> None:
        # Two vintages of the 2024-Q1 observation: first published, then revised.
        payload = {
            "observations": [
                {
                    "date": "2024-01-01",
                    "realtime_start": "2024-04-25",
                    "realtime_end": "2024-05-29",
                    "value": "27000.0",
                },
                {
                    "date": "2024-01-01",
                    "realtime_start": "2024-05-30",
                    "realtime_end": "9999-12-31",
                    "value": "27100.5",
                },
                {
                    "date": "2024-04-01",
                    "realtime_start": "2024-07-25",
                    "realtime_end": "9999-12-31",
                    "value": "27500.0",
                },
            ]
        }
        rows = normalize_observations(
            payload,
            series_id="GDPC1",
            source_endpoint="https://api.stlouisfed.org/fred/series/observations?series_id=GDPC1&api_key=***",
            ingest_run_id=11,
            fetched_at=NOW,
        )
        self.assertEqual(len(rows), 3)
        # Two vintages for the same observation date.
        q1_rows = [r for r in rows if r["ts"].date() == date(2024, 1, 1)]
        self.assertEqual(len(q1_rows), 2)
        self.assertEqual(
            {r["realtime_start"] for r in q1_rows}, {date(2024, 4, 25), date(2024, 5, 30)}
        )
        self.assertEqual({float(r["value"]) for r in q1_rows}, {27000.0, 27100.5})

    def test_drops_invalid_and_dedupes(self) -> None:
        payload = {
            "observations": [
                {
                    "date": "2024-01-15",
                    "realtime_start": "2024-01-15",
                    "realtime_end": "9999-12-31",
                    "value": "1.0",
                },
                {
                    "date": "2024-01-15",
                    "realtime_start": "2024-01-15",
                    "realtime_end": "9999-12-31",
                    "value": "2.0",
                },  # dup
                {
                    "date": "garbage",
                    "realtime_start": "2024-01-15",
                    "realtime_end": "9999-12-31",
                    "value": "1.0",
                },  # bad date
                {
                    "date": "2024-01-16",
                    "realtime_start": "2024-01-16",
                    "realtime_end": "9999-12-31",
                    "value": ".",
                },  # missing value preserved
                "string item",  # dropped
            ]
        }
        rows = normalize_observations(
            payload,
            series_id="DGS10",
            source_endpoint="x",
            ingest_run_id=1,
            fetched_at=NOW,
        )
        self.assertEqual(len(rows), 2)
        # The "missing value" row keeps NULL value but still lands.
        matches = [r for r in rows if r["ts"].date() == date(2024, 1, 16)]
        self.assertTrue(matches)
        missing = matches[0]
        self.assertIsNone(missing["value"])

    def test_returns_empty_for_malformed_payload(self) -> None:
        self.assertEqual(
            normalize_observations(
                "not a dict", series_id="x", source_endpoint="x", ingest_run_id=1, fetched_at=NOW
            ),
            [],
        )
        self.assertEqual(
            normalize_observations(
                {"observations": "not a list"},
                series_id="x",
                source_endpoint="x",
                ingest_run_id=1,
                fetched_at=NOW,
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
