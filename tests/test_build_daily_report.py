"""Unit tests for analyst report rendering."""

from __future__ import annotations

import unittest

from scripts.build_daily_report import build_report


class BuildDailyReportTests(unittest.TestCase):
    """Verify daily brief content from normalized input."""

    def test_build_report_uses_analyst_sections_and_target_scope(self) -> None:
        data = {
            "generated_at": "2026-05-05T18:00:00+00:00",
            "asset_prices": [
                {"symbol": "BTC", "ecosystem": "Bitcoin ecosystem", "price_usd": 64000}
            ],
            "chain_tvl": [
                {
                    "name": "Ethereum",
                    "tvl_usd": 10_000_000_000,
                    "change_1d_pct": 1,
                    "change_7d_pct": -6,
                    "change_1m_pct": 2,
                    "momentum_label": "momentum loss",
                    "zombie_risk": "normal",
                }
            ],
            "stablecoin_flows": [
                {"chain": "Ethereum", "stablecoin_supply_usd": 1_000_000_000}
            ],
            "protocol_exposure": [],
            "bitcoin_ecosystem": [
                {
                    "name": "Stacks DEX",
                    "matched_chains": ["Stacks"],
                    "tvl_usd": 1_000_000,
                    "change_7d_pct": 4,
                    "momentum_label": "expanding",
                }
            ],
        }

        report = build_report(data)

        self.assertIn("# DeFiLlama Daily Market Brief", report)
        self.assertIn("Focused assets: BTC, ETH, SOL, LINK, SUI", report)
        self.assertIn("## DCA timing support", report)
        self.assertIn("## Bitcoin ecosystem", report)
        self.assertIn("Stacks DEX", report)
        self.assertIn("Twitter-only sentiment", report)


if __name__ == "__main__":
    unittest.main()
