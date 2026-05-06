"""Unit tests for DeFiLlama collection guardrails."""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.collect_defillama import build_collection_targets, build_run_id, collect_snapshots, fetch_json


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

    def test_collect_snapshots_records_partial_chain_history_failure(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.json"
            output_dir = root / "raw"
            config = {
                "defillama_base_urls": {
                    "core": "https://api.llama.fi",
                    "coins": "https://coins.llama.fi",
                },
                "target_assets": [{"coingecko_id": "bitcoin"}],
                "chain_focus": ["Rootstock RSK"],
                "collection_endpoints": [],
            }
            config_path.write_text(json.dumps(config), encoding="utf-8")

            def fake_fetch(url: str) -> object:
                if "historicalChainTvl" in url:
                    raise RuntimeError("HTTP 404 while fetching history")
                return {"ok": True}

            with (
                patch("scripts.collect_defillama.fetch_json", side_effect=fake_fetch),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                manifest_path = collect_snapshots(config_path, output_dir)

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entries = {entry["name"]: entry for entry in manifest["entries"]}
            history_entry = entries["chain_tvl_history_rootstock_rsk"]
            self.assertEqual("partial", history_entry["status"])
            placeholder = json.loads(Path(history_entry["path"]).read_text(encoding="utf-8"))
            self.assertTrue(placeholder["partial"])

    def test_fetch_json_rejects_unsupported_url_scheme(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Unsupported URL scheme"):
            fetch_json("file:///tmp/snapshot.json")


if __name__ == "__main__":
    unittest.main()
