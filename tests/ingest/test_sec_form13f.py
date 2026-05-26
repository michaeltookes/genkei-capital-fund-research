"""Unit tests for the SEC 13F collector helpers (B-080)."""

from __future__ import annotations

import unittest

from genkei.ingest.sec_form13f import (
    HOLDINGS_BEARING_FORM_TYPES,
    Form13FCandidate,
    _select_phase_b_candidates,
    build_filer_submissions_url,
    build_filing_file_url,
    build_filing_index_url,
    extract_form13f_candidates,
    select_info_table_filename,
)


class BuildUrlTests(unittest.TestCase):
    def test_submissions_url_uses_zero_padded_cik(self) -> None:
        # data.sec.gov requires the literal `CIK{10-digit}.json` shape.
        url = build_filer_submissions_url("0001067983")
        self.assertEqual(url, "https://data.sec.gov/submissions/CIK0001067983.json")

    def test_filing_index_url_strips_leading_zeros_and_dashes(self) -> None:
        # Archives base uses integer CIK + dash-stripped accession.
        url = build_filing_index_url("0001067983", "0001067983-25-000001")
        self.assertIn("/Archives/edgar/data/1067983/", url)
        self.assertIn("/000106798325000001/", url)
        self.assertTrue(url.endswith("/index.json"))

    def test_filing_file_url_appends_filename(self) -> None:
        url = build_filing_file_url(
            "0001067983", "0001067983-25-000001", "infotable.xml"
        )
        self.assertTrue(url.endswith("/infotable.xml"))
        self.assertIn("/Archives/edgar/data/1067983/000106798325000001/", url)


class SelectInfoTableFilenameTests(unittest.TestCase):
    def test_picks_first_info_xml_case_insensitive(self) -> None:
        # Real filings vary the casing and word splits.
        payload = {
            "directory": {
                "item": [
                    {"name": "primary_doc.xml", "size": "5000"},
                    {"name": "Form13F_InformationTable.xml", "size": "20000"},
                    {"name": "primary_doc.html", "size": "1000"},
                ]
            }
        }
        self.assertEqual(
            select_info_table_filename(payload), "Form13F_InformationTable.xml"
        )

    def test_falls_back_to_largest_xml_when_no_info_match(self) -> None:
        payload = {
            "directory": {
                "item": [
                    {"name": "primary_doc.xml", "size": "5000"},
                    {"name": "exhibit99.xml", "size": "50000"},
                ]
            }
        }
        # No "info" in any name; should pick the largest XML.
        self.assertEqual(select_info_table_filename(payload), "exhibit99.xml")

    def test_returns_none_when_no_xml_present(self) -> None:
        payload = {"directory": {"item": [{"name": "primary_doc.html", "size": "1"}]}}
        self.assertIsNone(select_info_table_filename(payload))

    def test_handles_malformed_payload_gracefully(self) -> None:
        self.assertIsNone(select_info_table_filename({}))
        self.assertIsNone(select_info_table_filename({"directory": "nope"}))
        self.assertIsNone(select_info_table_filename(None))


class ExtractCandidatesTests(unittest.TestCase):
    def test_pulls_13f_filings_from_recent_block(self) -> None:
        payload = {
            "filings": {
                "recent": {
                    "accessionNumber": ["A-1", "A-2", "A-3"],
                    "form": ["13F-HR", "13F-NT", "8-K"],
                    "primaryDocument": ["primary_doc.xml", "doc.xml", "ek.htm"],
                }
            }
        }
        cands = extract_form13f_candidates(payload, filer_cik="0001067983")
        # 8-K is filtered; both 13F variants remain.
        self.assertEqual([c.form_type for c in cands], ["13F-HR", "13F-NT"])
        self.assertEqual({c.accession_number for c in cands}, {"A-1", "A-2"})
        self.assertTrue(all(c.filer_cik == "0001067983" for c in cands))

    def test_pulls_from_history_page_root_shape(self) -> None:
        # History pages put the parallel arrays at the *root* of the
        # payload rather than under filings.recent.
        history = {
            "accessionNumber": ["H-1"],
            "form": ["13F-HR/A"],
            "primaryDocument": ["primary_doc.xml"],
        }
        cands = extract_form13f_candidates(history, filer_cik="0001067983")
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0].form_type, "13F-HR/A")
        self.assertEqual(cands[0].accession_number, "H-1")

    def test_skips_non_13f_forms(self) -> None:
        payload = {
            "filings": {
                "recent": {
                    "accessionNumber": ["A-1"],
                    "form": ["13-G"],  # close but not 13F
                    "primaryDocument": ["doc.xml"],
                }
            }
        }
        self.assertEqual(extract_form13f_candidates(payload, "0001067983"), [])


