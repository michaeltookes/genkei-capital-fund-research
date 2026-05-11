"""Unit tests for the SEC normalizer (offline)."""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from genkei.normalize.sec import (
    _as_numeric,
    is_sec_blob_endpoint,
    main,
    normalize,
    normalize_company,
    normalize_facts,
    normalize_filings,
    parse_sec_date,
    parse_sec_datetime,
)

NOW = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)


class HelperTests(unittest.TestCase):
    def test_parse_sec_date(self) -> None:
        self.assertEqual(parse_sec_date("2024-01-15"), date(2024, 1, 15))
        self.assertIsNone(parse_sec_date("garbage"))
        self.assertIsNone(parse_sec_date(None))

    def test_parse_sec_datetime(self) -> None:
        parsed = parse_sec_datetime("2024-05-09T16:30:00.000Z")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.tzinfo, timezone.utc)
        self.assertEqual(parsed.year, 2024)

    def test_as_numeric_preserves_decimal_precision(self) -> None:
        self.assertEqual(_as_numeric("1234567890.123456789"), Decimal("1234567890.123456789"))
        self.assertIsNone(_as_numeric(True))

    def test_identifies_sec_raw_blob_endpoint_names(self) -> None:
        self.assertTrue(is_sec_blob_endpoint("submissions_0000320193"))
        self.assertTrue(is_sec_blob_endpoint("submissions_history_0000320193_file.json"))
        self.assertTrue(is_sec_blob_endpoint("companyfacts_0000320193"))
        self.assertFalse(is_sec_blob_endpoint("observations_GDPC1"))


class CliTests(unittest.TestCase):
    def test_json_output_uses_resolved_source_run_id(self) -> None:
        output = io.StringIO()
        with patch("genkei.normalize.sec.normalize", return_value=(9, 7)), redirect_stdout(output):
            self.assertEqual(main(["--json"]), 0)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["ingest_run_id"], 9)
        self.assertEqual(payload["source_run_id"], 7)

    def test_empty_argv_does_not_fall_back_to_process_args(self) -> None:
        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["prog", "--bad-flag"]),
            patch("genkei.normalize.sec.normalize", return_value=(9, 7)),
            redirect_stdout(output),
        ):
            self.assertEqual(main([]), 0)


class NormalizeRunTests(unittest.TestCase):
    def test_rejects_source_run_without_sec_blobs(self) -> None:
        with (
            patch(
                "genkei.normalize.sec.fetch_raw_blobs",
                return_value={"observations_GDPC1": ("x", {}, NOW)},
            ),
            self.assertRaisesRegex(SystemExit, "No SEC raw blobs"),
        ):
            normalize(source_run_id=123)


class NormalizeCompanyTests(unittest.TestCase):
    def test_extracts_metadata(self) -> None:
        payload = {
            "cik": "320193",
            "name": "Apple Inc.",
            "tickers": ["AAPL"],
            "exchanges": ["Nasdaq"],
            "sic": "3571",
            "sicDescription": "Electronic Computers",
            "ein": "942404110",
            "fiscalYearEnd": "0928",
            "entityType": "operating",
            "formerNames": [],
        }
        row = normalize_company(
            payload,
            cik="0000320193",
            source_endpoint="x",
            ingest_run_id=42,
            fetched_at=NOW,
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["cik"], "0000320193")
        self.assertEqual(row["ticker"], "AAPL")
        self.assertEqual(row["name"], "Apple Inc.")
        self.assertEqual(row["sic"], "3571")
        self.assertEqual(row["exchanges"], ["Nasdaq"])

    def test_returns_none_without_name(self) -> None:
        self.assertIsNone(
            normalize_company({}, cik="x", source_endpoint="x", ingest_run_id=1, fetched_at=NOW)
        )


