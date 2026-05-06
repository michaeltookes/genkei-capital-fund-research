"""Unit tests for DeFiLlama normalization helpers."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.normalize_defillama import (
    TargetAsset,
    classify_momentum,
    classify_zombie_risk,
    latest_snapshot_dir,
    normalize_bitcoin_ecosystem,
    normalize_prices,
    normalize_snapshot,
    parse_target_assets,
    normalize_stablecoins,
    validate_list_of_strings,
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

    def test_parse_target_assets_rejects_non_string_primary_chain_labels(self) -> None:
        config = {
            "target_assets": [
                {
                    "symbol": "BTC",
                    "name": "Bitcoin",
                    "coingecko_id": "bitcoin",
                    "primary_chain_labels": ["Bitcoin", 123],
                    "ecosystem": "Bitcoin ecosystem",
                }
            ]
        }

        with self.assertRaisesRegex(SystemExit, "primary_chain_labels for target asset BTC"):
            parse_target_assets(config)

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

    def test_latest_snapshot_dir_reports_missing_raw_directory(self) -> None:
        with TemporaryDirectory() as temp_dir:
            missing_dir = Path(temp_dir) / "missing"

            with self.assertRaisesRegex(SystemExit, "Raw snapshots directory"):
                latest_snapshot_dir(missing_dir)

    def test_validate_list_of_strings_rejects_strings_and_mappings(self) -> None:
        with self.assertRaisesRegex(TypeError, "chain_focus"):
            validate_list_of_strings("Ethereum", "chain_focus")

        with self.assertRaisesRegex(TypeError, "bitcoin_ecosystem_labels"):
            validate_list_of_strings({"name": "Bitcoin"}, "bitcoin_ecosystem_labels")

    def test_normalize_snapshot_uses_manifest_date_for_output_filename(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.json"
            raw_dir = root / "raw"
            snapshot_dir = raw_dir / "20260504T120000Z"
            output_dir = root / "normalized"
            snapshot_dir.mkdir(parents=True)

            config = {
                "target_assets": [
                    {
                        "symbol": "BTC",
                        "name": "Bitcoin",
                        "coingecko_id": "bitcoin",
                        "primary_chain_labels": ["Bitcoin"],
                        "ecosystem": "Bitcoin ecosystem",
                    }
                ],
                "chain_focus": ["Bitcoin"],
                "bitcoin_ecosystem_labels": ["Bitcoin"],
            }
            config_path.write_text(json.dumps(config), encoding="utf-8")
            (snapshot_dir / "manifest.json").write_text(
                json.dumps({"collected_at": "2026-05-04T23:59:00+00:00"}),
                encoding="utf-8",
            )
            (snapshot_dir / "prices_current.json").write_text(json.dumps({"coins": {}}), encoding="utf-8")
            (snapshot_dir / "chains.json").write_text(json.dumps([]), encoding="utf-8")
            (snapshot_dir / "protocols.json").write_text(json.dumps([]), encoding="utf-8")
            (snapshot_dir / "stablecoins.json").write_text(json.dumps({"peggedAssets": []}), encoding="utf-8")

            output_path = normalize_snapshot(config_path, raw_dir, output_dir)

            self.assertEqual(output_dir / "daily-2026-05-04.json", output_path)
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual("2026-05-04", payload["snapshot_date"])

    def test_normalize_snapshot_reports_invalid_chain_focus_without_traceback(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.json"
            raw_dir = root / "raw"
            snapshot_dir = raw_dir / "20260504T120000Z"
            output_dir = root / "normalized"
            snapshot_dir.mkdir(parents=True)

            config = {
                "target_assets": [
                    {
                        "symbol": "BTC",
                        "name": "Bitcoin",
                        "coingecko_id": "bitcoin",
                        "primary_chain_labels": ["Bitcoin"],
                        "ecosystem": "Bitcoin ecosystem",
                    }
                ],
                "chain_focus": "Bitcoin",
                "bitcoin_ecosystem_labels": ["Bitcoin"],
            }
            config_path.write_text(json.dumps(config), encoding="utf-8")
            (snapshot_dir / "manifest.json").write_text(
                json.dumps({"collected_at": "2026-05-04T23:59:00+00:00"}),
                encoding="utf-8",
            )
            (snapshot_dir / "prices_current.json").write_text(json.dumps({"coins": {}}), encoding="utf-8")
            (snapshot_dir / "chains.json").write_text(json.dumps([]), encoding="utf-8")
            (snapshot_dir / "protocols.json").write_text(json.dumps([]), encoding="utf-8")
            (snapshot_dir / "stablecoins.json").write_text(json.dumps({"peggedAssets": []}), encoding="utf-8")

            with self.assertRaisesRegex(SystemExit, "Invalid config: chain_focus"):
                normalize_snapshot(config_path, raw_dir, output_dir)


if __name__ == "__main__":
    unittest.main()
