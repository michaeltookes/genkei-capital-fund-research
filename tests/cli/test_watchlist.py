"""Unit tests for the CLI watchlist resolver helper."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from genkei.cli._watchlist import (
    CryptoEntry,
    DEFAULT_WATCHLIST_PATH,
    EquityEntry,
    MacroEntry,
    load_watchlist,
)


class LoadWatchlistTests(unittest.TestCase):
    def _write(self, body: str) -> Path:
        ctx = TemporaryDirectory()
        self.addCleanup(ctx.cleanup)
        tmp = Path(ctx.name)
        path = tmp / "watchlists.yml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_reads_crypto_equity_macro_sections(self) -> None:
        path = self._write(
            "crypto:\n"
            "  primary:\n"
            "    - symbol: BTC\n"
            "      name: Bitcoin\n"
            "      coingecko_id: bitcoin\n"
            "  secondary:\n"
            "    - symbol: PYTH\n"
            "      name: Pyth\n"
            "      coingecko_id: pyth-network\n"
            "equities:\n"
            "  primary:\n"
            "    - symbol: AAPL\n"
            "      name: Apple Inc.\n"
            '      cik: "0000320193"\n'
            "macro_series:\n"
            "  - id: DGS10\n"
            "    name: 10Y Treasury\n"
        )
        wl = load_watchlist(path)
        self.assertEqual(
            wl.crypto[0],
            CryptoEntry("BTC", "Bitcoin", "bitcoin", "primary"),
        )
        self.assertEqual(wl.crypto[1].tier, "secondary")
        self.assertEqual(
            wl.equities[0],
            EquityEntry("AAPL", "Apple Inc.", "0000320193", "primary"),
        )
        self.assertEqual(wl.macro[0], MacroEntry("DGS10", "10Y Treasury"))

    def test_find_helpers_are_case_insensitive_for_tickers(self) -> None:
        path = self._write(
            "crypto:\n"
            "  primary:\n"
            "    - symbol: BTC\n"
            "      name: Bitcoin\n"
            "      coingecko_id: bitcoin\n"
        )
        wl = load_watchlist(path)
        self.assertIsNotNone(wl.find_crypto("btc"))
        self.assertIsNotNone(wl.find_crypto("BTC"))
        self.assertIsNone(wl.find_crypto("eth"))

    def test_default_watchlist_path_is_independent_of_cwd(self) -> None:
        ctx = TemporaryDirectory()
        self.addCleanup(ctx.cleanup)
        original_cwd = Path.cwd()
        try:
            os.chdir(ctx.name)
            self.assertTrue(DEFAULT_WATCHLIST_PATH.is_absolute())
            wl = load_watchlist()
        finally:
            os.chdir(original_cwd)
        self.assertIsNotNone(wl.find_crypto("BTC"))

    def test_classify_disambiguates_sleeves(self) -> None:
        path = self._write(
            "crypto:\n  primary:\n    - symbol: BTC\n"
            "      name: Bitcoin\n      coingecko_id: bitcoin\n"
            "equities:\n  primary:\n    - symbol: AAPL\n"
            '      name: Apple\n      cik: "0000320193"\n'
            "macro_series:\n  - id: DGS10\n    name: 10Y\n"
        )
        wl = load_watchlist(path)
        self.assertEqual(wl.classify("BTC"), "crypto")
        self.assertEqual(wl.classify("AAPL"), "equity")
        self.assertEqual(wl.classify("DGS10"), "macro")
        self.assertEqual(wl.classify("dgs10"), "macro")
        self.assertIsNone(wl.classify("UNKNOWN"))

    def test_skips_malformed_entries(self) -> None:
        # Entries missing required fields silently drop; loader doesn't raise.
        # That's appropriate for a watchlist — partial bad data shouldn't
        # tank the whole CLI.
        path = self._write(
            "crypto:\n"
            "  primary:\n"
            "    - symbol: BTC\n"
            "      coingecko_id: bitcoin\n"  # no name — still loads
            "    - symbol: NOID\n"  # missing coingecko_id — drops
            "      name: NoID Coin\n"
            "    - 'a string entry, not a mapping'\n"  # drops
        )
        wl = load_watchlist(path)
        self.assertEqual([c.symbol for c in wl.crypto], ["BTC"])

    def test_rejects_missing_file(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_watchlist(Path("/no/such/path.yml"))


if __name__ == "__main__":
    unittest.main()
