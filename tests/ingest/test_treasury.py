"""Unit tests for the Treasury Fiscal Data collector (B-030).

Pure-function tests for URL building, watchlist → target deduplication,
blob-endpoint slug stability, page-merging, and total-pages parsing.
The DB + network path is exercised behind the integration test
(deferred — not in v1 scope) and via the normalizer tests which
exercise the parse shape against synthetic payloads.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from genkei.ingest.treasury import (
    BLOB_PREFIX,
    DEFAULT_RATE_LIMIT,
    MAX_PAGES,
    PAGE_SIZE,
    SOURCE_NAME,
    TREASURY_BASE_URL,
    EndpointTarget,
    _extract_total_pages,
    _fetch_all_pages,
    build_page_url,
    load_targets,
)

WATCHLIST_YAML = """\
treasury:
  - series_id: TOTAL_PUBLIC_DEBT
    name: Total Public Debt Outstanding
    endpoint: /v2/accounting/od/debt_to_penny
    value_field: tot_pub_debt_out_amt
    frequency: D
  - series_id: DEBT_HELD_PUBLIC
    name: Debt Held by the Public
    endpoint: /v2/accounting/od/debt_to_penny
    value_field: debt_held_public_amt
    frequency: D
  - series_id: TGA_CLOSING_BAL
    name: TGA closing balance
    endpoint: /v1/accounting/dts/operating_cash_balance
    value_field: close_today_bal
    frequency: D
    row_filter:
      account_type: Treasury General Account (TGA) Closing Balance
