"""Targeted parser tests for the shared watchlist loader.

The bulk of the watchlist's surface is exercised indirectly through every
ingester / CLI / experiment test, but the new ``benchmarks:`` section
(B-102) is narrow enough to deserve its own pinning so future YAML
changes don't silently break ``BenchmarkEntry`` parsing.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from genkei.common.watchlist import BenchmarkEntry, load_watchlist

BENCHMARKS_YAML = """\
version: 1
benchmarks:
  - symbol: SPY
    name: SPDR S&P 500 ETF Trust
    role: Broad US equity benchmark.
    asset_class: equity_index_etf
  - symbol: QQQ
    name: Invesco QQQ
    role: Tech-tilted benchmark.
"""

DEFAULTS_ONLY_YAML = """\
version: 1
benchmarks:
  - symbol: IWM
    name: iShares Russell 2000
    role: Small-cap benchmark.
"""

NO_BENCHMARKS_YAML = """\
version: 1
equities:
  primary:
    - symbol: AAPL
      cik: "0000320193"
      name: Apple Inc.
"""


def _load(body: str) -> object:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "watchlists.yml"
        path.write_text(body, encoding="utf-8")
        return load_watchlist(path)


class BenchmarksParserTests(unittest.TestCase):
    def test_parses_full_entries(self) -> None:
        w = _load(BENCHMARKS_YAML)
        self.assertEqual([b.symbol for b in w.benchmarks], ["SPY", "QQQ"])
        spy = w.benchmarks[0]
        self.assertIsInstance(spy, BenchmarkEntry)
        self.assertEqual(spy.symbol, "SPY")
        self.assertEqual(spy.name, "SPDR S&P 500 ETF Trust")
        self.assertEqual(spy.role, "Broad US equity benchmark.")
        self.assertEqual(spy.asset_class, "equity_index_etf")

    def test_asset_class_defaults_when_omitted(self) -> None:
        w = _load(DEFAULTS_ONLY_YAML)
        iwm = w.benchmarks[0]
        self.assertEqual(iwm.asset_class, "equity_index_etf")

    def test_no_benchmarks_section_yields_empty_list(self) -> None:
        # A watchlist with no benchmarks: key still parses; the engine just
        # doesn't get a benchmark to compare against.
        w = _load(NO_BENCHMARKS_YAML)
        self.assertEqual(w.benchmarks, [])

    def test_find_benchmark_is_case_insensitive(self) -> None:
        w = _load(BENCHMARKS_YAML)
        self.assertIsNotNone(w.find_benchmark("spy"))
        self.assertIsNotNone(w.find_benchmark("SPY"))
        self.assertIsNone(w.find_benchmark("AAPL"))

    def test_find_benchmark_does_not_collide_with_equities(self) -> None:
        # Equity find_equity must not return a benchmark even when the
        # ticker resembles one.
        w = _load(BENCHMARKS_YAML)
        self.assertIsNone(w.find_equity("SPY"))
        self.assertIsNotNone(w.find_benchmark("SPY"))

    def test_duplicate_symbol_dedupes_silently(self) -> None:
        body = (
            "version: 1\n"
            "benchmarks:\n"
            "  - symbol: SPY\n"
            "    name: First\n"
            "  - symbol: SPY\n"
            "    name: Duplicate\n"
        )
        w = _load(body)
        self.assertEqual(len(w.benchmarks), 1)
        self.assertEqual(w.benchmarks[0].name, "First")


if __name__ == "__main__":
    unittest.main()
