"""End-to-end test for the SEC 13F collector + normalizer (B-080).

Exercises the full Phase A (submissions) → Phase B (info-table XML) →
normalize path against the testcontainers Postgres harness. Pins the
two acceptance criteria from B-080:

  (a) value field is in $1000s — verified by SELECTing a known
      holding from `sec.form13f_holdings.value_usd` and asserting it
      matches the raw <value> × 1000.

  (b) 13F-NT amendments link back to the 13F-HR — verified by
      filing-row count (both NT + HR present), holdings-row count
      (only the HR has holdings), and a SQL join on shared
      (filer_cik, period_of_report) reconciling them.
"""

from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from genkei.common import db
from genkei.common.http import HttpClient
from genkei.ingest import sec_form13f as ingest
from genkei.normalize import sec_form13f as normalizer
from tests._postgres import get_harness, postgres_required

WATCHLIST = (
    "filers:\n"
    "  primary:\n"
    "    - cik: 1067983\n"
    "      name: Berkshire Hathaway Inc\n"
)

SUBMISSIONS_PAYLOAD: dict = {
    "cik": "1067983",
    "name": "Berkshire Hathaway Inc",
    "filings": {
        "recent": {
            "accessionNumber": [
                "0001067983-25-000001",
                "0001067983-25-000002",
                "0001067983-25-000099",
            ],
            "filingDate": ["2025-05-15", "2025-05-15", "2025-04-01"],
            "reportDate": ["2025-03-31", "2025-03-31", ""],
            "acceptanceDateTime": [
                "2025-05-15T16:30:00.000Z",
                "2025-05-15T16:31:00.000Z",
                "2025-04-01T09:00:00.000Z",
            ],
            "form": ["13F-HR", "13F-NT", "8-K"],
            "primaryDocument": ["primary_doc.xml", "primary_doc.xml", "ek.htm"],
            "primaryDocDescription": ["13F-HR", "13F-NT", "Current report"],
        },
        "files": [],
    },
}

# index.json shape for the 13F-HR filing — directs Phase B at infotable.xml.
INDEX_PAYLOAD: dict = {
    "directory": {
        "item": [
            {"name": "primary_doc.xml", "size": "5000"},
            {"name": "infotable.xml", "size": "20000"},
        ]
    }
}

INFO_TABLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable>
    <nameOfIssuer>APPLE INC</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>037833100</cusip>
    <value>42</value>
    <shrsOrPrnAmt>
      <sshPrnamt>200</sshPrnamt>
      <sshPrnamtType>SH</sshPrnamtType>
    </shrsOrPrnAmt>
    <investmentDiscretion>SOLE</investmentDiscretion>
    <votingAuthority>
      <Sole>200</Sole>
      <Shared>0</Shared>
      <None>0</None>
    </votingAuthority>
  </infoTable>
