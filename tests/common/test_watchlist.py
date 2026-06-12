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

from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    BenchmarkEntry,
    CotMarketEntry,
    EiaSeriesEntry,
    EtfTickerEntry,
    TreasurySeriesEntry,
    load_watchlist,
)

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


COT_MARKETS_YAML = """\
version: 1
cot_markets:
  - code: "133741"
    symbol: BTC
    name: BITCOIN - CHICAGO MERCANTILE EXCHANGE
    report_type: tff
    sleeve: "crypto:core"
    rationale: BTC futures TFF.
  - code: "088691"
    symbol: GC
    name: GOLD - COMMODITY EXCHANGE INC.
    report_type: disaggregated
    sleeve: macro
"""


class CotMarketsParserTests(unittest.TestCase):
    """Pin the cot_markets parsing path so YAML drift surfaces loudly (B-031)."""

    def test_parses_typed_entries(self) -> None:
        w = _load(COT_MARKETS_YAML)
        self.assertEqual(len(w.cot_markets), 2)
        btc, gold = w.cot_markets
        self.assertEqual(
            btc,
            CotMarketEntry(
                code="133741",
                symbol="BTC",
                name="BITCOIN - CHICAGO MERCANTILE EXCHANGE",
                report_type="tff",
                sleeve="crypto:core",
                rationale="BTC futures TFF.",
            ),
        )
        # Optional rationale defaults to None when absent
        self.assertIsNone(gold.rationale)
        # sleeve defaults to "macro" when omitted? (here it's explicit)
        self.assertEqual(gold.sleeve, "macro")

    def test_integer_code_coerces_to_string(self) -> None:
        # YAML parses unquoted numerics as ints; codes like 67651 would
        # silently drop the leading zero ("067651") if we didn't keep
        # them as strings. The loader stringifies but does NOT zero-pad.
        body = (
            "version: 1\n"
            "cot_markets:\n"
            "  - code: 133741\n"
            "    symbol: BTC\n"
            "    name: BTC\n"
            "    report_type: tff\n"
        )
        w = _load(body)
        self.assertEqual(len(w.cot_markets), 1)
        self.assertEqual(w.cot_markets[0].code, "133741")

    def test_unknown_report_type_drops_row(self) -> None:
        body = (
            "version: 1\n"
            "cot_markets:\n"
            "  - code: '999'\n"
            "    symbol: XX\n"
            "    name: BAD\n"
            "    report_type: legacy\n"  # not yet supported
            "  - code: '133741'\n"
            "    symbol: BTC\n"
            "    name: BTC\n"
            "    report_type: tff\n"
        )
        w = _load(body)
        symbols = {m.symbol for m in w.cot_markets}
        self.assertEqual(symbols, {"BTC"})

    def test_duplicate_codes_dedupe_first_wins(self) -> None:
        body = (
            "version: 1\n"
            "cot_markets:\n"
            "  - code: '133741'\n"
            "    symbol: BTC\n"
            "    name: First\n"
            "    report_type: tff\n"
            "  - code: '133741'\n"
            "    symbol: BTCX\n"
            "    name: Duplicate\n"
            "    report_type: tff\n"
        )
        w = _load(body)
        self.assertEqual(len(w.cot_markets), 1)
        self.assertEqual(w.cot_markets[0].symbol, "BTC")
        self.assertEqual(w.cot_markets[0].name, "First")

    def test_find_cot_market_by_symbol_or_code(self) -> None:
        w = _load(COT_MARKETS_YAML)
        self.assertEqual(w.find_cot_market("btc").code, "133741")
        self.assertEqual(w.find_cot_market("BTC").code, "133741")
        self.assertEqual(w.find_cot_market("133741").symbol, "BTC")
        self.assertIsNone(w.find_cot_market("nope"))

    def test_absent_section_yields_empty_list(self) -> None:
        w = _load("version: 1\n")
        self.assertEqual(w.cot_markets, [])


ETF_TICKERS_YAML = """\
version: 1
etf_tickers:
  - ticker: IBIT
    name: iShares Bitcoin Trust ETF
    asset: BTC
    issuer: BlackRock
    launch_date: 2024-01-11
    sleeve: tactical
    rationale: Largest spot BTC ETF.
  - ticker: ETHA
    name: iShares Ethereum Trust ETF
    asset: ETH
    issuer: BlackRock
"""

TREASURY_YAML = """\
version: 1
treasury:
  - series_id: TOTAL_INTEREST_EXPENSE_MTD
    name: Monthly total interest expense
    endpoint: /v2/accounting/od/interest_expense
    value_field: month_expense_amt
    frequency: M
    aggregate: sum
    units: USD
  - series_id: TGA_CLOSING_BAL
    name: TGA closing balance
    endpoint: /v1/accounting/dts/operating_cash_balance
    value_field: open_today_bal
    frequency: D
    row_filter:
      account_type: Treasury General Account (TGA) Closing Balance
"""


