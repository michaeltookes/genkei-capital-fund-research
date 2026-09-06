"""Unit tests for the CLI watchlist resolver helper."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    CryptoEntry,
    CryptoPriceTargetEntry,
    EquityEntry,
    EthWhaleAddressEntry,
    MacroEntry,
    ProtocolEntry,
    YahooPriceTargetEntry,
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

    def test_reads_price_only_yahoo_targets_without_equity_classification(self) -> None:
        path = self._write(
            "yahoo_price_targets:\n"
            "  - symbol: CYBL\n"
            "    name: Cyberlux Corporation\n"
            "    role: Price-only reflection coverage.\n"
            "    asset_class: otc_equity\n"
        )
        wl = load_watchlist(path)
        self.assertEqual(
            wl.yahoo_price_targets[0],
            YahooPriceTargetEntry(
                symbol="CYBL",
                name="Cyberlux Corporation",
                role="Price-only reflection coverage.",
                asset_class="otc_equity",
            ),
        )
        self.assertIsNotNone(wl.find_yahoo_price_target("cybl"))
        self.assertIsNone(wl.find_equity("CYBL"))
        self.assertIsNone(wl.classify("CYBL"))

    def test_reads_price_only_crypto_targets_without_crypto_classification(self) -> None:
        path = self._write(
            "crypto_price_targets:\n"
            "  - symbol: LQTY\n"
            "    name: Liquity\n"
            "    coingecko_id: liquity\n"
            "    role: Price-only reflection coverage.\n"
            "    asset_class: crypto\n"
        )
        wl = load_watchlist(path)
        self.assertEqual(
            wl.crypto_price_targets[0],
            CryptoPriceTargetEntry(
                symbol="LQTY",
                name="Liquity",
                coingecko_id="liquity",
                role="Price-only reflection coverage.",
                asset_class="crypto",
            ),
        )
        self.assertIsNotNone(wl.find_crypto_price_target("lqty"))
        self.assertIsNone(wl.find_crypto("LQTY"))
        self.assertIsNone(wl.classify("LQTY"))

    def test_default_watchlist_has_hype_primary_coverage(self) -> None:
        wl = load_watchlist(DEFAULT_WATCHLIST_PATH)
        hype = wl.find_crypto("hype")
        self.assertIsNotNone(hype)
        assert hype is not None
        self.assertEqual(hype.coingecko_id, "hyperliquid")
        self.assertEqual(hype.tier, "primary")
        self.assertEqual(hype.sleeve, "tactical")
        self.assertEqual(hype.coinbase_product, "HYPE-USD")
        self.assertIn("Hyperliquid", hype.gdelt_terms)
        self.assertIsNone(wl.find_crypto_price_target("HYPE"))

    def test_default_watchlist_has_xrp_price_only_coverage(self) -> None:
        wl = load_watchlist(DEFAULT_WATCHLIST_PATH)
        xrp = wl.find_crypto_price_target("xrp")
        self.assertIsNotNone(xrp)
        assert xrp is not None
        self.assertEqual(xrp.coingecko_id, "xrp")
        self.assertEqual(xrp.asset_class, "crypto")
        self.assertIsNone(wl.find_crypto("XRP"))
        self.assertIsNone(wl.classify("XRP"))

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

    def test_packaged_jupiter_protocols_group_under_jup_token(self) -> None:
        wl = load_watchlist(DEFAULT_WATCHLIST_PATH)
        jupiter_protocols = {p.slug: p for p in wl.protocols if p.slug.startswith("jupiter")}

        self.assertEqual(
            {slug: p.coingecko_id for slug, p in jupiter_protocols.items()},
            {
                "jupiter": "jupiter-exchange-solana",
                "jupiter-perpetual-exchange": "jupiter-exchange-solana",
                "jupiter-lend": "jupiter-exchange-solana",
                "jupiter-staked-sol": "jupiter-exchange-solana",
            },
        )
        self.assertFalse(jupiter_protocols["jupiter"].include_in_tvl_scoring)
        self.assertTrue(
            all(
                jupiter_protocols[slug].include_in_tvl_scoring
                for slug in {
                    "jupiter-perpetual-exchange",
                    "jupiter-lend",
                    "jupiter-staked-sol",
                }
            )
        )


class LoadEthWhaleAddressesTests(unittest.TestCase):
    """B-106 — `eth_whale_addresses:` parser pins fail-fast config validation."""

    def _write(self, body: str) -> Path:
        ctx = TemporaryDirectory()
        self.addCleanup(ctx.cleanup)
        tmp = Path(ctx.name)
        path = tmp / "watchlists.yml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_reads_and_normalizes_whale_addresses(self) -> None:
        path = self._write(
            "eth_whale_addresses:\n"
            "  - address: '0xDE0B295669a9FD93d5F28D9Ec85E40f4cb697BAe'\n"
            "    label: Ethereum Foundation\n"
            "    category: foundation\n"
            "    notes: Public label.\n"
        )
        wl = load_watchlist(path)
        self.assertEqual(
            wl.eth_whale_addresses,
            [
                EthWhaleAddressEntry(
                    address="0xde0b295669a9fd93d5f28d9ec85e40f4cb697bae",
                    label="Ethereum Foundation",
                    category="foundation",
                    notes="Public label.",
                )
            ],
        )

    def test_rejects_non_hex_whale_address(self) -> None:
        path = self._write(
            "eth_whale_addresses:\n"
            "  - address: '0xzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz'\n"
            "    label: Bad\n"
            "    category: whale\n"
        )
        with self.assertRaisesRegex(ValueError, "Invalid eth_whale_addresses.address"):
            load_watchlist(path)

    def test_rejects_invalid_whale_category(self) -> None:
        path = self._write(
            "eth_whale_addresses:\n"
            "  - address: '0xde0b295669a9fd93d5f28d9ec85e40f4cb697bae'\n"
            "    label: Bad\n"
            "    category: market-maker\n"
        )
        with self.assertRaisesRegex(ValueError, "Invalid eth_whale_addresses.category"):
            load_watchlist(path)


if __name__ == "__main__":
    unittest.main()