class SelectPhaseBCandidatesTests(unittest.TestCase):
    def _cand(
        self,
        accn: str,
        form: str,
        *,
        filed_at: str | None = None,
        accepted_at: str | None = None,
    ) -> Form13FCandidate:
        return Form13FCandidate(
            accession_number=accn,
            filer_cik="0001067983",
            form_type=form,
            primary_document="primary_doc.xml",
            filed_at=filed_at,
            accepted_at=accepted_at,
        )

    def test_filters_to_holdings_bearing_forms(self) -> None:
        # 13F-NT carries no holdings — phase B should skip it entirely.
        cands = [
            self._cand("A-HR", "13F-HR"),
            self._cand("A-NT", "13F-NT"),
            self._cand("A-NT-A", "13F-NT/A"),
            self._cand("A-CTR", "13F-CTR"),
        ]
        out = _select_phase_b_candidates(
            cands, already_normalized=set(), limit=None
        )
        self.assertEqual(
            sorted(c.accession_number for c in out), ["A-CTR", "A-HR"]
        )

    def test_skips_already_normalized_accessions(self) -> None:
        cands = [self._cand("A-HR", "13F-HR"), self._cand("B-HR", "13F-HR")]
        out = _select_phase_b_candidates(
            cands, already_normalized={"A-HR"}, limit=None
        )
        self.assertEqual([c.accession_number for c in out], ["B-HR"])

    def test_dedupes_repeated_accession(self) -> None:
        # Same filing appears in both `recent` and a history page — phase
        # B should only emit one fetch for it.
        cands = [self._cand("A-HR", "13F-HR"), self._cand("A-HR", "13F-HR")]
        out = _select_phase_b_candidates(
            cands, already_normalized=set(), limit=None
        )
        self.assertEqual([c.accession_number for c in out], ["A-HR"])

    def test_respects_limit_after_newest_first_sort(self) -> None:
        cands = [
            self._cand(
                "0009999999-26-000001",
                "13F-HR",
                accepted_at="2026-01-02T10:00:00.000Z",
            ),
            self._cand(
                "0000000001-26-000001",
                "13F-HR",
                accepted_at="2026-01-03T10:00:00.000Z",
            ),
            self._cand(
                "0001067983-26-000001",
                "13F-HR",
                accepted_at="2026-01-01T10:00:00.000Z",
            ),
        ]
        out = _select_phase_b_candidates(
            cands, already_normalized=set(), limit=2
        )
        self.assertEqual(
            [c.accession_number for c in out],
            ["0000000001-26-000001", "0009999999-26-000001"],
        )


class HoldingsBearingConstantsTests(unittest.TestCase):
    def test_amendments_are_holdings_bearing(self) -> None:
        # Amendments to HR are holdings-bearing; amendments to NT are not.
        self.assertIn("13F-HR/A", HOLDINGS_BEARING_FORM_TYPES)
        self.assertIn("13F-CTR/A", HOLDINGS_BEARING_FORM_TYPES)
        self.assertNotIn("13F-NT", HOLDINGS_BEARING_FORM_TYPES)
        self.assertNotIn("13F-NT/A", HOLDINGS_BEARING_FORM_TYPES)


if __name__ == "__main__":
    unittest.main()
