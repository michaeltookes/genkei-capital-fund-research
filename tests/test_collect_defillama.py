"""Unit tests for DeFiLlama collection guardrails."""

from __future__ import annotations

import unittest

from scripts.collect_defillama import build_collection_targets, fetch_json


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

    def test_fetch_json_rejects_unsupported_url_scheme(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Unsupported URL scheme"):
            fetch_json("file:///tmp/snapshot.json")


if __name__ == "__main__":
    unittest.main()
