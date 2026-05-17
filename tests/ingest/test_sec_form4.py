"""Unit tests for the Form 4 collector helpers (B-079)."""

from __future__ import annotations

import unittest
from contextlib import redirect_stderr
from datetime import date
from io import StringIO
from unittest.mock import patch

from genkei.ingest.sec_form4 import (
    Form4Target,
    build_form4_xml_url,
    parse_args,
    select_uncached_form4s,
    strip_xsl_prefix,
)


class StripXslPrefixTests(unittest.TestCase):
    def test_strips_xslt_viewer_prefix(self) -> None:
        # The SEC submissions index returns the XSLT-styled HTML viewer
        # path here; the raw XML lives at the basename in the same folder.
        self.assertEqual(strip_xsl_prefix("xslF345X06/form4.xml"), "form4.xml")
        self.assertEqual(strip_xsl_prefix("xslF345X05/wf-form4_X.xml"), "wf-form4_X.xml")

    def test_passes_through_non_xsl_paths(self) -> None:
        self.assertEqual(strip_xsl_prefix("form4.xml"), "form4.xml")
        self.assertEqual(strip_xsl_prefix("subdir/form4.xml"), "subdir/form4.xml")

    def test_only_strips_leading_xsl(self) -> None:
        # "Other/xslX/form4.xml" — the leading dir isn't xsl-prefixed,
        # so we should NOT strip.
        self.assertEqual(strip_xsl_prefix("Other/xslX/form4.xml"), "Other/xslX/form4.xml")


class BuildFormUrlTests(unittest.TestCase):
    def test_uses_integer_cik_and_dash_stripped_accession(self) -> None:
        url = build_form4_xml_url(
            cik="0000320193",
            accession_number="0001140361-26-020871",
            primary_document="xslF345X06/form4.xml",
        )
        # Integer CIK (no leading zeros) is what /Archives/edgar/data/ expects.
        self.assertIn("/Archives/edgar/data/320193/", url)
        # Dash-stripped accession is the folder name.
        self.assertIn("/000114036126020871/", url)
        # Raw XML basename, not the XSLT viewer path.
        self.assertTrue(url.endswith("/form4.xml"))

    def test_preserves_basename_when_no_xsl_prefix(self) -> None:
        url = build_form4_xml_url(
            cik="0000320193",
            accession_number="0001140361-26-020871",
            primary_document="custom-doc.xml",
        )
        self.assertTrue(url.endswith("/custom-doc.xml"))


class SelectTargetsTests(unittest.TestCase):
    def test_selects_form4_and_amendments(self) -> None:
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
                        "0000000000-26-000001",
                        "0000320193",
                        "xslF345X06/form4.xml",
                        date(2026, 5, 16),
                    )
                ]

        class FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return FakeCursor()

        with patch("genkei.ingest.sec_form4.db.connection", return_value=FakeConn()):
            rows = select_uncached_form4s(limit=10)

        self.assertIn("f.form_type IN ('4', '4/A')", str(captured["sql"]))
        self.assertIn("sec.form4_normalized_filings", str(captured["sql"]))
        self.assertNotIn("meta.raw_blobs", str(captured["sql"]))
        self.assertEqual(captured["params"], [10])
        self.assertEqual(
            rows,
            [
                Form4Target(
                    accession_number="0000000000-26-000001",
                    cik="0000320193",
                    primary_document="xslF345X06/form4.xml",
                    filed_at="2026-05-16",
                )
            ],
        )


class ParseArgsTests(unittest.TestCase):
    def test_limit_must_be_positive(self) -> None:
        with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
            parse_args(["--limit", "-1"])


if __name__ == "__main__":
    unittest.main()