class EtfTickersParserTests(unittest.TestCase):
    """Pin the etf_tickers loader path (B-105)."""

    def test_parses_typed_entries(self) -> None:
        """ETF ticker rows load into typed watchlist entries."""
        w = _load(ETF_TICKERS_YAML)
        self.assertEqual(len(w.etf_tickers), 2)
        ibit, etha = w.etf_tickers
        self.assertEqual(
            ibit,
            EtfTickerEntry(
                ticker="IBIT",
                name="iShares Bitcoin Trust ETF",
                asset="BTC",
                issuer="BlackRock",
                sleeve="tactical",
                launch_date="2024-01-11",
                rationale="Largest spot BTC ETF.",
            ),
        )
        # Optional fields default cleanly
        self.assertEqual(etha.sleeve, "tactical")
        self.assertIsNone(etha.launch_date)
        self.assertIsNone(etha.rationale)

    def test_ticker_uppercased_on_load(self) -> None:
        """Ticker, asset, and issuer strings are normalized on load."""
        body = (
            "version: 1\n"
            "etf_tickers:\n"
            "  - ticker: ' ibit '\n"
            "    name: iShares\n"
            "    asset: ' btc '\n"
            "    issuer: ' BlackRock '\n"
        )
        w = _load(body)
        self.assertEqual(w.etf_tickers[0].ticker, "IBIT")
        self.assertEqual(w.etf_tickers[0].asset, "BTC")
        self.assertEqual(w.etf_tickers[0].issuer, "BlackRock")

    def test_whitespace_ticker_drops_row(self) -> None:
        """Whitespace-only ETF tickers are rejected before Yahoo ingestion."""
        body = (
            "version: 1\n"
            "etf_tickers:\n"
            "  - ticker: '   '\n"
            "    name: malformed\n"
            "    asset: BTC\n"
            "    issuer: hypothetical\n"
            "  - ticker: IBIT\n"
            "    name: ok\n"
            "    asset: BTC\n"
            "    issuer: BlackRock\n"
        )
        w = _load(body)
        self.assertEqual([entry.ticker for entry in w.etf_tickers], ["IBIT"])

    def test_unknown_asset_drops_row(self) -> None:
        """Unsupported ETF asset buckets are skipped by the v1 parser."""
        body = (
            "version: 1\n"
            "etf_tickers:\n"
            "  - ticker: SOLX\n"
            "    name: bad asset class\n"
            "    asset: SOL\n"  # v1 only supports BTC + ETH
            "    issuer: hypothetical\n"
            "  - ticker: IBIT\n"
            "    name: ok\n"
            "    asset: BTC\n"
            "    issuer: BlackRock\n"
        )
        w = _load(body)
        tickers = {e.ticker for e in w.etf_tickers}
        self.assertEqual(tickers, {"IBIT"})

    def test_duplicate_ticker_dedupes_first_wins(self) -> None:
        """Duplicate ETF tickers dedupe case-insensitively with first row kept."""
        body = (
            "version: 1\n"
            "etf_tickers:\n"
            "  - ticker: IBIT\n"
            "    name: First\n"
            "    asset: BTC\n"
            "    issuer: BlackRock\n"
            "  - ticker: ibit\n"  # case-insensitive collision
            "    name: Duplicate\n"
            "    asset: BTC\n"
            "    issuer: hypothetical\n"
        )
        w = _load(body)
        self.assertEqual(len(w.etf_tickers), 1)
        self.assertEqual(w.etf_tickers[0].name, "First")

    def test_find_etf_ticker_case_insensitive(self) -> None:
        """ETF ticker lookup ignores ticker case."""
        w = _load(ETF_TICKERS_YAML)
        self.assertEqual(w.find_etf_ticker("ibit").asset, "BTC")
        self.assertEqual(w.find_etf_ticker("IBIT").issuer, "BlackRock")
        self.assertIsNone(w.find_etf_ticker("UNKNOWN"))

    def test_etfs_for_asset_routes_correctly(self) -> None:
        """ETF asset lookup returns only entries for the requested bucket."""
        w = _load(ETF_TICKERS_YAML)
        btc_etfs = w.etfs_for_asset("BTC")
        eth_etfs = w.etfs_for_asset("ETH")
        self.assertEqual({e.ticker for e in btc_etfs}, {"IBIT"})
        self.assertEqual({e.ticker for e in eth_etfs}, {"ETHA"})
        # case-insensitive
        self.assertEqual(
            {e.ticker for e in w.etfs_for_asset("eth")}, {"ETHA"}
        )
        # unknown asset returns empty rather than raising
        self.assertEqual(w.etfs_for_asset("DOGE"), [])

    def test_absent_section_yields_empty_list(self) -> None:
        """Missing etf_tickers config produces an empty ETF list."""
        w = _load("version: 1\n")
        self.assertEqual(w.etf_tickers, [])

    def test_packaged_spot_etf_basket_includes_late_additions(self) -> None:
        """Packaged ETF basket includes later BTC and ETH product launches."""
        w = load_watchlist(DEFAULT_WATCHLIST_PATH)
        btc_tickers = {entry.ticker for entry in w.etfs_for_asset("BTC")}
        eth_tickers = {entry.ticker for entry in w.etfs_for_asset("ETH")}

        self.assertEqual(len(btc_tickers), 12)
        self.assertIn("DEFI", btc_tickers)
        self.assertIn("BTC", btc_tickers)
        self.assertEqual(len(eth_tickers), 10)
        self.assertIn("ETHB", eth_tickers)
        ethb = w.find_etf_ticker("ethb")
        self.assertIsNotNone(ethb)
        self.assertEqual(ethb.issuer, "BlackRock")
        self.assertEqual(ethb.launch_date, "2026-03-12")


