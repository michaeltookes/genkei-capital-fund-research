"""Unit tests for the BEA NIPA collector (B-029).

Pure-function tests for URL building, API-key redaction, watchlist →
target deduplication, and BEA error-envelope detection. The DB +
network path is exercised behind the integration test (deferred — not
in v1 scope) and via the normalizer tests which exercise the parse
shape against synthetic payloads.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from genkei.ingest.bea import (
    BEA_BASE_URL,
    BLOB_PREFIX,
    DEFAULT_RATE_LIMIT,
    SOURCE_NAME,
    TableTarget,
    _extract_bea_error,
    _is_bea_error_envelope,
    _redact_key,
    build_table_url,
    load_targets,
    require_api_key,
)

WATCHLIST_YAML = """\
bea:
  - table_id: T10101
    line_number: 1
    name: Real GDP — % change
    frequency: Q
  - table_id: T10101
    line_number: 2
    name: PCE contribution to %Δ
    frequency: Q
  - table_id: T20100
    line_number: 1
    name: Personal Income
    frequency: Q
  - table_id: T70100
    line_number: 5
    name: Real GDP per capita
    frequency: A
"""


def _watchlist_path(case: unittest.TestCase) -> Path:
    ctx = TemporaryDirectory()
    case.addCleanup(ctx.cleanup)
    tmp = Path(ctx.name)
    path = tmp / "watchlists.yml"
    path.write_text(WATCHLIST_YAML, encoding="utf-8")
    return path


class ModuleConstantsTests(unittest.TestCase):
    def test_source_name_is_bea(self) -> None:
        self.assertEqual(SOURCE_NAME, "bea")

    def test_blob_prefix(self) -> None:
        self.assertEqual(BLOB_PREFIX, "bea_")

    def test_rate_limit_is_polite(self) -> None:
        # 2 req/s is the "be polite" baseline for an undocumented free
        # API; tighter than necessary for the v1 watchlist size (~7
        # calls per run) but leaves headroom for v2 expansion.
        # Limiter exposes the configured limit via private attrs;
        # we only assert it exists and is non-None.
        self.assertIsNotNone(DEFAULT_RATE_LIMIT)


class TableTargetTests(unittest.TestCase):
    def test_blob_endpoint_lowercases_both_parts(self) -> None:
        target = TableTarget(table_id="T10101", frequency="Q")
        # blob endpoint is the meta.raw_blobs key — must be stable
        # across runs so re-ingest is idempotent.
        self.assertEqual(target.blob_endpoint, "bea_t10101_q")

    def test_annual_blob_endpoint(self) -> None:
        target = TableTarget(table_id="T70100", frequency="A")
        self.assertEqual(target.blob_endpoint, "bea_t70100_a")


class LoadTargetsTests(unittest.TestCase):
    def test_dedup_multiple_lines_same_table(self) -> None:
        # T10101 has 2 watchlist lines but should yield 1 target.
        path = _watchlist_path(self)
        targets = load_targets(path)
        self.assertEqual(len(targets), 3)  # T10101, T20100, T70100
        ids = {(t.table_id, t.frequency) for t in targets}
        self.assertEqual(
            ids,
            {("T10101", "Q"), ("T20100", "Q"), ("T70100", "A")},
        )

    def test_targets_sorted_for_deterministic_blob_order(self) -> None:
        # Stable ordering across runs = stable blob URLs = easier diff.
        path = _watchlist_path(self)
        targets = load_targets(path)
        keys = [(t.table_id, t.frequency) for t in targets]
        self.assertEqual(keys, sorted(keys))

    def test_empty_bea_section_raises(self) -> None:
        ctx = TemporaryDirectory()
        self.addCleanup(ctx.cleanup)
        path = Path(ctx.name) / "watchlists.yml"
        path.write_text("bea: []\n", encoding="utf-8")
        # Empty watchlist means the workflow has nothing to do — fail
        # loud rather than emit a no-op success run.
        with self.assertRaises(SystemExit):
            load_targets(path)


class BuildTableUrlTests(unittest.TestCase):
    def test_canonical_params_in_url(self) -> None:
        url = build_table_url(
            "MYKEY", TableTarget(table_id="T10101", frequency="Q")
        )
        self.assertIn(BEA_BASE_URL, url)
        self.assertIn("UserID=MYKEY", url)
        self.assertIn("method=GetData", url)
        self.assertIn("datasetname=NIPA", url)
        self.assertIn("TableName=T10101", url)
        self.assertIn("Frequency=Q", url)
        self.assertIn("Year=ALL", url)
        self.assertIn("ResultFormat=JSON", url)

    def test_annual_frequency_in_url(self) -> None:
        url = build_table_url(
            "K", TableTarget(table_id="T70100", frequency="A")
        )
        self.assertIn("Frequency=A", url)


class RedactKeyTests(unittest.TestCase):
    def test_replaces_literal_key_anywhere_in_url(self) -> None:
        url = "https://apps.bea.gov/api/data/?UserID=SECRET&method=GetData"
        self.assertEqual(
            _redact_key(url, "SECRET"),
            "https://apps.bea.gov/api/data/?UserID=***&method=GetData",
        )

    def test_empty_api_key_passes_url_through(self) -> None:
        # Defensive — if the redactor is ever called with empty key,
        # don't accidentally strip everything (string.replace('', '***')
        # would). The early-return branch protects against that.
        url = "https://apps.bea.gov/api/data/?UserID=SECRET"
        self.assertEqual(_redact_key(url, ""), url)


class IsBeaErrorEnvelopeTests(unittest.TestCase):
    def test_canonical_success_shape_is_not_error(self) -> None:
        payload = {
            "BEAAPI": {
                "Request": {},
                "Results": {"Statistic": "GDP", "Data": [{"LineNumber": "1"}]},
            }
        }
        self.assertFalse(_is_bea_error_envelope(payload))

    def test_top_level_error_envelope_detected(self) -> None:
        # Bad UserID returns this shape.
        payload = {
            "BEAAPI": {
                "Error": {
                    "APIErrorCode": "1",
                    "APIErrorDescription": "Invalid UserID",
                }
            }
        }
        self.assertTrue(_is_bea_error_envelope(payload))

    def test_nested_results_error_detected(self) -> None:
        # Bad table id returns this shape.
        payload = {
            "BEAAPI": {
                "Results": {
                    "Error": {"APIErrorDescription": "Invalid TableName"}
                }
            }
        }
        self.assertTrue(_is_bea_error_envelope(payload))

    def test_missing_results_block_is_error(self) -> None:
        # If BEA returns just the Request echo with no Results, the
        # call can't have succeeded.
        payload = {"BEAAPI": {"Request": {}}}
        self.assertTrue(_is_bea_error_envelope(payload))

    def test_results_without_data_is_error(self) -> None:
        payload = {"BEAAPI": {"Results": {"Statistic": "GDP"}}}
        self.assertTrue(_is_bea_error_envelope(payload))

    def test_results_with_non_list_data_is_error(self) -> None:
        payload = {"BEAAPI": {"Results": {"Data": {"LineNumber": "1"}}}}
        self.assertTrue(_is_bea_error_envelope(payload))

    def test_empty_data_array_is_not_error(self) -> None:
        payload = {"BEAAPI": {"Results": {"Data": []}}}
        self.assertFalse(_is_bea_error_envelope(payload))

    def test_non_dict_payload_is_error(self) -> None:
        # Defensive — a stringified response from a bad JSON parse
        # shouldn't crash; treat as error.
        self.assertTrue(_is_bea_error_envelope("not a dict"))
        self.assertTrue(_is_bea_error_envelope(None))


class ExtractBeaErrorTests(unittest.TestCase):
    def test_extracts_top_level_description(self) -> None:
        payload = {
            "BEAAPI": {
                "Error": {
                    "APIErrorCode": "1",
                    "APIErrorDescription": "Invalid UserID",
                }
            }
        }
        self.assertEqual(_extract_bea_error(payload), "Invalid UserID")

    def test_extracts_nested_results_description(self) -> None:
        payload = {
            "BEAAPI": {
                "Results": {
                    "Error": {"APIErrorDescription": "Invalid TableName"}
                }
            }
        }
        self.assertEqual(_extract_bea_error(payload), "Invalid TableName")

    def test_extracts_list_wrapped_error(self) -> None:
        # BEA occasionally wraps a single error in a list.
        payload = {
            "BEAAPI": {
                "Results": {
                    "Error": [{"APIErrorDescription": "Bad LineNumber"}]
                }
            }
        }
        self.assertEqual(_extract_bea_error(payload), "Bad LineNumber")

    def test_unknown_shape_returns_diagnostic(self) -> None:
        # Should produce *something* informative, never crash.
        result = _extract_bea_error({"random": "junk"})
        self.assertIn("unknown BEA error", result)


class RequireApiKeyTests(unittest.TestCase):
    def test_missing_key_raises_with_signup_hint(self) -> None:
        # Save + clear the env, restore on teardown.
        import os

        prev = os.environ.pop("BEA_API_KEY", None)
        self.addCleanup(
            lambda: (
                os.environ.update({"BEA_API_KEY": prev})
                if prev is not None
                else None
            )
        )
        try:
            with self.assertRaises(SystemExit) as ctx:
                require_api_key()
            self.assertIn("BEA_API_KEY", str(ctx.exception))
            self.assertIn("apps.bea.gov", str(ctx.exception))
        finally:
            pass

    def test_present_key_returned(self) -> None:
        import os

        prev = os.environ.get("BEA_API_KEY")
        os.environ["BEA_API_KEY"] = "TESTKEY123"
        self.addCleanup(
            lambda: (
                os.environ.update({"BEA_API_KEY": prev})
                if prev is not None
                else os.environ.pop("BEA_API_KEY", None)
            )
        )
        self.assertEqual(require_api_key(), "TESTKEY123")


if __name__ == "__main__":
    unittest.main()