</informationTable>"""


def _route(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/submissions/CIK0001067983.json":
        return httpx.Response(200, json=SUBMISSIONS_PAYLOAD)
    if path.endswith("/000106798325000001/index.json"):
        return httpx.Response(200, json=INDEX_PAYLOAD)
    if path.endswith("/000106798325000001/infotable.xml"):
        return httpx.Response(200, text=INFO_TABLE_XML)
    return httpx.Response(404, text=f"unmocked: {request.url}")


@postgres_required
class Form13FIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.harness = get_harness()

    def setUp(self) -> None:
        from psycopg_pool import ConnectionPool

        self.harness.truncate_all()
        db.reset_pool()
        self._pool = ConnectionPool(
            conninfo=self.harness.url, min_size=1, max_size=2, open=True
        )
        db.set_pool(self._pool)

    def tearDown(self) -> None:
        db.reset_pool()
        self._pool.close()
        self.harness.truncate_all()

    def _run_collect(self, http: HttpClient) -> int:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(WATCHLIST, encoding="utf-8")
            return ingest.collect(path, http=http)

    def test_full_run_writes_filers_filings_holdings(self) -> None:
        transport = httpx.MockTransport(_route)
        with HttpClient("sec-13f-test", transport=transport) as http:
            run_id = self._run_collect(http)

        # Two blobs land in Phase A (submissions) + Phase B (infotable).
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
                "form13f_0001067983-25-000001",
                "submissions_filer_0001067983",
            ],
        )

        normalizer_run, processed = normalizer.normalize(source_run_id=run_id)
        self.assertEqual(processed, 1)

        with db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*), max(name) FROM sec.filers")
            filer_count, filer_name = cur.fetchone()
            cur.execute(
                "SELECT count(*), array_agg(form_type ORDER BY form_type) "
                "FROM sec.form13f_filings"
            )
            filings_count, forms = cur.fetchone()
            cur.execute("SELECT count(*) FROM sec.form13f_holdings")
            holdings_count = cur.fetchone()[0]
            cur.execute(
                "SELECT cusip, value_usd, shares_or_principal "
                "FROM sec.form13f_holdings WHERE accession_number = %s",
                ["0001067983-25-000001"],
            )
            cusip, value_usd, shares = cur.fetchone()

        # Filer + filings + holdings + provenance.
        self.assertEqual(filer_count, 1)
        self.assertEqual(filer_name, "Berkshire Hathaway Inc")
        # Both 13F-HR and 13F-NT lit up as filings rows; 8-K filtered out.
        self.assertEqual(filings_count, 2)
        self.assertCountEqual(forms, ["13F-HR", "13F-NT"])
        # Only the HR contributes a holdings row — NT is notice-only.
        self.assertEqual(holdings_count, 1)
        self.assertEqual(cusip, "037833100")
        # **Acceptance criterion (a)**: <value>42</value> in $1000s →
        # 42000 dollars in the column.
        self.assertEqual(value_usd, Decimal("42000"))
        self.assertEqual(shares, Decimal("200"))

    def test_nt_links_to_hr_via_shared_period(self) -> None:
        """Acceptance criterion (b): 13F-NT references the 13F-HR via period."""
        transport = httpx.MockTransport(_route)
        with HttpClient("sec-13f-test", transport=transport) as http:
            run_id = self._run_collect(http)
        normalizer.normalize(source_run_id=run_id)

        with db.connection() as conn, conn.cursor() as cur:
            # Join NT → HR via filer + period_of_report; expect the HR's
            # holdings count to be visible from the NT side.
            cur.execute(
                """
                SELECT nt.accession_number, nt.report_type,
                       hr.accession_number, hr.report_type,
                       (SELECT count(*) FROM sec.form13f_holdings
                        WHERE accession_number = hr.accession_number) AS hr_holdings,
                       (SELECT count(*) FROM sec.form13f_holdings
                        WHERE accession_number = nt.accession_number) AS nt_holdings
                FROM sec.form13f_filings nt
                JOIN sec.form13f_filings hr
                  ON hr.filer_cik = nt.filer_cik
                 AND hr.period_of_report = nt.period_of_report
                 AND hr.accession_number != nt.accession_number
                WHERE nt.form_type = '13F-NT'
                """
            )
            row = cur.fetchone()

        self.assertIsNotNone(row)
        nt_accn, nt_report_type, hr_accn, hr_report_type, hr_holdings, nt_holdings = row
        self.assertEqual(nt_report_type, "NOTICE")
        self.assertEqual(hr_report_type, "HOLDINGS REPORT")
        self.assertEqual(hr_accn, "0001067983-25-000001")
        self.assertEqual(nt_accn, "0001067983-25-000002")
        # HR has the holdings; NT has none. Joining them recovers the
        # full positioning picture without double-counting.
        self.assertEqual(hr_holdings, 1)
        self.assertEqual(nt_holdings, 0)

    def test_renormalize_and_recollect_are_idempotent(self) -> None:
        transport = httpx.MockTransport(_route)
        with HttpClient("sec-13f-test", transport=transport) as http:
            run_id = self._run_collect(http)
        normalizer.normalize(source_run_id=run_id)
        normalizer.normalize(source_run_id=run_id)

        # A second collect run would already see the accession in
        # sec.form13f_normalized_filings and skip the Phase B fetch.
        with HttpClient("sec-13f-test", transport=transport) as http:
            run_id_2 = self._run_collect(http)

        with db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM sec.form13f_holdings")
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute("SELECT count(*) FROM sec.form13f_filings")
            self.assertEqual(cur.fetchone()[0], 2)
            # Only the submissions blob lands in the second run — Phase B
            # finds the accession already normalized and skips.
            cur.execute(
                "SELECT endpoint_name FROM meta.raw_blobs "
                "WHERE ingest_run_id = %s ORDER BY endpoint_name",
                [run_id_2],
            )
            second_run_blobs = [r[0] for r in cur.fetchall()]
        self.assertEqual(second_run_blobs, ["submissions_filer_0001067983"])


if __name__ == "__main__":
    unittest.main()