class TreasuryParserTests(unittest.TestCase):
    """Pin Treasury watchlist parsing for row filters and aggregate totals."""

    def test_parses_sum_aggregate(self) -> None:
        w = _load(TREASURY_YAML)
        interest = w.find_treasury("total_interest_expense_mtd")
        self.assertIsNotNone(interest)
        self.assertIsInstance(interest, TreasurySeriesEntry)
        self.assertEqual(interest.aggregate, "sum")
        self.assertEqual(interest.row_filter, {})

    def test_row_filter_series_keeps_default_aggregate(self) -> None:
        w = _load(TREASURY_YAML)
        tga = w.find_treasury("TGA_CLOSING_BAL")
        self.assertIsNotNone(tga)
        self.assertIsNone(tga.aggregate)
        self.assertEqual(
            tga.row_filter,
            {"account_type": "Treasury General Account (TGA) Closing Balance"},
        )

    def test_unknown_aggregate_drops_row(self) -> None:
        body = (
            "version: 1\n"
            "treasury:\n"
            "  - series_id: BAD_AGG\n"
            "    name: bad\n"
            "    endpoint: /v2/accounting/od/interest_expense\n"
            "    value_field: month_expense_amt\n"
            "    frequency: M\n"
            "    aggregate: average\n"
            "  - series_id: GOOD_AGG\n"
            "    name: good\n"
            "    endpoint: /v2/accounting/od/interest_expense\n"
            "    value_field: month_expense_amt\n"
            "    frequency: M\n"
            "    aggregate: sum\n"
        )
        w = _load(body)
        self.assertEqual([entry.series_id for entry in w.treasury], ["GOOD_AGG"])

    def test_packaged_interest_expense_is_total_aggregate(self) -> None:
        w = load_watchlist(DEFAULT_WATCHLIST_PATH)
        interest = w.find_treasury("TOTAL_INTEREST_EXPENSE_MTD")
        self.assertIsNotNone(interest)
        self.assertEqual(interest.aggregate, "sum")
        self.assertEqual(interest.row_filter, {})


class EiaParserTests(unittest.TestCase):
    """Pin EIA watchlist parsing for facet validation."""

    def test_rejects_malformed_facets(self) -> None:
        body = (
            "version: 1\n"
            "eia:\n"
            "  - series_id: BAD_FACETS\n"
            "    name: bad\n"
            "    route: petroleum/pri/spt\n"
            "    frequency: D\n"
            "    facets:\n"
            "      - series\n"
            "  - series_id: WTI_SPOT\n"
            "    name: good\n"
            "    route: petroleum/pri/spt\n"
            "    frequency: D\n"
            "    facets:\n"
            "      series: RWTC\n"
        )
        w = _load(body)
        self.assertEqual([entry.series_id for entry in w.eia], ["WTI_SPOT"])
        self.assertIsInstance(w.eia[0], EiaSeriesEntry)

    def test_rejects_partially_malformed_facets(self) -> None:
        body = (
            "version: 1\n"
            "eia:\n"
            "  - series_id: BAD_FACET_VALUE\n"
            "    name: bad\n"
            "    route: petroleum/pri/spt\n"
            "    frequency: D\n"
            "    facets:\n"
            "      series: true\n"
            "      location: US\n"
            "  - series_id: WTI_SPOT\n"
            "    name: good\n"
            "    route: petroleum/pri/spt\n"
            "    frequency: D\n"
            "    facets:\n"
            "      series: RWTC\n"
        )
        w = _load(body)
        self.assertEqual([entry.series_id for entry in w.eia], ["WTI_SPOT"])

    def test_strips_valid_facet_keys_and_values(self) -> None:
        body = (
            "version: 1\n"
            "eia:\n"
            "  - series_id: WTI_SPOT\n"
            "    name: good\n"
            "    route: petroleum/pri/spt\n"
            "    frequency: D\n"
            "    facets:\n"
            "      ' series ': ' RWTC '\n"
        )
        w = _load(body)
        self.assertEqual(w.eia[0].facets, {"series": "RWTC"})


if __name__ == "__main__":
    unittest.main()
