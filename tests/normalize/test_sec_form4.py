"""Unit tests for Form 4 XML parsing (B-079)."""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from genkei.normalize import sec_form4
from genkei.normalize.sec_form4 import (
    _payload_to_xml,
    fetch_unnormalized_form4_blobs,
    normalize,
    parse_form4_xml,
)

NOW = datetime(2026, 5, 16, tzinfo=timezone.utc)


# Real AAPL Form 4, accession 0001140361-26-020871 (Ben Borders, May 8 sale)
SAMPLE_NON_DERIVATIVE = """<?xml version="1.0"?>
<ownershipDocument>
  <schemaVersion>X0609</schemaVersion>
  <documentType>4</documentType>
  <periodOfReport>2026-05-08</periodOfReport>
  <issuer>
    <issuerCik>0000320193</issuerCik>
    <issuerName>Apple Inc.</issuerName>
    <issuerTradingSymbol>AAPL</issuerTradingSymbol>
  </issuer>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>0002100523</rptOwnerCik>
      <rptOwnerName>Borders Ben</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerRelationship>
      <isOfficer>true</isOfficer>
      <officerTitle>Principal Accounting Officer</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2026-05-08</value></transactionDate>
      <transactionCoding>
        <transactionFormType>4</transactionFormType>
        <transactionCode>S</transactionCode>
        <equitySwapInvolved>0</equitySwapInvolved>
      </transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1274</value></transactionShares>
        <transactionPricePerShare><value>290</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
      <postTransactionAmounts>
        <sharesOwnedFollowingTransaction><value>38713</value></sharesOwnedFollowingTransaction>
      </postTransactionAmounts>
      <ownershipNature>
        <directOrIndirectOwnership><value>D</value></directOrIndirectOwnership>
      </ownershipNature>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


SAMPLE_DERIVATIVE = """<?xml version="1.0"?>
<ownershipDocument>
  <documentType>4</documentType>
  <periodOfReport>2024-03-15</periodOfReport>
  <issuer>
    <issuerCik>0000320193</issuerCik>
    <issuerName>Apple Inc.</issuerName>
  </issuer>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>0001214156</rptOwnerCik>
      <rptOwnerName>Cook Timothy D</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>true</isDirector>
      <isOfficer>true</isOfficer>
      <officerTitle>CEO</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <derivativeTable>
    <derivativeTransaction>
      <securityTitle><value>Employee Stock Option (Right to Buy)</value></securityTitle>
      <conversionOrExercisePrice><value>120.5</value></conversionOrExercisePrice>
      <transactionDate><value>2024-03-15</value></transactionDate>
      <transactionCoding>
        <transactionCode>M</transactionCode>
      </transactionCoding>
      <transactionAmounts>
        <transactionShares><value>500</value></transactionShares>
        <transactionPricePerShare><value>0</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
      <exerciseDate><value>2024-03-15</value></exerciseDate>
      <expirationDate><value>2031-03-15</value></expirationDate>
      <underlyingSecurity>
        <underlyingSecurityTitle><value>Common Stock</value></underlyingSecurityTitle>
        <underlyingSecurityShares><value>500</value></underlyingSecurityShares>
      </underlyingSecurity>
      <postTransactionAmounts>
        <sharesOwnedFollowingTransaction><value>0</value></sharesOwnedFollowingTransaction>
      </postTransactionAmounts>
      <ownershipNature>
        <directOrIndirectOwnership><value>D</value></directOrIndirectOwnership>
      </ownershipNature>
    </derivativeTransaction>
  </derivativeTable>
</ownershipDocument>"""


SAMPLE_MULTI_REPORTER = """<?xml version="1.0"?>
<ownershipDocument>
  <documentType>4</documentType>
  <periodOfReport>2024-06-01</periodOfReport>
  <issuer>
    <issuerCik>0000320193</issuerCik>
    <issuerName>Apple Inc.</issuerName>
  </issuer>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>0000111111</rptOwnerCik>
      <rptOwnerName>Alice Trust</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerRelationship>
      <isTenPercentOwner>true</isTenPercentOwner>
    </reportingOwnerRelationship>
  </reportingOwner>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>0000222222</rptOwnerCik>
      <rptOwnerName>Bob Trust</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerRelationship>
      <isOther>true</isOther>
      <otherText>Co-trustee</otherText>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2024-06-01</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1000</value></transactionShares>
        <transactionPricePerShare><value>200.50</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


