"""Unit tests for the Yahoo Finance collector helpers (offline)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from genkei.ingest.yahoo import (
    DAILY_LOOKBACK_DAYS,
    EquityTarget,
    build_chart_url,
    load_equities,
)


class LoadEquitiesTests(unittest.TestCase):
    def test_reads_equity_entries(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(
                "equities:\n"
                "  primary:\n"
                "    - symbol: AAPL\n"
                "      name: Apple Inc.\n"
                '      cik: "0000320193"\n'
                "    - symbol: MSFT\n"
                "      name: Microsoft Corp.\n"
                '      cik: "0000789019"\n',
                encoding="utf-8",
            )
            equities = load_equities(path)
        self.assertEqual(len(equities), 2)
        self.assertEqual(equities[0], EquityTarget(ticker="AAPL"))
        self.assertEqual(equities[1], EquityTarget(ticker="MSFT"))

    def test_raises_when_no_equities(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            # crypto-only watchlist — Yahoo collector has nothing to do.
            path.write_text(
                "crypto:\n"
                "  primary:\n"
                "    - symbol: BTC\n"
                "      name: Bitcoin\n"
                "      coingecko_id: bitcoin\n",
                encoding="utf-8",
            )
            with self.assertRaises(SystemExit) as cm:
                load_equities(path)
            self.assertIn("equity", str(cm.exception).lower())

    def test_rejects_duplicate_symbols(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            # Two AAPL entries — silent double-fetch would waste rate
            # budget; surface loudly instead.
            path.write_text(
                "equities:\n"
                "  primary:\n"
                "    - symbol: AAPL\n"
                "      name: Apple Inc.\n"
                '      cik: "0000320193"\n'
                "    - symbol: AAPL\n"
                "      name: Apple (dupe)\n"
                '      cik: "0000320193"\n',
                encoding="utf-8",
            )
            with self.assertRaises(SystemExit) as cm:
                load_equities(path)
            self.assertIn("Duplicate equity symbol", str(cm.exception))


class BuildChartUrlTests(unittest.TestCase):
    def test_url_shape(self) -> None:
        url = build_chart_url(
            "AAPL",
            period1=0,
            period2=int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp()),
        )
        self.assertIn("/v8/finance/chart/AAPL", url)
        self.assertIn("interval=1d", url)
        self.assertIn("period1=0", url)
        self.assertIn("period2=1704067200", url)

    def test_default_interval_is_daily(self) -> None:
        url = build_chart_url("MSFT", period1=1000, period2=2000)
        self.assertIn("interval=1d", url)

    def test_url_is_deterministic_for_same_inputs(self) -> None:
        self.assertEqual(
            build_chart_url("NVDA", period1=100, period2=200),
            build_chart_url("NVDA", period1=100, period2=200),
        )


class LookbackDefaultTests(unittest.TestCase):
    def test_default_lookback_is_documented(self) -> None:
        # Longer than Coinbase's 7d (which is 7) to absorb equity-market
        # holidays + weekends + occasional Yahoo gaps. The exact number
        # is documented in the module docstring; a behavior-defining
        # pin keeps us from quietly drifting it.
        self.assertEqual(DAILY_LOOKBACK_DAYS, 14)


if __name__ == "__main__":
    unittest.main()
