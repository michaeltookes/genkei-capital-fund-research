"""Unit tests for the FRED collector helpers (offline)."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from genkei.ingest import fred
from genkei.ingest.fred import (
    DEFAULT_RATE_LIMIT,
    SeriesTarget,
    _fetch_observations_payload,
    _fetch_series_pair,
    _redact_key,
    build_observations_url,
    build_series_url,
    load_series,
    require_api_key,
)


class LoadSeriesTests(unittest.TestCase):
    def test_reads_macro_series_from_yaml(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(
                "macro_series:\n"
                "  - id: DGS10\n"
                "    name: 10-Year Treasury Yield\n"
                "    rationale: Risk-free benchmark.\n"
                "  - id: CPIAUCSL\n"
                "    name: Consumer Price Index (All Urban Consumers)\n",
                encoding="utf-8",
            )
            series = load_series(path)
        self.assertEqual(len(series), 2)
        self.assertEqual(
            series[0], SeriesTarget("DGS10", "10-Year Treasury Yield", "Risk-free benchmark.")
        )
        self.assertEqual(series[1].rationale, None)

    def test_rejects_missing_id(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text("macro_series:\n  - name: bad\n", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "missing a string `id`"):
                load_series(path)

    def test_rejects_empty_macro_series(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text("macro_series: []\n", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "macro_series"):
                load_series(path)

    def test_rejects_duplicate_macro_series_ids(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(
                "macro_series:\n"
                "  - id: DGS10\n"
                "    name: 10-Year Treasury Yield\n"
                "  - id: DGS10\n"
                "    name: Duplicate 10-Year Treasury Yield\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SystemExit, "Duplicate macro_series id: DGS10"):
                load_series(path)

    def test_rejects_missing_file(self) -> None:
        with self.assertRaisesRegex(SystemExit, "Watchlist file not found"):
            load_series(Path("/no/such/path.yml"))

    def test_rejects_invalid_yaml(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text("[: not yaml\n", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "Invalid YAML"):
                load_series(path)


class UrlBuilderTests(unittest.TestCase):
    def test_series_url_contains_required_params(self) -> None:
        url = build_series_url("KEY123", "DGS10")
        self.assertIn("series_id=DGS10", url)
        self.assertIn("api_key=KEY123", url)
        self.assertIn("file_type=json", url)

    def test_observations_url_omits_realtime_window(self) -> None:
        # G-027: FRED returns 400 if realtime_start predates the first
        # observation by too much (the 2000-vintage cap). Without
        # realtime params, FRED still tags each observation with its
        # own realtime_start in the payload, so vintage tracking works.
        url = build_observations_url("KEY123", "GDPC1")
        self.assertIn("series_id=GDPC1", url)
        self.assertNotIn("realtime_start", url)
        self.assertNotIn("realtime_end", url)
        self.assertIn("limit=100000", url)
        self.assertIn("offset=0", url)

    def test_redact_key_strips_api_key_from_url(self) -> None:
        url = build_observations_url("SECRET", "DGS10")
        redacted = _redact_key(url, "SECRET")
        self.assertNotIn("SECRET", redacted)
        self.assertIn("api_key=***", redacted)

    def test_redact_key_handles_missing_key(self) -> None:
        self.assertEqual(_redact_key("http://x", ""), "http://x")

    def test_fetch_series_pair_does_not_swallow_unexpected_errors(self) -> None:
        class BuggyHttp:
            def get_json(self, _url: str) -> object:
                raise RuntimeError("programming bug")

        with self.assertRaisesRegex(RuntimeError, "programming bug"):
            _fetch_series_pair(
                SeriesTarget("DGS10", "10-Year Treasury Yield"),
                "KEY123",
                BuggyHttp(),  # type: ignore[arg-type]
                1,
                [],
            )

    def test_fetch_observations_payload_paginates_until_count_is_complete(self) -> None:
        calls: list[str] = []

        class PagingHttp:
            def get_json(self, url: str) -> object:
                calls.append(url)
                if "offset=0" in url:
                    return {
                        "count": 3,
                        "observations": [
                            {"date": "2024-01-01"},
                            {"date": "2024-01-02"},
                        ],
                    }
                if "offset=2" in url:
                    return {"count": 3, "observations": [{"date": "2024-01-03"}]}
                raise AssertionError(f"unexpected url: {url}")

        with patch.object(fred, "OBSERVATIONS_PAGE_LIMIT", 2):
            url, payload = _fetch_observations_payload(
                SeriesTarget("DGS10", "10-Year Treasury Yield"),
                "KEY123",
                PagingHttp(),  # type: ignore[arg-type]
            )

        self.assertIn("offset=0", url)
        self.assertEqual(len(calls), 2)
        self.assertIn("limit=2", calls[0])
        self.assertIn("offset=2", calls[1])
        self.assertEqual(payload["count"], 3)
        self.assertEqual(len(payload["observations"]), 3)

    def test_fetch_observations_payload_fails_when_count_is_not_satisfied(self) -> None:
        class TruncatedHttp:
            def get_json(self, _url: str) -> object:
                return {"count": 3, "observations": [{"date": "2024-01-01"}]}

        with (
            patch.object(fred, "OBSERVATIONS_PAGE_LIMIT", 2),
            self.assertRaisesRegex(ValueError, "ended after 1 of 3 rows"),
        ):
            _fetch_observations_payload(
                SeriesTarget("DGS10", "10-Year Treasury Yield"),
                "KEY123",
                TruncatedHttp(),  # type: ignore[arg-type]
            )


class RequireApiKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = os.environ.pop("FRED_API_KEY", None)

    def tearDown(self) -> None:
        if self._saved is not None:
            os.environ["FRED_API_KEY"] = self._saved
        else:
            os.environ.pop("FRED_API_KEY", None)

    def test_returns_value_when_set(self) -> None:
        os.environ["FRED_API_KEY"] = "abc123"
        self.assertEqual(require_api_key(), "abc123")

    def test_raises_with_helpful_message_when_missing(self) -> None:
        with self.assertRaisesRegex(SystemExit, "FRED_API_KEY"):
            require_api_key()


class RateLimitDefaultTests(unittest.TestCase):
    def test_default_rate_limit_is_per_second(self) -> None:
        self.assertEqual(DEFAULT_RATE_LIMIT.requests, 1)
        self.assertEqual(DEFAULT_RATE_LIMIT.window_seconds, 1.0)


# Smoke that the module is importable end-to-end and json import is wired.
class ImportSmokeTests(unittest.TestCase):
    def test_json_module_in_use(self) -> None:
        # Sanity ping that load_series + json don't trip on each other when
        # the YAML payload happens to contain JSON-quoted text.
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "w.yml"
            path.write_text(
                f"macro_series:\n  - id: X\n    name: {json.dumps('quoted name')}\n",
                encoding="utf-8",
            )
            series = load_series(path)
        self.assertEqual(series[0].name, "quoted name")


if __name__ == "__main__":
    unittest.main()