class ParseNonDerivativeTests(unittest.TestCase):
    def test_parses_canonical_aapl_sale(self) -> None:
        insiders, transactions = parse_form4_xml(
            SAMPLE_NON_DERIVATIVE,
            accession_number="0001140361-26-020871",
            source_endpoint="https://example.com/form4.xml",
            ingest_run_id=42,
            fetched_at=NOW,
        )
        self.assertEqual(len(insiders), 1)
        self.assertEqual(insiders[0]["reporter_cik"], "0002100523")
        self.assertEqual(insiders[0]["reporter_name"], "Borders Ben")
        self.assertEqual(insiders[0]["last_seen_at"], NOW)
        self.assertEqual(len(transactions), 1)
        t = transactions[0]
        self.assertEqual(t["accession_number"], "0001140361-26-020871")
        self.assertEqual(t["transaction_idx"], 0)
        self.assertEqual(t["issuer_cik"], "0000320193")
        self.assertEqual(t["reporter_cik"], "0002100523")
        self.assertTrue(t["is_officer"])
        self.assertEqual(t["officer_title"], "Principal Accounting Officer")
        self.assertEqual(t["transaction_date"], date(2026, 5, 8))
        self.assertEqual(t["transaction_code"], "S")
        self.assertEqual(t["acquired_disposed"], "D")
        self.assertEqual(t["security_title"], "Common Stock")
        self.assertFalse(t["is_derivative"])
        self.assertEqual(t["shares"], Decimal("1274"))
        self.assertEqual(t["price_usd"], Decimal("290"))
        self.assertEqual(t["post_transaction_shares"], Decimal("38713"))
        self.assertEqual(t["ownership_type"], "D")
        self.assertEqual(t["period_of_report"], date(2026, 5, 8))
        # Derivative-only fields are None on non-derivative rows
        self.assertIsNone(t["underlying_security_title"])
        self.assertIsNone(t["conversion_or_exercise_price"])


class ParseDerivativeTests(unittest.TestCase):
    def test_parses_option_exercise(self) -> None:
        insiders, transactions = parse_form4_xml(
            SAMPLE_DERIVATIVE,
            accession_number="0000000000-24-000001",
            source_endpoint="x",
            ingest_run_id=1,
            fetched_at=NOW,
        )
        self.assertEqual(insiders[0]["reporter_cik"], "0001214156")
        # Relationship flags live on the transaction row, not the
        # insiders dim (that table just holds reporter_cik + name).
        t = transactions[0]
        self.assertTrue(t["is_director"])
        self.assertTrue(t["is_officer"])
        self.assertEqual(t["officer_title"], "CEO")
        self.assertTrue(t["is_derivative"])
        self.assertEqual(t["transaction_code"], "M")
        self.assertEqual(t["security_title"], "Employee Stock Option (Right to Buy)")
        self.assertEqual(t["conversion_or_exercise_price"], Decimal("120.5"))
        self.assertEqual(t["underlying_security_title"], "Common Stock")
        self.assertEqual(t["underlying_shares"], Decimal("500"))
        self.assertEqual(t["exercise_date"], date(2024, 3, 15))
        self.assertEqual(t["expiration_date"], date(2031, 3, 15))


class ParseMultiReporterTests(unittest.TestCase):
    def test_emits_one_insider_per_reporter_and_one_txn_per_reporter(self) -> None:
        insiders, transactions = parse_form4_xml(
            SAMPLE_MULTI_REPORTER,
            accession_number="0000000000-24-000002",
            source_endpoint="x",
            ingest_run_id=1,
            fetched_at=NOW,
        )
        # Two reporters → two insider rows
        self.assertEqual({i["reporter_cik"] for i in insiders}, {"0000111111", "0000222222"})
        # One transaction in the filing, attributed to BOTH reporters → 2 rows
        self.assertEqual(len(transactions), 2)
        self.assertEqual({t["reporter_cik"] for t in transactions}, {"0000111111", "0000222222"})
        # transaction_idx is unique per row even though they cover the same XML transaction
        idxs = sorted(t["transaction_idx"] for t in transactions)
        self.assertEqual(idxs, [0, 1])
        # Other-text on Bob is preserved; 10%-owner flag on Alice is preserved
        alice = next(t for t in transactions if t["reporter_cik"] == "0000111111")
        bob = next(t for t in transactions if t["reporter_cik"] == "0000222222")
        self.assertTrue(alice["is_ten_percent_owner"])
        self.assertEqual(bob["other_text"], "Co-trustee")


