"""Unit tests for the EIA Open Data v2 collector (B-032).

Pure-function tests for URL building, watchlist → target projection,
blob-endpoint slug stability, API key redaction, page merging, and
total parsing. The DB + network path is exercised behind the
integration test (deferred — not in v1 scope) and via the normalizer
tests which exercise the parse shape against synthetic payloads.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from genkei.ingest.eia import (
    BLOB_PREFIX,
    DEFAULT_RATE_LIMIT,
    EIA_BASE_URL,
    MAX_PAGES,
    PAGE_SIZE,
    SOURCE_NAME,
    SeriesTarget,
    _eia_frequency,
    _extract_total,
    _fetch_all_pages,
    _redact_key,
    build_page_url,
    load_targets,
)

WATCHLIST_YAML = """\
eia:
  - series_id: WTI_SPOT
    name: Cushing OK WTI spot price
    route: petroleum/pri/spt
    frequency: D
    facets:
      series: RWTC
  - series_id: BRENT_SPOT
    name: Europe Brent spot price
    route: petroleum/pri/spt
    frequency: D
    facets:
      series: RBRTE
  - series_id: ELEC_NET_GEN_US
    name: US net electricity generation
    route: electricity/electric-power-operational-data
    frequency: M
    data_field: generation
    facets:
      fueltype: ALL
      location: US
      sectorid: '99'
