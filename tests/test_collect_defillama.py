"""Unit tests for DeFiLlama collection guardrails."""

from __future__ import annotations

import unittest

from scripts.collect_defillama import build_collection_targets, build_run_id, fetch_json


class CollectDefillamaTests(unittest.TestCase):
    """Verify collector validation that does not require network access."""

    def test_build_collection_targets_rejects_duplicate_names(self) -> None:
        config = {
            "defillama_base_urls": {
                "core": "https://api.llama.fi",
                "coins": "https://coins.llama.fi",
            },
            "target_assets": [{"coingecko_id": "bitcoin"}],
            "collection_endpoints": [
                {"name": "protocols", "base": "core", "path": "/protocols"},
                {"name": "protocols", "base": "core", "path": "/v2/chains"},
            ],
        }

        with self.assertRaisesRegex(SystemExit, "Duplicate collection endpoint name: protocols"):
            build_collection_targets(config)

    def test_build_collection_targets_requires_coins_base_url(self) -> None:
        config = {
            "defillama_base_urls": {
                "core": "https://api.llama.fi",
            },
            "target_assets": [{"coingecko_id": "bitcoin"}],
            "collection_endpoints": [],
        }

        with self.assertRaisesRegex(SystemExit, "defillama_base_urls.coins is missing"):
            build_collection_targets(config)

    def test_build_collection_targets_adds_chain_history_for_focus_chains(self) -> None:
        config = {
            "defillama_base_urls": {
                "core": "https://api.llama.fi",
                "coins": "https://coins.llama.fi",
            },
            "target_assets": [{"coingecko_id": "bitcoin"}],
            "chain_focus": ["Rootstock RSK"],
            "collection_endpoints": [],
        }

        targets = build_collection_targets(config)

        urls_by_name = {target.name: target.url for target in targets}

        self.assertIn("chain_tvl_history_rootstock_rsk", urls_by_name)
        self.assertIn("Rootstock%20RSK", urls_by_name["chain_tvl_history_rootstock_rsk"])

    def test_build_run_id_is_collision_resistant_for_same_second_runs(self) -> None:
        first = build_run_id("2026-05-05T18:00:00.123456+00:00")
        second = build_run_id("2026-05-05T18:00:00.123456+00:00")

        self.assertNotEqual(first, second)
        self.assertIn(".123456", first)

    def test_fetch_json_rejects_unsupported_url_scheme(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Unsupported URL scheme"):
            fetch_json("file:///tmp/snapshot.json")


if __name__ == "__main__":
    unittest.main()