class ParseEdgeCaseTests(unittest.TestCase):
    def test_malformed_xml_returns_empty(self) -> None:
        insiders, transactions = parse_form4_xml(
            "<not really xml",
            accession_number="x",
            source_endpoint="x",
            ingest_run_id=1,
            fetched_at=NOW,
        )
        self.assertEqual(insiders, [])
        self.assertEqual(transactions, [])

    def test_entity_expansion_returns_empty(self) -> None:
        xml = """<?xml version="1.0"?>
<!DOCTYPE foo [
  <!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<ownershipDocument>
  <issuer><issuerCik>&xxe;</issuerCik></issuer>
</ownershipDocument>"""
        insiders, transactions = parse_form4_xml(
            xml,
            accession_number="x",
            source_endpoint="x",
            ingest_run_id=1,
            fetched_at=NOW,
        )
        self.assertEqual(insiders, [])
        self.assertEqual(transactions, [])

    def test_missing_issuer_cik_returns_empty(self) -> None:
        xml = """<?xml version="1.0"?>
<ownershipDocument>
  <documentType>4</documentType>
  <issuer><issuerName>No CIK</issuerName></issuer>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>0000111111</rptOwnerCik>
      <rptOwnerName>Anon</rptOwnerName>
    </reportingOwnerId>
  </reportingOwner>
</ownershipDocument>"""
        insiders, transactions = parse_form4_xml(
            xml,
            accession_number="x",
            source_endpoint="x",
            ingest_run_id=1,
            fetched_at=NOW,
        )
        self.assertEqual(insiders, [])
        self.assertEqual(transactions, [])

    def test_filing_with_no_transactions_still_emits_insider(self) -> None:
        # A Form 4 may report only holdings (no transaction) — schema still
        # records the insider so the entity row exists for downstream FK.
        xml = """<?xml version="1.0"?>
<ownershipDocument>
  <documentType>4</documentType>
  <issuer><issuerCik>0000320193</issuerCik><issuerName>Apple Inc.</issuerName></issuer>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>0000111111</rptOwnerCik>
      <rptOwnerName>Just A Holder</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerRelationship>
      <isOfficer>false</isOfficer>
      <isDirector>true</isDirector>
    </reportingOwnerRelationship>
  </reportingOwner>
</ownershipDocument>"""
        insiders, transactions = parse_form4_xml(
            xml,
            accession_number="x",
            source_endpoint="x",
            ingest_run_id=1,
            fetched_at=NOW,
        )
        self.assertEqual(len(insiders), 1)
        self.assertEqual(transactions, [])

    def test_transaction_missing_date_is_skipped(self) -> None:
        # transaction_date is NOT NULL in the schema; missing-date rows
        # are dropped silently rather than crashing the whole filing.
        xml = """<?xml version="1.0"?>
<ownershipDocument>
  <issuer><issuerCik>0000320193</issuerCik><issuerName>X</issuerName></issuer>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>0000111111</rptOwnerCik>
      <rptOwnerName>R</rptOwnerName>
    </reportingOwnerId>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <!-- no transactionDate -->
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""
        insiders, transactions = parse_form4_xml(
            xml,
            accession_number="x",
            source_endpoint="x",
            ingest_run_id=1,
            fetched_at=NOW,
        )
        self.assertEqual(len(insiders), 1)
        self.assertEqual(transactions, [])


class PayloadToXmlTests(unittest.TestCase):
    def test_unwraps_dict_envelope(self) -> None:
        self.assertEqual(_payload_to_xml({"xml": "<foo/>"}), "<foo/>")

    def test_accepts_bare_string(self) -> None:
        self.assertEqual(_payload_to_xml("<foo/>"), "<foo/>")

    def test_rejects_unknown_shape(self) -> None:
        self.assertIsNone(_payload_to_xml({"other": 1}))
        self.assertIsNone(_payload_to_xml(None))


class FetchUnnormalizedBlobsTests(unittest.TestCase):
    def test_default_selector_uses_normalized_filing_marker(self) -> None:
        captured: dict[str, object] = {}

        class FakeCursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params):
                captured["sql"] = sql
                captured["params"] = params

            def fetchall(self):
                return [
                    (
                        "form4_0000000000-26-000001",
                        "https://example.test/form4.xml",
                        {"xml": "<ownershipDocument/>"},
                        NOW,
                    )
                ]

        class FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return FakeCursor()

        with patch("genkei.normalize.sec_form4.db.connection", return_value=FakeConn()):
            rows = fetch_unnormalized_form4_blobs()

        sql_text = str(captured["sql"])
        self.assertIn("sec.form4_normalized_filings", sql_text)
        self.assertNotIn("sec.form4_transactions", sql_text)
        self.assertEqual(captured["params"], ["form4_%", 7])
        self.assertEqual(rows[0][0], "0000000000-26-000001")


class NormalizeTests(unittest.TestCase):
    def test_marks_holdings_only_filing_as_normalized(self) -> None:
        marked: dict[str, object] = {}
        upserts: dict[str, list[dict[str, object]]] = {}

        class Run:
            id = 99

            def __init__(self) -> None:
                self.rows = 0

            def add_rows(self, rows: int) -> None:
                self.rows += rows

        class FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        @contextmanager
        def fake_ingest_run(*args, **kwargs):
            yield Run()

        def fake_mark(conn, accessions, *, ingest_run_id):
            marked["accessions"] = accessions
            marked["ingest_run_id"] = ingest_run_id

        def fake_bulk_upsert(conn, table, rows, conflict_keys):
            upserts[table] = rows
            return 0

        with (
            patch(
                "genkei.normalize.sec_form4.fetch_unnormalized_form4_blobs",
                return_value=[("0000000000-26-000001", "url", "<xml/>", NOW)],
            ),
            patch(
                "genkei.normalize.sec_form4.parse_form4_xml",
                return_value=(
                    [
                        {
                            "reporter_cik": "0000111111",
                            "reporter_name": "Holder",
                            "source_endpoint": "url",
                            "last_seen_at": NOW,
                            "ingest_run_id": 99,
                        }
                    ],
                    [],
                ),
            ),
            patch("genkei.normalize.sec_form4.db.ingest_run", fake_ingest_run),
            patch("genkei.normalize.sec_form4.db.connection", return_value=FakeConn()),
            patch("genkei.normalize.sec_form4.db.bulk_upsert", fake_bulk_upsert),
            patch("genkei.normalize.sec_form4._mark_normalized_filings", fake_mark),
        ):
            self.assertEqual(normalize(), (99, 1))

        self.assertEqual(marked["accessions"], ["0000000000-26-000001"])
        self.assertEqual(marked["ingest_run_id"], 99)
        self.assertEqual(upserts["sec.insiders"][0]["last_seen_at"], NOW)

    def test_does_not_mark_unusable_parse_as_normalized(self) -> None:
        marked: dict[str, object] = {}

        class Run:
            id = 99

            def add_rows(self, rows: int) -> None:
                pass

        class FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        @contextmanager
        def fake_ingest_run(*args, **kwargs):
            yield Run()

        def fake_mark(conn, accessions, *, ingest_run_id):
            marked["accessions"] = accessions
            marked["ingest_run_id"] = ingest_run_id

        with (
            patch(
                "genkei.normalize.sec_form4.fetch_unnormalized_form4_blobs",
                return_value=[("0000000000-26-000001", "url", "<bad/>", NOW)],
            ),
            patch("genkei.normalize.sec_form4.parse_form4_xml", return_value=([], [])),
            patch("genkei.normalize.sec_form4.db.ingest_run", fake_ingest_run),
            patch("genkei.normalize.sec_form4.db.connection", return_value=FakeConn()),
            patch("genkei.normalize.sec_form4.db.bulk_upsert", return_value=0),
            patch("genkei.normalize.sec_form4._mark_normalized_filings", fake_mark),
        ):
            self.assertEqual(normalize(), (99, 1))

        self.assertEqual(marked["accessions"], [])
        self.assertEqual(marked["ingest_run_id"], 99)

    def test_marker_insert_upserts_accession_rows(self) -> None:
        captured: dict[str, object] = {}

        class FakeCursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def executemany(self, sql, params):
                captured["sql"] = sql
                captured["params"] = params

        class FakeConn:
            def cursor(self):
                return FakeCursor()

        sec_form4._mark_normalized_filings(
            FakeConn(), ["0000000000-26-000001"], ingest_run_id=99
        )

        self.assertIn("sec.form4_normalized_filings", str(captured["sql"]))
        self.assertEqual(captured["params"], [("0000000000-26-000001", 99)])


if __name__ == "__main__":
    unittest.main()
