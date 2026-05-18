"""Unit tests for the CLI watchlist resolver helper."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    CryptoEntry,
    EquityEntry,
    MacroEntry,
    ProtocolEntry,
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
            "      sleeve: core\n"
            "  secondary:\n"
            "    - symbol: PYTH\n"
            "      name: Pyth\n"
            "      coingecko_id: pyth-network\n"
            "equities:\n"
            "  primary:\n"
            "    - symbol: AAPL\n"
            "      name: Apple Inc.\n"
            '      cik: "0000320193"\n'
            "      sleeve: core\n"
            "macro_series:\n"
            "  - id: DGS10\n"
            "    name: 10Y Treasury\n"
        )
        wl = load_watchlist(path)
        self.assertEqual(
            wl.crypto[0],
            CryptoEntry("BTC", "Bitcoin", "bitcoin", "primary", "core"),
        )
        self.assertEqual(wl.crypto[1].tier, "secondary")
        self.assertEqual(wl.crypto[0].sleeve, "core")
        self.assertEqual(
            wl.equities[0],
            EquityEntry("AAPL", "Apple Inc.", "0000320193", "primary", "core"),
        )
        self.assertEqual(wl.equities[0].sleeve, "core")
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
            self.assertEqual(DEFAULT_WATCHLIST_PATH.name, "watchlists.yml")
            self.assertEqual(DEFAULT_WATCHLIST_PATH.parent.name, "data")
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

    def test_whitespace_only_cik_is_treated_as_missing(self) -> None:
        path = self._write(
            "equities:\n"
            "  primary:\n"
            "    - symbol: BLANK\n"
            "      name: Blank CIK Co.\n"
            '      cik: "   "\n'
        )
        wl = load_watchlist(path)
        self.assertIsNone(wl.equities[0].cik)

    def test_rejects_missing_file(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_watchlist(Path("/no/such/path.yml"))


class LoadProtocolsTests(unittest.TestCase):
    """B-081 — `protocols:` section parsing into ProtocolEntry list."""

    def _write(self, body: str) -> Path:
        ctx = TemporaryDirectory()
        self.addCleanup(ctx.cleanup)
        tmp = Path(ctx.name)
        path = tmp / "watchlists.yml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_reads_primary_and_secondary_tiers(self) -> None:
        path = self._write(
            "protocols:\n"
            "  primary:\n"
            "    - slug: aave-v3\n"
            "      name: Aave V3\n"
            "      category: Lending\n"
            "      rationale: Sector pulse.\n"
            "  secondary:\n"
            "    - slug: balancer-v2\n"
            "      name: Balancer V2\n"
        )
        wl = load_watchlist(path)
        self.assertEqual(
            wl.protocols[0],
            ProtocolEntry(
                slug="aave-v3",
                name="Aave V3",
                category="Lending",
                tier="primary",
                rationale="Sector pulse.",
            ),
        )
        self.assertEqual(wl.protocols[1].tier, "secondary")
        # category may be missing — passes through as None
        self.assertIsNone(wl.protocols[1].category)

    def test_skips_entries_without_slug(self) -> None:
        path = self._write(
            "protocols:\n"
            "  primary:\n"
            "    - slug: aave-v3\n"
            "      name: Aave V3\n"
            "    - name: Slugless protocol\n"  # missing slug — drops
            "    - 'a string entry, not a mapping'\n"  # drops
        )
        wl = load_watchlist(path)
        self.assertEqual([p.slug for p in wl.protocols], ["aave-v3"])

    def test_find_protocol_is_case_insensitive(self) -> None:
        path = self._write(
            "protocols:\n  primary:\n    - slug: chainlink\n      name: Chainlink\n"
        )
        wl = load_watchlist(path)
        self.assertIsNotNone(wl.find_protocol("chainlink"))
        self.assertIsNotNone(wl.find_protocol("CHAINLINK"))
        self.assertIsNone(wl.find_protocol("aave-v3"))

    def test_classify_routes_protocol_slugs(self) -> None:
        path = self._write(
            "protocols:\n  primary:\n    - slug: lido\n      name: Lido\n"
        )
        wl = load_watchlist(path)
        self.assertEqual(wl.classify("lido"), "protocol")
        self.assertIsNone(wl.classify("not-a-real-slug"))

    def test_absent_protocols_section_is_safe(self) -> None:
        # Real-world: a watchlist that hasn't been updated to include
        # the protocols section. The loader must still return an empty
        # list, not crash.
        path = self._write(
            "crypto:\n  primary:\n"
            "    - symbol: BTC\n      name: BTC\n      coingecko_id: bitcoin\n"
        )
        wl = load_watchlist(path)
        self.assertEqual(wl.protocols, [])

    def test_packaged_watchlist_has_at_least_one_primary_protocol(self) -> None:
        # Pin the contract that the bundled watchlist always carries at
        # least one primary protocol — otherwise the daily defillama
        # collect's per-protocol step becomes a no-op silently.
        wl = load_watchlist(DEFAULT_WATCHLIST_PATH)
        primary = [p for p in wl.protocols if p.tier == "primary"]
        self.assertGreater(len(primary), 0)


if __name__ == "__main__":
    unittest.main()