class NormalizeFilingsTests(unittest.TestCase):
    def test_walks_parallel_arrays_into_rows(self) -> None:
        payload = {
            "filings": {
                "recent": {
                    "accessionNumber": ["0000320193-24-000123", "0000320193-24-000124"],
                    "filingDate": ["2024-01-15", "2024-02-01"],
                    "reportDate": ["2023-12-30", "2024-01-31"],
                    "acceptanceDateTime": [
                        "2024-01-15T16:30:00.000Z",
                        "2024-02-01T17:00:00.000Z",
                    ],
                    "form": ["10-K", "8-K"],
                    "primaryDocument": ["aapl-20231230.htm", "aapl-20240131.htm"],
                    "primaryDocDescription": ["10-K", "8-K"],
                    "fileNumber": ["001-36743", "001-36743"],
                    "filmNumber": ["24500001", "24500002"],
                    "items": [None, "2.02,9.01"],
                    "size": [12345, 6789],
                    "isXBRL": [1, 0],
                    "isInlineXBRL": [1, 0],
                }
            }
        }
        rows = normalize_filings(
            payload,
            cik="0000320193",
            source_endpoint="https://data.sec.gov/submissions/CIK0000320193.json",
            ingest_run_id=11,
            fetched_at=NOW,
        )
        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first["accession_number"], "0000320193-24-000123")
        self.assertEqual(first["form_type"], "10-K")
        self.assertEqual(first["filed_at"], date(2024, 1, 15))
        self.assertTrue(first["is_xbrl"])
        self.assertEqual(first["size_bytes"], 12345)
        self.assertEqual(rows[1]["items"], "2.02,9.01")
        self.assertFalse(rows[1]["is_xbrl"])

    def test_history_page_reads_payload_root(self) -> None:
        payload = {
            "accessionNumber": ["0000320193-15-000001"],
            "filingDate": ["2015-10-29"],
            "reportDate": ["2015-09-26"],
            "acceptanceDateTime": [None],
            "form": ["10-K"],
            "primaryDocument": ["aapl-20150926.htm"],
            "primaryDocDescription": ["10-K"],
            "fileNumber": ["001-36743"],
            "filmNumber": ["151181420"],
            "items": [None],
            "size": [555555],
            "isXBRL": [1],
            "isInlineXBRL": [0],
        }
        rows = normalize_filings(
            payload,
            cik="0000320193",
            source_endpoint="https://data.sec.gov/submissions/CIK0000320193-submissions-001.json",
            ingest_run_id=1,
            fetched_at=NOW,
            is_history_page=True,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["filed_at"], date(2015, 10, 29))

    def test_drops_invalid_and_dedupes(self) -> None:
        payload = {
            "filings": {
                "recent": {
                    "accessionNumber": [
                        "good-1",
                        None,  # missing accession
                        "good-1",  # duplicate
                        "good-2",
                    ],
                    "form": ["10-K", "10-K", "10-K", None],  # last has no form -> dropped
                    "filingDate": [
                        "2024-01-15",
                        "2024-01-16",
                        "2024-01-15",
                        "2024-01-17",
                    ],
                }
            }
        }
        rows = normalize_filings(
            payload, cik="x", source_endpoint="x", ingest_run_id=1, fetched_at=NOW
        )
        self.assertEqual([r["accession_number"] for r in rows], ["good-1"])

    def test_returns_empty_for_malformed_payload(self) -> None:
        self.assertEqual(
            normalize_filings(
                "not a dict", cik="x", source_endpoint="x", ingest_run_id=1, fetched_at=NOW
            ),
            [],
        )


class NormalizeFactsTests(unittest.TestCase):
    def test_walks_taxonomy_concept_unit_hierarchy(self) -> None:
        payload = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "label": "Revenues",
                        "units": {
                            "USD": [
                                {
                                    "start": "2023-01-01",
                                    "end": "2023-12-31",
                                    "val": 383285000000,
                                    "accn": "0000320193-24-000123",
                                    "form": "10-K",
                                    "filed": "2024-01-15",
                                    "frame": "CY2023",
                                    "fy": 2023,
                                    "fp": "FY",
                                },
                                {
                                    "start": "2023-04-01",
                                    "end": "2023-06-30",
                                    "val": 81797000000,
                                    "accn": "0000320193-23-000077",
                                    "form": "10-Q",
                                    "filed": "2023-08-04",
                                    "fy": 2023,
                                    "fp": "Q3",
                                },
                            ]
                        },
                    },
                    "EarningsPerShareBasic": {
                        "units": {
                            "USD/shares": [
                                {
                                    "end": "2023-12-31",
                                    "val": 6.16,
                                    "accn": "0000320193-24-000123",
                                    "form": "10-K",
                                }
                            ]
                        }
                    },
                },
                "dei": {
                    "EntityCommonStockSharesOutstanding": {
                        "units": {
                            "shares": [
                                {
                                    "end": "2024-01-19",
                                    "val": 15441881000,
                                    "accn": "0000320193-24-000123",
                                }
                            ]
                        }
                    }
                },
            }
        }
        rows = normalize_facts(
            payload,
            cik="0000320193",
            source_endpoint="https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
            ingest_run_id=22,
            fetched_at=NOW,
        )
        self.assertEqual(len(rows), 4)
        concepts = sorted({r["concept"] for r in rows})
        self.assertEqual(
            concepts,
            [
                "dei:EntityCommonStockSharesOutstanding",
                "us-gaap:EarningsPerShareBasic",
                "us-gaap:Revenues",
            ],
        )
        units = sorted({r["unit"] for r in rows})
        self.assertEqual(units, ["USD", "USD/shares", "shares"])
        instant_rows = [r for r in rows if r["concept"] == "us-gaap:EarningsPerShareBasic"]
        self.assertEqual(instant_rows[0]["period_start"], instant_rows[0]["period_end"])
        revenue_rows = [r for r in rows if r["concept"] == "us-gaap:Revenues"]
        self.assertEqual(len(revenue_rows), 2)
        # Both annual and quarterly Revenues land — accession_number disambiguates.
        accns = {r["accession_number"] for r in revenue_rows}
        self.assertEqual(accns, {"0000320193-23-000077", "0000320193-24-000123"})

    def test_drops_facts_without_period_end_or_accn(self) -> None:
        payload = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "USD": [
                                {"end": "2024-01-01", "val": 1.0, "accn": "good"},
                                {"val": 2.0, "accn": "no-end"},  # dropped
                                {"end": "2024-01-01", "val": 3.0},  # no accn -> dropped
                            ]
                        }
                    }
                }
            }
        }
        rows = normalize_facts(
            payload, cik="x", source_endpoint="x", ingest_run_id=1, fetched_at=NOW
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["accession_number"], "good")

    def test_returns_empty_for_malformed_payload(self) -> None:
        self.assertEqual(
            normalize_facts("x", cik="x", source_endpoint="x", ingest_run_id=1, fetched_at=NOW),
            [],
        )
        self.assertEqual(
            normalize_facts(
                {"facts": "not a dict"},
                cik="x",
                source_endpoint="x",
                ingest_run_id=1,
                fetched_at=NOW,
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