"""


def _watchlist_path(case: unittest.TestCase) -> Path:
    ctx = TemporaryDirectory()
    case.addCleanup(ctx.cleanup)
    tmp = Path(ctx.name)
    path = tmp / "watchlists.yml"
    path.write_text(WATCHLIST_YAML, encoding="utf-8")
    return path


class ModuleConstantsTests(unittest.TestCase):
    def test_source_name_is_eia(self) -> None:
        self.assertEqual(SOURCE_NAME, "eia")

    def test_blob_prefix(self) -> None:
        self.assertEqual(BLOB_PREFIX, "eia_")

    def test_rate_limit_set(self) -> None:
        # EIA's anonymous tier allows 5,000 req/hour; 2 req/s stays well
        # under the cap and matches our other free-API politeness default.
        self.assertIsNotNone(DEFAULT_RATE_LIMIT)

    def test_page_size_at_api_max(self) -> None:
        # EIA v2 hard-caps response length at 5000 rows per request.
        self.assertEqual(PAGE_SIZE, 5000)

    def test_max_pages_positive(self) -> None:
        self.assertGreater(MAX_PAGES, 0)


class EiaFrequencyTests(unittest.TestCase):
    def test_maps_every_short_code(self) -> None:
        cases = {
            "D": "daily",
            "W": "weekly",
            "M": "monthly",
            "Q": "quarterly",
            "A": "annual",
        }
        for short, verbose in cases.items():
            with self.subTest(short=short):
                self.assertEqual(_eia_frequency(short), verbose)

    def test_accepts_lowercase(self) -> None:
        self.assertEqual(_eia_frequency("d"), "daily")


class SeriesTargetTests(unittest.TestCase):
    def test_blob_endpoint_slug_is_lowercase_series_id(self) -> None:
        target = SeriesTarget(
            series_id="WTI_SPOT",
            route="petroleum/pri/spt",
            frequency="D",
            data_field="value",
            facets={"series": "RWTC"},
        )
        self.assertEqual(target.blob_endpoint, "eia_wti_spot")

    def test_blob_endpoint_distinct_for_each_series(self) -> None:
        # Two series sharing one route must produce distinct blob slugs.
        a = SeriesTarget(
            series_id="CRUDE_INV_EXSPR",
            route="petroleum/stoc/wstk",
            frequency="W",
            data_field="value",
            facets={"series": "WCESTUS1"},
        )
        b = SeriesTarget(
            series_id="GASOLINE_INV",
            route="petroleum/stoc/wstk",
            frequency="W",
            data_field="value",
            facets={"series": "WGTSTUS1"},
        )
        self.assertNotEqual(a.blob_endpoint, b.blob_endpoint)


class LoadTargetsTests(unittest.TestCase):
    def test_projects_each_watchlist_entry_into_target(self) -> None:
        path = _watchlist_path(self)
        targets = load_targets(path)
        self.assertEqual(len(targets), 3)
        ids = {t.series_id for t in targets}
        self.assertEqual(ids, {"WTI_SPOT", "BRENT_SPOT", "ELEC_NET_GEN_US"})

    def test_targets_sorted_for_deterministic_blob_order(self) -> None:
        path = _watchlist_path(self)
        targets = load_targets(path)
        series_order = [t.series_id for t in targets]
        self.assertEqual(series_order, sorted(series_order))

    def test_electricity_target_preserves_three_facets(self) -> None:
        path = _watchlist_path(self)
        targets = load_targets(path)
        elec = next(t for t in targets if t.series_id == "ELEC_NET_GEN_US")
        self.assertEqual(
            dict(elec.facets),
            {"fueltype": "ALL", "location": "US", "sectorid": "99"},
        )
        self.assertEqual(elec.data_field, "generation")

    def test_empty_eia_section_raises(self) -> None:
        ctx = TemporaryDirectory()
        self.addCleanup(ctx.cleanup)
        path = Path(ctx.name) / "watchlists.yml"
        path.write_text("eia: []\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            load_targets(path)


class BuildPageUrlTests(unittest.TestCase):
    def _target(self, **overrides: Any) -> SeriesTarget:
        base = {
            "series_id": "WTI_SPOT",
            "route": "petroleum/pri/spt",
            "frequency": "D",
            "data_field": "value",
            "facets": {"series": "RWTC"},
        }
        base.update(overrides)
        return SeriesTarget(**base)  # type: ignore[arg-type]

    def test_canonical_params_in_url(self) -> None:
        url = build_page_url(self._target(), api_key="KEY", start="2016-06-11")
        self.assertIn(f"{EIA_BASE_URL}/petroleum/pri/spt/data/", url)
        self.assertIn("frequency=daily", url)
        self.assertIn("start=2016-06-11", url)
        self.assertIn("api_key=KEY", url)
        # EIA's data + sort + facet params are URL-encoded.
        self.assertIn("data%5B0%5D=value", url)
        self.assertIn("sort%5B0%5D%5Bcolumn%5D=period", url)
        self.assertIn("sort%5B0%5D%5Bdirection%5D=asc", url)
        self.assertIn("offset=0", url)
        self.assertIn("length=5000", url)
        self.assertIn("facets%5Bseries%5D%5B%5D=RWTC", url)

    def test_offset_changes_per_page(self) -> None:
        url0 = build_page_url(self._target(), api_key="KEY", start="2016-06-11", offset=0)
        url1 = build_page_url(self._target(), api_key="KEY", start="2016-06-11", offset=5000)
        self.assertIn("offset=0", url0)
        self.assertIn("offset=5000", url1)

    def test_multi_facet_route_emits_each_facet(self) -> None:
        target = self._target(
            series_id="ELEC_NET_GEN_US",
            route="electricity/electric-power-operational-data",
            frequency="M",
            data_field="generation",
            facets={"fueltype": "ALL", "location": "US", "sectorid": "99"},
        )
        url = build_page_url(target, api_key="KEY", start="2016-06-11")
        self.assertIn(
            f"{EIA_BASE_URL}/electricity/electric-power-operational-data/data/",
            url,
        )
        self.assertIn("facets%5Bfueltype%5D%5B%5D=ALL", url)
        self.assertIn("facets%5Blocation%5D%5B%5D=US", url)
        self.assertIn("facets%5Bsectorid%5D%5B%5D=99", url)
        self.assertIn("data%5B0%5D=generation", url)

    def test_facet_order_is_stable_across_calls(self) -> None:
        # Two consecutive calls must produce identical URLs so blob
        # tracking via raw_blobs.url stays meaningful.
        target = self._target(
            facets={"series": "WCESTUS1", "padd": "0"},
        )
        first = build_page_url(target, api_key="KEY", start="2016-06-11")
        second = build_page_url(target, api_key="KEY", start="2016-06-11")
        self.assertEqual(first, second)


class RedactKeyTests(unittest.TestCase):
    def test_replaces_api_key_anywhere_in_string(self) -> None:
        url = "https://api.eia.gov/v2/foo?api_key=SECRET_KEY&data=value"
        self.assertEqual(
            _redact_key(url, "SECRET_KEY"),
            "https://api.eia.gov/v2/foo?api_key=***&data=value",
        )

    def test_empty_api_key_is_passthrough(self) -> None:
        # Edge case — collector callers can pass "" defensively. No-op
        # rather than corrupting the URL with stray substitutions.
        self.assertEqual(_redact_key("https://x?api_key=", ""), "https://x?api_key=")

    def test_redaction_handles_repeated_key_occurrences(self) -> None:
        # Defensive — error messages may quote the URL twice.
        msg = "fetch failed: SECRET retry url ?api_key=SECRET"
        self.assertEqual(
            _redact_key(msg, "SECRET"),
            "fetch failed: *** retry url ?api_key=***",
        )


class ExtractTotalTests(unittest.TestCase):
    def test_extracts_integer_total(self) -> None:
        self.assertEqual(_extract_total({"total": 12}), 12)

    def test_extracts_string_total(self) -> None:
        # EIA frequently returns numeric counts as strings.
        self.assertEqual(_extract_total({"total": "12"}), 12)

    def test_missing_total_is_none(self) -> None:
        self.assertIsNone(_extract_total({}))

    def test_negative_total_is_none(self) -> None:
        self.assertIsNone(_extract_total({"total": -3}))

    def test_unparseable_string_is_none(self) -> None:
        self.assertIsNone(_extract_total({"total": "many"}))


class _StubHttp:
    """Minimal stub of HttpClient.get_json for offline tests."""

    def __init__(self, pages: list[Any]) -> None:
        self._pages = pages
        self.calls: list[str] = []

    def get_json(self, url: str) -> Any:
        self.calls.append(url)
        idx = len(self.calls) - 1
        if idx >= len(self._pages):
            raise AssertionError(
                f"Unexpected extra page request {idx + 1}: {url}"
            )
        return self._pages[idx]


class FetchAllPagesTests(unittest.TestCase):
    def _target(self, **overrides: Any) -> SeriesTarget:
        base = {
            "series_id": "WTI_SPOT",
            "route": "petroleum/pri/spt",
            "frequency": "D",
            "data_field": "value",
            "facets": {"series": "RWTC"},
        }
        base.update(overrides)
        return SeriesTarget(**base)  # type: ignore[arg-type]

    def test_merges_data_across_pages_using_total(self) -> None:
        page1 = {
            "request": {"route": "petroleum/pri/spt"},
            "apiVersion": "2.1.8",
            "response": {
                "total": "3",
                "data": [
                    {"period": "2024-01-01", "value": 1.0},
                    {"period": "2024-01-02", "value": 2.0},
                ],
                "warnings": [],
            },
        }
        page2 = {
            "request": {"route": "petroleum/pri/spt"},
            "apiVersion": "2.1.8",
            "response": {
                "total": "3",
                "data": [{"period": "2024-01-03", "value": 3.0}],
                "warnings": [],
            },
        }
        stub = _StubHttp([page1, page2])
        # PAGE_SIZE is 5000; both pages are well under that but `total=3`
        # is the loop bound so we stop after page 2.
        combined = _fetch_all_pages(
            self._target(), api_key="KEY", start="2024-01-01", http=stub  # type: ignore[arg-type]
        )
        self.assertEqual(len(combined["response"]["data"]), 3)
        self.assertEqual(combined["response"]["total"], 3)
        self.assertEqual(
            [row["period"] for row in combined["response"]["data"]],
            ["2024-01-01", "2024-01-02", "2024-01-03"],
        )
        self.assertEqual(len(stub.calls), 2)

    def test_short_page_terminates_loop_when_total_missing(self) -> None:
        page = {
            "request": {},
            "response": {
                "data": [{"period": "2024-01-01", "value": 1.0}],
            },
        }
        stub = _StubHttp([page])
        combined = _fetch_all_pages(
            self._target(), api_key="KEY", start="2024-01-01", http=stub  # type: ignore[arg-type]
        )
        self.assertEqual(len(combined["response"]["data"]), 1)
        self.assertEqual(len(stub.calls), 1)

    def test_offset_increments_between_requests(self) -> None:
        page1 = {
            "request": {},
            "response": {
                "total": "2",
                "data": [{"period": "2024-01-01", "value": 1.0}],
            },
        }
        page2 = {
            "request": {},
            "response": {
                "total": "2",
                "data": [{"period": "2024-01-02", "value": 2.0}],
            },
        }
        stub = _StubHttp([page1, page2])
        _fetch_all_pages(
            self._target(), api_key="KEY", start="2024-01-01", http=stub  # type: ignore[arg-type]
        )
        self.assertEqual(len(stub.calls), 2)
        self.assertIn("offset=0", stub.calls[0])
        self.assertIn("offset=5000", stub.calls[1])

    def test_non_dict_payload_raises(self) -> None:
        stub = _StubHttp(["not a dict"])
        with self.assertRaisesRegex(ValueError, "is not an object"):
            _fetch_all_pages(
                self._target(),
                api_key="KEY",
                start="2024-01-01",
                http=stub,  # type: ignore[arg-type]
            )

    def test_missing_response_block_raises(self) -> None:
        stub = _StubHttp([{"apiVersion": "2.1.8"}])
        with self.assertRaisesRegex(ValueError, "missing a `response` object"):
            _fetch_all_pages(
                self._target(),
                api_key="KEY",
                start="2024-01-01",
                http=stub,  # type: ignore[arg-type]
            )

    def test_missing_data_field_raises(self) -> None:
        stub = _StubHttp([{"response": {"total": "0"}}])
        with self.assertRaisesRegex(ValueError, "missing a `response.data` array"):
            _fetch_all_pages(
                self._target(),
                api_key="KEY",
                start="2024-01-01",
                http=stub,  # type: ignore[arg-type]
            )


if __name__ == "__main__":
    unittest.main()
