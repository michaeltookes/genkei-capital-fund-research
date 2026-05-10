"""Unit tests for analyst report rendering."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from genkei.reports.defillama_daily import build_daily_report, build_report


class BuildDailyReportTests(unittest.TestCase):
    """Verify daily brief content from normalized input."""

    def test_build_report_uses_analyst_sections_and_target_scope(self) -> None:
        data = {
            "generated_at": "2026-05-05T18:00:00+00:00",
            "scope": {"target_assets": ["BTC", "ETH", "SOL"]},
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
                    "trend_label": "short-term deterioration",
                    "zombie_risk": "normal",
                }
            ],
            "data_quality": {"stablecoin_chain_data": "available", "missing_stablecoin_chains": []},
            "stablecoin_flows": [
                {
                    "chain": "Ethereum",
                    "stablecoin_supply_usd": 1_000_000_000,
                    "focus_supply_share_pct": 100,
                    "money_flow_label": "deep stablecoin liquidity",
                }
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
        self.assertIn("Focused assets: BTC, ETH, SOL", report)
        self.assertIn("## DCA timing support", report)
        self.assertIn("## Bitcoin ecosystem", report)
        self.assertIn("Stacks DEX", report)
        self.assertIn("Twitter-only sentiment", report)
        self.assertIn("not financial advice", report)
        self.assertIn("deep stablecoin liquidity", report)
        self.assertIn("(100.00% of focused-chain supply)", report)
        self.assertNotIn("(+100.00% of focused-chain supply)", report)
        self.assertIn("Signal label: **caution**", report)

    def test_build_report_caveats_dca_when_stablecoin_data_unavailable(self) -> None:
        data = {
            "data_quality": {"stablecoin_chain_data": "unavailable"},
            "chain_tvl": [
                {
                    "name": "Ethereum",
                    "momentum_label": "expanding",
                    "trend_label": "confirmed uptrend",
                }
            ],
        }

        report = build_report(data)

        self.assertIn("Stablecoin chain data status: **unavailable**", report)
        self.assertIn("Signal label: **neutral**", report)
        self.assertNotIn("Constructive: TVL momentum is expanding", report)
        self.assertIn("money-flow conviction is capped", report)

    def test_build_report_separates_bitcoin_cex_custody_exposure(self) -> None:
        data = {
            "bitcoin_ecosystem": [
                {"name": "Stacks DEX", "matched_chains": ["Stacks"], "tvl_usd": 1_000_000}
            ],
            "bitcoin_excluded_exposure": [
                {"name": "Binance BTC", "matched_chains": ["Bitcoin"], "tvl_usd": 5_000_000_000}
            ],
        }

        report = build_report(data)

        self.assertIn("## Bitcoin CEX/custody exposure excluded from ecosystem signal", report)
        self.assertIn("Binance BTC", report)
        self.assertIn("not Bitcoin-native DeFi demand", report)

    def test_build_report_falls_back_to_default_target_scope(self) -> None:
        report = build_report({})

        self.assertIn("Focused assets: BTC, ETH, SOL, LINK, SUI", report)

    def test_build_report_handles_invalid_matched_chains(self) -> None:
        data = {
            "protocol_exposure": [
                {"name": "None DEX", "matched_chains": None},
                {"name": "Mixed DEX", "matched_chains": ["Ethereum", 123, None]},
            ],
            "bitcoin_ecosystem": [],
        }

        report = build_report(data)

        self.assertIn("None DEX (n/a)", report)
        self.assertIn("Mixed DEX (Ethereum, 123)", report)

    def test_build_daily_report_uses_normalized_snapshot_date_for_filename(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            normalized_path = root / "daily-2026-05-04.json"
            normalized_path.write_text('{"snapshot_date": "2026-05-04"}', encoding="utf-8")

            output_path = build_daily_report(normalized_path, root / "reports")

            self.assertEqual(root / "reports" / "defillama-daily-2026-05-04.md", output_path)
            self.assertTrue(output_path.exists())


if __name__ == "__main__":
    unittest.main()
