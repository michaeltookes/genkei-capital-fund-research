"""End-to-end test for the SEC collector + normalizer (B-027)."""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from genkei.common import db
from genkei.common.http import HttpClient
from genkei.ingest import sec as ingest
from genkei.normalize import sec as normalizer
from tests._postgres import PostgresTestCase

WATCHLIST = (
    'equities:\n  primary:\n    - symbol: AAPL\n      cik: "0000320193"\n      name: Apple Inc.\n'
)

SUBMISSIONS_PAYLOAD: dict = {
    "cik": "320193",
    "name": "Apple Inc.",
    "tickers": ["AAPL"],
    "exchanges": ["Nasdaq"],
    "sic": "3571",
    "sicDescription": "Electronic Computers",
    "ein": "942404110",
    "fiscalYearEnd": "0928",
    "entityType": "operating",
    "filings": {
        "recent": {
            "accessionNumber": ["0000320193-24-000123"],
            "filingDate": ["2024-01-15"],
            "reportDate": ["2023-12-30"],
            "acceptanceDateTime": ["2024-01-15T16:30:00.000Z"],
            "form": ["10-K"],
            "primaryDocument": ["aapl-20231230.htm"],
            "primaryDocDescription": ["10-K"],
            "fileNumber": ["001-36743"],
            "filmNumber": ["24500001"],
            "items": [None],
            "size": [12345],
            "isXBRL": [1],
            "isInlineXBRL": [1],
        },
        "files": [
            {"name": "CIK0000320193-submissions-001.json", "filingCount": 1},
        ],
    },
}

HISTORY_PAYLOAD: dict = {
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

COMPANYFACTS_PAYLOAD: dict = {
    "cik": 320193,
    "entityName": "Apple Inc.",
    "facts": {
        "us-gaap": {
            "Revenues": {
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
                        }
                    ]
                }
            }
        }
    },
}


def _route(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/submissions/CIK0000320193.json":
        return httpx.Response(200, json=SUBMISSIONS_PAYLOAD)
    if path == "/submissions/CIK0000320193-submissions-001.json":
        return httpx.Response(200, json=HISTORY_PAYLOAD)
    if path == "/api/xbrl/companyfacts/CIK0000320193.json":
        return httpx.Response(200, json=COMPANYFACTS_PAYLOAD)
    return httpx.Response(404, text=f"unmocked: {request.url}")


class SecIntegrationTests(PostgresTestCase):
    def _run_collect(self, http: HttpClient) -> int:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(WATCHLIST, encoding="utf-8")
            return ingest.collect(path, http=http)

    def test_full_run_writes_companies_filings_facts(self) -> None:
        transport = httpx.MockTransport(_route)
        with HttpClient("sec-test", transport=transport) as http:
            run_id = self._run_collect(http)

        # Verify three blobs landed (submissions + history page + companyfacts).
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT endpoint_name FROM meta.raw_blobs "
                "WHERE ingest_run_id = %s ORDER BY endpoint_name",
                [run_id],
            )
            blob_names = [r[0] for r in cur.fetchall()]
        self.assertEqual(
            blob_names,
            [
                "companyfacts_0000320193",
                "submissions_0000320193",
                "submissions_history_0000320193_CIK0000320193-submissions-001.json",
            ],
        )

        normalizer_run, normalized_source_run = normalizer.normalize(source_run_id=run_id)

        with db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*), max(name) FROM sec.companies WHERE cik = '0000320193'")
            companies_count, name = cur.fetchone()
            cur.execute("SELECT count(*) FROM sec.filings WHERE cik = '0000320193'")
            filings_count = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM sec.facts WHERE cik = '0000320193'")
            facts_count = cur.fetchone()[0]
            cur.execute(
                "SELECT status, metadata->>'source_run_id' FROM meta.ingest_runs WHERE id = %s",
                [normalizer_run],
            )
            status, source_run_str = cur.fetchone()

        self.assertEqual(companies_count, 1)
        self.assertEqual(name, "Apple Inc.")
        self.assertEqual(filings_count, 2)  # recent + history
        self.assertEqual(facts_count, 1)
        self.assertEqual(status, "success")
        self.assertEqual(int(source_run_str), run_id)
        self.assertEqual(normalized_source_run, run_id)

    def test_renormalize_is_idempotent(self) -> None:
        transport = httpx.MockTransport(_route)
        with HttpClient("sec-test", transport=transport) as http:
            run_id = self._run_collect(http)

        normalizer.normalize(source_run_id=run_id)
        normalizer.normalize(source_run_id=run_id)

        with db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM sec.companies")
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute("SELECT count(*) FROM sec.filings")
            self.assertEqual(cur.fetchone()[0], 2)
            cur.execute("SELECT count(*) FROM sec.facts")
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute(
                "SELECT period_end, value FROM sec.facts WHERE concept = 'us-gaap:Revenues'"
            )
            period, value = cur.fetchone()
        self.assertEqual(period, date(2023, 12, 31))
        self.assertEqual(float(value), 383285000000)


if __name__ == "__main__":
    unittest.main()
