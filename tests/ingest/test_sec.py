"""Unit tests for the SEC collector helpers (offline)."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from genkei.ingest import sec
from genkei.ingest.sec import (
    DEFAULT_RATE_LIMIT,
    DEFAULT_USER_AGENT,
    USER_AGENT_ENV,
    CompanyTarget,
    build_companyfacts_url,
    build_submissions_url,
    load_companies,
    normalize_cik,
    resolve_user_agent,
)


class LoadCompaniesTests(unittest.TestCase):
    def test_reads_equities_with_cik(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(
                "equities:\n"
                "  primary:\n"
                "    - symbol: AAPL\n"
                '      cik: "0000320193"\n'
                "      name: Apple Inc.\n"
                "    - symbol: MSFT\n"
                '      cik: "0000789019"\n'
                "      name: Microsoft Corporation\n",
                encoding="utf-8",
            )
            companies = load_companies(path)
        self.assertEqual(len(companies), 2)
        self.assertEqual(
            companies[0], CompanyTarget(cik="0000320193", symbol="AAPL", name="Apple Inc.")
        )

    def test_normalizes_numeric_and_unpadded_cik_values(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(
                "equities:\n"
                "  primary:\n"
                "    - symbol: AAPL\n"
                "      cik: 320193\n"
                "      name: Apple Inc.\n"
                "    - symbol: MSFT\n"
                '      cik: "789019"\n'
                "      name: Microsoft Corporation\n",
                encoding="utf-8",
            )
            companies = load_companies(path)
        self.assertEqual([c.cik for c in companies], ["0000320193", "0000789019"])

    def test_dedupes_by_cik(self) -> None:
        # GOOG and GOOGL share Alphabet's CIK; collector should fetch once.
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(
                "equities:\n"
                "  primary:\n"
                "    - symbol: GOOG\n"
                '      cik: "0001652044"\n'
                "      name: Alphabet Inc. (Class C)\n"
                "    - symbol: GOOGL\n"
                '      cik: "0001652044"\n'
                "      name: Alphabet Inc. (Class A)\n",
                encoding="utf-8",
            )
            companies = load_companies(path)
        self.assertEqual(len(companies), 1)
        # First-seen wins on the symbol/name fields.
        self.assertEqual(companies[0].symbol, "GOOG")

    def test_skips_entries_without_cik(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(
                "equities:\n"
                "  primary:\n"
                "    - symbol: NOCIK\n"
                "      name: No CIK Here\n"
                "    - symbol: AAPL\n"
                '      cik: "0000320193"\n'
                "      name: Apple Inc.\n",
                encoding="utf-8",
            )
            companies = load_companies(path)
        self.assertEqual([c.symbol for c in companies], ["AAPL"])

    def test_rejects_when_no_equities_have_cik(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(
                "equities:\n  primary:\n    - symbol: NOCIK\n      name: x\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SystemExit, "No equities with CIK"):
                load_companies(path)

    def test_rejects_missing_file(self) -> None:
        with self.assertRaisesRegex(SystemExit, "Watchlist file not found"):
            load_companies(Path("/no/such/path.yml"))


class UrlBuilderTests(unittest.TestCase):
    def test_submissions_url(self) -> None:
        self.assertEqual(
            build_submissions_url("0000320193"),
            "https://data.sec.gov/submissions/CIK0000320193.json",
        )

    def test_companyfacts_url(self) -> None:
        self.assertEqual(
            build_companyfacts_url("0000320193"),
            "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
        )

    def test_normalize_cik_rejects_malformed_values(self) -> None:
        self.assertIsNone(normalize_cik(True))
        self.assertIsNone(normalize_cik("abc"))
        self.assertIsNone(normalize_cik("12345678901"))
        self.assertEqual(normalize_cik("320193"), "0000320193")


class ResolveUserAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = os.environ.pop(USER_AGENT_ENV, None)

    def tearDown(self) -> None:
        if self._saved is not None:
            os.environ[USER_AGENT_ENV] = self._saved
        else:
            os.environ.pop(USER_AGENT_ENV, None)

    def test_uses_env_when_set(self) -> None:
        os.environ[USER_AGENT_ENV] = "Real Person realperson@example.com"
        self.assertEqual(resolve_user_agent(), "Real Person realperson@example.com")

    def test_falls_back_to_placeholder_with_warning(self) -> None:
        with patch.dict(os.environ, {USER_AGENT_ENV: ""}), patch.object(
            sec.LOGGER, "warning"
        ) as warning:
            ua = resolve_user_agent()
        self.assertEqual(ua, DEFAULT_USER_AGENT)
        warning.assert_called_once()
        self.assertIn(USER_AGENT_ENV, warning.call_args.args)


class RateLimitDefaultTests(unittest.TestCase):
    def test_default_rate_limit_is_under_secs_10_per_second_cap(self) -> None:
        # G-021: SEC's documented limit is 10/sec; we stay at 8/sec.
        self.assertEqual(DEFAULT_RATE_LIMIT.requests, 8)
        self.assertEqual(DEFAULT_RATE_LIMIT.window_seconds, 1.0)


if __name__ == "__main__":
    unittest.main()
