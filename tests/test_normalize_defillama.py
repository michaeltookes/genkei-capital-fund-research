"""Unit tests for DeFiLlama normalization helpers."""

from __future__ import annotations

import unittest

from scripts.normalize_defillama import (
    TargetAsset,
    classify_momentum,
    classify_zombie_risk,
    normalize_bitcoin_ecosystem,
    normalize_prices,
    normalize_stablecoins,
)


class NormalizeDefillamaTests(unittest.TestCase):
    """Verify deterministic normalization behavior without network calls."""

    def test_normalize_prices_keeps_only_configured_assets(self) -> None:
        assets = [
            TargetAsset(
                symbol="BTC",
                name="Bitcoin",
                coingecko_id="bitcoin",
                primary_chain_labels=("Bitcoin",),
                ecosystem="Bitcoin ecosystem",
            )
        ]
        payload = {
            "coins": {
                "coingecko:bitcoin": {"price": 64000, "timestamp": 123},
                "coingecko:dogecoin": {"price": 0.15, "timestamp": 123},
            }
        }

        records = normalize_prices(payload, assets)

        self.assertEqual(1, len(records))
        self.assertEqual("BTC", records[0]["symbol"])
        self.assertEqual(64000.0, records[0]["price_usd"])

    def test_classify_momentum_flags_loss_threshold(self) -> None:
        self.assertEqual("momentum loss", classify_momentum(-5.0))
        self.assertEqual("softening", classify_momentum(-1.0))
        self.assertEqual("expanding", classify_momentum(0.1))
        self.assertEqual("unknown", classify_momentum(None))

    def test_classify_zombie_risk_uses_tvl_and_weekly_change(self) -> None:
        self.assertEqual("elevated", classify_zombie_risk(9_000_000, -12.0))
        self.assertEqual("watch", classify_zombie_risk(100_000_000, -12.0))
        self.assertEqual("normal", classify_zombie_risk(100_000_000, 1.0))
        self.assertEqual("unknown", classify_zombie_risk(None, 1.0))

    def test_normalize_bitcoin_ecosystem_labels_matching_protocols(self) -> None:
        protocols = [
            {"name": "Stacks DEX", "chains": ["Stacks"], "tvl": 1_000_000, "change_7d": 3},
            {"name": "Ethereum DEX", "chains": ["Ethereum"], "tvl": 2_000_000, "change_7d": 3},
        ]

        records = normalize_bitcoin_ecosystem(protocols, {"Bitcoin", "Stacks"})

        self.assertEqual(1, len(records))
        self.assertEqual("Stacks DEX", records[0]["name"])
        self.assertEqual("Bitcoin ecosystem", records[0]["bucket"])

    def test_normalize_stablecoins_sums_focused_chain_balances(self) -> None:
        payload = {
            "peggedAssets": [
                {"chainBalances": {"Ethereum": {"current": 100}, "Solana": 50}},
                {"chainBalances": {"Ethereum": {"circulating": 25}, "Sui": {"peggedUSD": 10}}},
            ]
        }

        records = normalize_stablecoins(payload, {"Ethereum", "Solana", "Sui"})

        self.assertEqual(
            [
                {"chain": "Ethereum", "stablecoin_supply_usd": 125.0},
                {"chain": "Solana", "stablecoin_supply_usd": 50.0},
                {"chain": "Sui", "stablecoin_supply_usd": 10.0},
            ],
            records,
        )


if __name__ == "__main__":
    unittest.main()