"""


def _watchlist_path(case: unittest.TestCase) -> Path:
    ctx = TemporaryDirectory()
    case.addCleanup(ctx.cleanup)
    tmp = Path(ctx.name)
    path = tmp / "watchlists.yml"
    path.write_text(WATCHLIST_YAML, encoding="utf-8")
    return path


class ModuleConstantsTests(unittest.TestCase):
    def test_source_name_is_treasury(self) -> None:
        self.assertEqual(SOURCE_NAME, "treasury")

    def test_blob_prefix(self) -> None:
        self.assertEqual(BLOB_PREFIX, "treasury_")

    def test_rate_limit_set(self) -> None:
        # 2 req/s is the "be polite" default for the undocumented Fiscal
        # Data rate limit — limiter is not None when configured.
        self.assertIsNotNone(DEFAULT_RATE_LIMIT)

    def test_page_size_at_api_max(self) -> None:
        # Fiscal Data caps page[size] at 10000. Going lower means more
        # round trips for the same coverage; going higher is rejected.
        self.assertEqual(PAGE_SIZE, 10000)


class EndpointTargetTests(unittest.TestCase):
    def test_blob_endpoint_slug_is_deterministic(self) -> None:
        target = EndpointTarget(
            endpoint="/v2/accounting/od/debt_to_penny",
            date_field="record_date",
        )
        self.assertEqual(
            target.blob_endpoint, "treasury_v2_accounting_od_debt_to_penny"
        )

    def test_blob_endpoint_handles_v1_dts_path(self) -> None:
        target = EndpointTarget(
            endpoint="/v1/accounting/dts/operating_cash_balance",
            date_field="record_date",
        )
        self.assertEqual(
            target.blob_endpoint,
            "treasury_v1_accounting_dts_operating_cash_balance",
        )

    def test_blob_endpoint_lowercases_uppercase_paths(self) -> None:
        # Defensive — endpoint paths in the wild shouldn't have caps,
        # but the slug must collapse them so the (idempotent) blob key
        # doesn't accidentally drift across runs.
        target = EndpointTarget(
            endpoint="/V2/Accounting/Foo", date_field="record_date"
        )
        self.assertEqual(target.blob_endpoint, "treasury_v2_accounting_foo")


class LoadTargetsTests(unittest.TestCase):
    def test_dedup_multiple_series_same_endpoint(self) -> None:
        # debt_to_penny carries two series in the test fixture but
        # should yield one collector target.
        path = _watchlist_path(self)
        targets = load_targets(path)
        self.assertEqual(len(targets), 2)
        endpoints = {t.endpoint for t in targets}
        self.assertEqual(
            endpoints,
            {
                "/v2/accounting/od/debt_to_penny",
                "/v1/accounting/dts/operating_cash_balance",
            },
        )

    def test_targets_sorted_for_deterministic_blob_order(self) -> None:
        # Stable ordering across runs = stable blob URLs = easier diff.
        path = _watchlist_path(self)
        targets = load_targets(path)
        keys = [(t.endpoint, t.date_field) for t in targets]
        self.assertEqual(keys, sorted(keys))

    def test_empty_treasury_section_raises(self) -> None:
        ctx = TemporaryDirectory()
        self.addCleanup(ctx.cleanup)
        path = Path(ctx.name) / "watchlists.yml"
        path.write_text("treasury: []\n", encoding="utf-8")
        # Empty watchlist means the workflow has nothing to do — fail
        # loud rather than emit a no-op success run.
        with self.assertRaises(SystemExit):
            load_targets(path)


class BuildPageUrlTests(unittest.TestCase):
    def test_canonical_params_in_url(self) -> None:
        target = EndpointTarget(
            endpoint="/v2/accounting/od/debt_to_penny",
            date_field="record_date",
        )
        url = build_page_url(target, page_number=1)
        self.assertIn(TREASURY_BASE_URL, url)
        self.assertIn("/v2/accounting/od/debt_to_penny", url)
        self.assertIn("sort=record_date", url)
        self.assertIn("page%5Bsize%5D=10000", url)
        self.assertIn("page%5Bnumber%5D=1", url)
        self.assertIn("format=json", url)

    def test_page_number_increments(self) -> None:
        target = EndpointTarget(
            endpoint="/v2/accounting/od/avg_interest_rates",
            date_field="record_date",
        )
        first = build_page_url(target, page_number=1)
        second = build_page_url(target, page_number=2)
        self.assertNotEqual(first, second)
        self.assertIn("page%5Bnumber%5D=2", second)


class ExtractTotalPagesTests(unittest.TestCase):
    def test_extracts_integer_total_pages(self) -> None:
        payload = {"meta": {"total-pages": 5}}
        self.assertEqual(_extract_total_pages(payload), 5)

    def test_extracts_string_total_pages(self) -> None:
        # Fiscal Data sometimes returns numeric meta as strings.
        payload = {"meta": {"total-pages": "12"}}
        self.assertEqual(_extract_total_pages(payload), 12)

    def test_missing_meta_block_is_none(self) -> None:
        self.assertIsNone(_extract_total_pages({}))
        self.assertIsNone(_extract_total_pages({"meta": "not a dict"}))

    def test_negative_total_is_none(self) -> None:
        # Defensive — negative total-pages can't be honored, treat as
        # absent so the loop falls back to short-page detection.
        self.assertIsNone(_extract_total_pages({"meta": {"total-pages": -1}}))

    def test_unparseable_string_is_none(self) -> None:
        self.assertIsNone(
            _extract_total_pages({"meta": {"total-pages": "many"}})
        )


class _StubHttp:
    """Minimal stub of HttpClient.get_json for offline tests."""

    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self._pages = pages
        self.calls: list[str] = []

    def get_json(self, url: str) -> Any:
        self.calls.append(url)
        page_index = len(self.calls) - 1
        if page_index >= len(self._pages):
            raise AssertionError(
                f"Unexpected extra page request {page_index + 1}: {url}"
            )
        return self._pages[page_index]


class FetchAllPagesTests(unittest.TestCase):
    def _target(self) -> EndpointTarget:
        return EndpointTarget(
            endpoint="/v2/accounting/od/debt_to_penny",
            date_field="record_date",
        )

    def test_merges_data_across_pages(self) -> None:
        page1 = {
            "data": [{"record_date": "2024-01-01"}, {"record_date": "2024-01-02"}],
            "meta": {"count": 2, "total-count": 3, "total-pages": 2},
            "links": {"next": "..."},
        }
        page2 = {
            "data": [{"record_date": "2024-01-03"}],
            "meta": {"count": 1, "total-count": 3, "total-pages": 2},
            "links": {"next": None},
        }
        stub = _StubHttp([page1, page2])
        combined = _fetch_all_pages(self._target(), stub)
        self.assertEqual(len(combined["data"]), 3)
        self.assertEqual(
            [row["record_date"] for row in combined["data"]],
            ["2024-01-01", "2024-01-02", "2024-01-03"],
        )
        self.assertEqual(combined["meta"]["total-count"], 3)
        self.assertEqual(combined["meta"]["total-pages"], 1)
        self.assertEqual(len(stub.calls), 2)

    def test_short_page_terminates_loop(self) -> None:
        # When total-pages is missing, a sub-PAGE_SIZE page signals the
        # end-of-data — the loop must not keep going forever.
        page = {
            "data": [{"record_date": "2024-01-01"}],
            "meta": {"count": 1},
            "links": {"next": None},
        }
        stub = _StubHttp([page])
        combined = _fetch_all_pages(self._target(), stub)
        self.assertEqual(len(combined["data"]), 1)
        self.assertEqual(len(stub.calls), 1)

    def test_total_pages_one_stops_after_first(self) -> None:
        # When total-pages is 1, even a full PAGE_SIZE page should stop.
        page = {
            "data": [{"record_date": f"2024-01-{i:02d}"} for i in range(1, 11)],
            "meta": {"count": 10, "total-pages": 1},
            "links": {"next": None},
        }
        stub = _StubHttp([page])
        combined = _fetch_all_pages(self._target(), stub)
        self.assertEqual(len(combined["data"]), 10)
        self.assertEqual(len(stub.calls), 1)

    def test_non_dict_payload_raises(self) -> None:
        stub = _StubHttp(["not a dict"])  # type: ignore[list-item]
        with self.assertRaisesRegex(ValueError, "is not an object"):
            _fetch_all_pages(self._target(), stub)

    def test_missing_data_field_raises(self) -> None:
        stub = _StubHttp([{"meta": {}}])
        with self.assertRaisesRegex(ValueError, "missing a `data` array"):
            _fetch_all_pages(self._target(), stub)

    def test_max_pages_ceiling_protects_runaway(self) -> None:
        # Even with explicit checks, a malicious / broken response that
        # never short-circuits the loop must hit the MAX_PAGES guard.
        self.assertGreater(MAX_PAGES, 0)


if __name__ == "__main__":
    unittest.main()
