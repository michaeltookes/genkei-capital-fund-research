"""Unit tests for the DeFiLlama collector (offline)."""

from __future__ import annotations

import unittest

from genkei.ingest.defillama import (
    build_chain_history_targets,
    build_collection_targets,
    build_price_target,
    chain_history_target_name,
    read_base_urls,
)


class BuildTargetsTests(unittest.TestCase):
    def test_chain_history_target_name_normalises_punctuation(self) -> None:
        self.assertEqual(
            chain_history_target_name("Rootstock RSK"), "chain_tvl_history_rootstock_rsk"
        )
        # str.isalnum keeps Unicode digits/letters (²) but normalises spaces.
        self.assertEqual(chain_history_target_name("B² Network"), "chain_tvl_history_b²_network")

    def test_build_price_target_joins_coingecko_keys(self) -> None:
        config = {
            "defillama_base_urls": {"coins": "https://coins.llama.fi"},
            "target_assets": [
                {"coingecko_id": "bitcoin"},
                {"coingecko_id": "ethereum"},
            ],
        }
        target = build_price_target(config)
        self.assertEqual(target.name, "prices_current")
        self.assertEqual(
            target.url,
            "https://coins.llama.fi/prices/current/coingecko:bitcoin,coingecko:ethereum",
        )

    def test_build_price_target_requires_coins_base(self) -> None:
        with self.assertRaisesRegex(SystemExit, "defillama_base_urls.coins is missing"):
            build_price_target(
                {
                    "defillama_base_urls": {"core": "https://api.llama.fi"},
                    "target_assets": [{"coingecko_id": "bitcoin"}],
                }
            )

    def test_build_chain_history_targets_marks_optional_and_url_encodes(self) -> None:
        config = {
            "defillama_base_urls": {"core": "https://api.llama.fi"},
            "chain_focus": ["Rootstock RSK"],
        }
        targets = build_chain_history_targets(config)
        self.assertEqual(len(targets), 1)
        self.assertFalse(targets[0].required)
        self.assertEqual(targets[0].name, "chain_tvl_history_rootstock_rsk")
        self.assertIn("Rootstock%20RSK", targets[0].url)

    def test_build_collection_targets_rejects_duplicate_endpoint_names(self) -> None:
        config = {
            "defillama_base_urls": {
                "core": "https://api.llama.fi",
                "coins": "https://coins.llama.fi",
            },
            "target_assets": [{"coingecko_id": "bitcoin"}],
            "chain_focus": [],
            "collection_endpoints": [
                {"name": "protocols", "base": "core", "path": "/protocols"},
                {"name": "chains", "base": "core", "path": "/v2/chains"},
                {"name": "stablecoins", "base": "core", "path": "/stablecoins"},
                {"name": "protocols", "base": "core", "path": "/v2/chains"},
            ],
        }
        with self.assertRaisesRegex(SystemExit, "Duplicate collection endpoint name: protocols"):
            build_collection_targets(config)

    def test_build_collection_targets_requires_core_endpoints(self) -> None:
        config = {
            "defillama_base_urls": {
                "core": "https://api.llama.fi",
                "coins": "https://coins.llama.fi",
            },
            "target_assets": [{"coingecko_id": "bitcoin"}],
            "chain_focus": [],
            "collection_endpoints": [
                {"name": "protocols", "base": "core", "path": "/protocols"},
            ],
        }
        with self.assertRaisesRegex(
            SystemExit, "Missing required collection endpoints: chains, stablecoins"
        ):
            build_collection_targets(config)

    def test_read_base_urls_strips_trailing_slash(self) -> None:
        urls = read_base_urls(
            {"defillama_base_urls": {"core": "https://api.llama.fi/", "coins": "https://x/"}}
        )
        self.assertEqual(urls, {"core": "https://api.llama.fi", "coins": "https://x"})


if __name__ == "__main__":
    unittest.main()
