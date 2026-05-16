"""Unit tests for the Form 4 collector helpers (B-079)."""

from __future__ import annotations

import unittest

from genkei.ingest.sec_form4 import build_form4_xml_url, strip_xsl_prefix


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


if __name__ == "__main__":
    unittest.main()
