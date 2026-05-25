"""Unit tests for SEC 13F XML / submissions normalization (B-080).

Pins the two acceptance criteria from B-080:
  (a) the value field is in $1000s — multiplication is applied at
      normalize time so the column carries dollars
  (b) 13F-NT (notice) amendments correctly link back to a 13F-HR
      via shared (filer_cik, period_of_report) — the NT is captured
      as a filings row with zero holdings rows.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from genkei.normalize.sec_form13f import (
    derive_report_type,
    normalize_filer,
    normalize_form13f_filings,
    parse_form13f_xml,
)

NOW = datetime(2026, 5, 25, tzinfo=timezone.utc)


# Two-holding 13F information table mirroring the SEC's published schema
# (http://www.sec.gov/edgar/document/thirteenf/informationtable). Values
# are expressed in $1000s — see the value=42 → value_usd=42000 assertion.
SAMPLE_INFO_TABLE = """<?xml version="1.0" encoding="UTF-8"?>
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
  <infoTable>
    <nameOfIssuer>SALESFORCE INC</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>79466L302</cusip>
    <value>1500</value>
    <shrsOrPrnAmt>
      <sshPrnamt>5000</sshPrnamt>
      <sshPrnamtType>SH</sshPrnamtType>
    </shrsOrPrnAmt>
    <putCall>Call</putCall>
    <investmentDiscretion>DEFINED</investmentDiscretion>
    <otherManager>1,2</otherManager>
    <votingAuthority>
      <Sole>5000</Sole>
      <Shared>0</Shared>
      <None>0</None>
    </votingAuthority>
  </infoTable>
</informationTable>"""


# No-CUSIP variant: every infoTable's <cusip> child is required by the SEC
# schema, but defensive parsing should drop the row rather than fail the
# whole batch.
SAMPLE_INFO_TABLE_MISSING_CUSIP = """<?xml version="1.0"?>
<informationTable>
  <infoTable>
    <nameOfIssuer>APPLE INC</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <value>1000</value>
    <shrsOrPrnAmt>
      <sshPrnamt>200</sshPrnamt>
      <sshPrnamtType>SH</sshPrnamtType>
    </shrsOrPrnAmt>
  </infoTable>
</informationTable>"""


class ValueInThousandsGotchaTests(unittest.TestCase):
    """B-080 acceptance criterion (a): <value> is in $1000s."""

    def test_multiplies_value_by_one_thousand(self) -> None:
        rows = parse_form13f_xml(
            SAMPLE_INFO_TABLE,
            accession_number="0001067983-25-000001",
            filer_cik="0001067983",
            period_of_report=date(2025, 3, 31),
            source_endpoint="https://example.com/infotable.xml",
            ingest_run_id=1,
            fetched_at=NOW,
        )
        # First holding: <value>42</value> → 42 * 1000 = 42000 dollars.
        self.assertEqual(rows[0]["value_usd"], Decimal("42000"))
        # Second holding: <value>1500</value> → 1.5M dollars.
        self.assertEqual(rows[1]["value_usd"], Decimal("1500000"))


class ParseInfoTableTests(unittest.TestCase):
    def test_parses_canonical_two_holding_table(self) -> None:
        rows = parse_form13f_xml(
            SAMPLE_INFO_TABLE,
            accession_number="0001067983-25-000001",
            filer_cik="0001067983",
            period_of_report=date(2025, 3, 31),
            source_endpoint="https://example.com/infotable.xml",
            ingest_run_id=42,
            fetched_at=NOW,
        )
        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first["accession_number"], "0001067983-25-000001")
        self.assertEqual(first["holding_idx"], 0)
        self.assertEqual(first["filer_cik"], "0001067983")
        self.assertEqual(first["period_of_report"], date(2025, 3, 31))
        self.assertEqual(first["cusip"], "037833100")
        self.assertEqual(first["issuer_name"], "APPLE INC")
        self.assertEqual(first["class_title"], "COM")
        self.assertEqual(first["shares_or_principal"], Decimal("200"))
        self.assertEqual(first["shares_or_principal_type"], "SH")
        self.assertEqual(first["investment_discretion"], "SOLE")
        self.assertEqual(first["voting_authority_sole"], Decimal("200"))
        self.assertEqual(first["voting_authority_shared"], Decimal("0"))
        self.assertEqual(first["voting_authority_none"], Decimal("0"))
        self.assertIsNone(first["put_call"])
        self.assertIsNone(first["other_managers"])
        # Second holding's options-on-CRM example exercises put_call + otherManagers.
        second = rows[1]
        self.assertEqual(second["holding_idx"], 1)
        self.assertEqual(second["put_call"], "Call")
        self.assertEqual(second["other_managers"], "1,2")

    def test_skips_rows_without_cusip(self) -> None:
        rows = parse_form13f_xml(
            SAMPLE_INFO_TABLE_MISSING_CUSIP,
            accession_number="A",
            filer_cik="0001067983",
            period_of_report=date(2025, 3, 31),
            source_endpoint="x",
            ingest_run_id=1,
            fetched_at=NOW,
        )
        self.assertEqual(rows, [])

    def test_returns_empty_for_malformed_xml(self) -> None:
        rows = parse_form13f_xml(
            "<not xml",
            accession_number="A",
            filer_cik="0001067983",
            period_of_report=date(2025, 3, 31),
            source_endpoint="x",
            ingest_run_id=1,
            fetched_at=NOW,
        )
        self.assertEqual(rows, [])


SUBMISSIONS_PAYLOAD = {
    "name": "Berkshire Hathaway Inc",
    "filings": {
        "recent": {
            "accessionNumber": [
                "0001067983-25-000001",  # 13F-HR Q1 2025
                "0001067983-25-000002",  # 13F-NT Q1 2025 — notice-only, references HR
                "0001067983-25-000003",  # 8-K (must be filtered out)
            ],
            "form": ["13F-HR", "13F-NT", "8-K"],
            "filingDate": ["2025-05-15", "2025-05-15", "2025-04-01"],
            "acceptanceDateTime": [
                "2025-05-15T16:30:00.000Z",
                "2025-05-15T16:31:00.000Z",
                "2025-04-01T09:00:00.000Z",
            ],
            "reportDate": ["2025-03-31", "2025-03-31", ""],
            "primaryDocument": [
                "primary_doc.xml",
                "primary_doc.xml",
                "ek.htm",
            ],
            "primaryDocDescription": [
                "13F-HR",
                "13F-NT",
                "8-K cover",
            ],
        }
    },
}


class NormalizeFilerTests(unittest.TestCase):
    def test_picks_name_from_submissions_payload(self) -> None:
        row = normalize_filer(
            SUBMISSIONS_PAYLOAD,
            filer_cik="0001067983",
            source_endpoint="https://example.com/submissions",
            ingest_run_id=1,
            fetched_at=NOW,
        )
        assert row is not None  # narrowing
        self.assertEqual(row["filer_cik"], "0001067983")
        self.assertEqual(row["name"], "Berkshire Hathaway Inc")

    def test_returns_none_when_name_missing(self) -> None:
        self.assertIsNone(
            normalize_filer(
                {},
                filer_cik="0001067983",
                source_endpoint="x",
                ingest_run_id=1,
                fetched_at=NOW,
            )
        )


class NormalizeFilingsTests(unittest.TestCase):
    """Acceptance criterion (b): 13F-NT amendments captured as filings, no holdings."""

    def test_filters_to_13f_forms_and_captures_both_hr_and_nt(self) -> None:
        rows = normalize_form13f_filings(
            SUBMISSIONS_PAYLOAD,
            filer_cik="0001067983",
            source_endpoint="https://example.com/submissions",
            ingest_run_id=1,
            fetched_at=NOW,
        )
        # 8-K excluded; both 13F variants land.
        self.assertEqual(len(rows), 2)
        forms = [r["form_type"] for r in rows]
        self.assertCountEqual(forms, ["13F-HR", "13F-NT"])

    def test_hr_and_nt_share_period_of_report(self) -> None:
        # This is the linkage 13F-NT relies on: the NT references the HR
        # via the same `periodOfReport`. Once normalized into our table,
        # a SQL join on (filer_cik, period_of_report) reconciles them.
        rows = normalize_form13f_filings(
            SUBMISSIONS_PAYLOAD,
            filer_cik="0001067983",
            source_endpoint="x",
            ingest_run_id=1,
            fetched_at=NOW,
        )
        by_form = {r["form_type"]: r for r in rows}
        self.assertEqual(by_form["13F-HR"]["period_of_report"], date(2025, 3, 31))
        self.assertEqual(by_form["13F-NT"]["period_of_report"], date(2025, 3, 31))
        # NT's report_type label distinguishes it from the holdings report.
        self.assertEqual(by_form["13F-HR"]["report_type"], "HOLDINGS REPORT")
        self.assertEqual(by_form["13F-NT"]["report_type"], "NOTICE")

    def test_history_page_uses_root_parallel_arrays(self) -> None:
        history_payload = {
            "accessionNumber": ["0001067983-20-000010"],
            "form": ["13F-HR"],
            "filingDate": ["2020-05-15"],
            "acceptanceDateTime": ["2020-05-15T16:30:00.000Z"],
            "reportDate": ["2020-03-31"],
            "primaryDocument": ["primary_doc.xml"],
            "primaryDocDescription": ["13F-HR"],
        }
        rows = normalize_form13f_filings(
            history_payload,
            filer_cik="0001067983",
            source_endpoint="x",
            ingest_run_id=1,
            fetched_at=NOW,
            is_history_page=True,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["period_of_report"], date(2020, 3, 31))


class DeriveReportTypeTests(unittest.TestCase):
    def test_known_form_types_map_to_labels(self) -> None:
        self.assertEqual(derive_report_type("13F-HR"), "HOLDINGS REPORT")
        self.assertEqual(derive_report_type("13F-HR/A"), "HOLDINGS REPORT")
        self.assertEqual(derive_report_type("13F-NT"), "NOTICE")
        self.assertEqual(derive_report_type("13F-NT/A"), "NOTICE")
        self.assertEqual(derive_report_type("13F-CTR"), "COMBINATION")
        self.assertEqual(derive_report_type("13F-CTR/A"), "COMBINATION")

    def test_unknown_form_type_returns_none(self) -> None:
        self.assertIsNone(derive_report_type("10-K"))


if __name__ == "__main__":
    unittest.main()
