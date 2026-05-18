"""Unit tests for the DeFiLlama collector (offline)."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from genkei.ingest.defillama import (
    _load_watchlist_protocol_slugs,
    build_chain_history_targets,
    build_collection_targets,
    build_price_target,
    chain_history_target_name,
    main,
    read_base_urls,
    target_asset_coin_keys,
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

    def test_target_asset_coin_keys_require_every_asset_to_have_coingecko_id(self) -> None:
        with self.assertRaisesRegex(SystemExit, "Each target asset must include coingecko_id"):
            target_asset_coin_keys(
                {
                    "target_assets": [
                        {"coingecko_id": "bitcoin"},
                        {"symbol": "ETH"},
                    ]
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
            SystemExit, "Missing required collection endpoints: stablecoins"
        ):
            build_collection_targets(config)

    def test_build_collection_targets_marks_chains_optional(self) -> None:
        config = {
            "defillama_base_urls": {
                "core": "https://api.llama.fi",
                "coins": "https://coins.llama.fi",
                "stablecoins": "https://stablecoins.llama.fi",
            },
            "target_assets": [{"coingecko_id": "bitcoin"}],
            "chain_focus": [],
            "collection_endpoints": [
                {"name": "protocols", "base": "core", "path": "/protocols"},
                {"name": "stablecoins", "base": "stablecoins", "path": "/stablecoins"},
                {"name": "chains", "base": "core", "path": "/v2/chains"},
            ],
        }
        targets = build_collection_targets(config)
        by_name = {target.name: target for target in targets}
        self.assertFalse(by_name["chains"].required)
        self.assertTrue(by_name["protocols"].required)
        self.assertTrue(by_name["stablecoins"].required)

    def test_read_base_urls_strips_trailing_slash(self) -> None:
        urls = read_base_urls(
            {"defillama_base_urls": {"core": "https://api.llama.fi/", "coins": "https://x/"}}
        )
        self.assertEqual(urls, {"core": "https://api.llama.fi", "coins": "https://x"})

    def test_main_rejects_backfill_only_flags_for_daily_collection(self) -> None:
        with self.assertRaisesRegex(SystemExit, "--since/--endpoint only valid with --backfill"):
            main(["--since", "2026-05-01"])

        with self.assertRaisesRegex(SystemExit, "--since/--endpoint only valid with --backfill"):
            main(["--endpoint", "prices"])


class WatchlistProtocolSlugLoaderTests(unittest.TestCase):
    """B-081 — daily collect dispatches per-protocol fetches via this loader."""

    def _write_watchlist(self, body: str) -> Path:
        ctx = TemporaryDirectory()
        self.addCleanup(ctx.cleanup)
        path = Path(ctx.name) / "watchlists.yml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_returns_slugs_primary_first(self) -> None:
        path = self._write_watchlist(
            "protocols:\n"
            "  secondary:\n"
            "    - slug: balancer-v2\n      name: Balancer\n"
            "  primary:\n"
            "    - slug: aave-v3\n      name: Aave V3\n"
            "    - slug: chainlink\n      name: Chainlink\n"
        )
        slugs = _load_watchlist_protocol_slugs(path)
        # Primary slugs come first, in file order; then secondary.
        self.assertEqual(slugs, ["aave-v3", "chainlink", "balancer-v2"])

    def test_deduplicates_slugs(self) -> None:
        path = self._write_watchlist(
            "protocols:\n"
            "  primary:\n"
            "    - slug: aave-v3\n      name: Aave V3\n"
            "  secondary:\n"
            "    - slug: aave-v3\n      name: Aave V3 (dup)\n"
        )
        self.assertEqual(_load_watchlist_protocol_slugs(path), ["aave-v3"])

    def test_missing_protocols_section_returns_empty(self) -> None:
        path = self._write_watchlist(
            "crypto:\n  primary:\n    - symbol: BTC\n      name: BTC\n      coingecko_id: bitcoin\n"
        )
        self.assertEqual(_load_watchlist_protocol_slugs(path), [])

    def test_missing_file_returns_empty_not_raise(self) -> None:
        # collect() shouldn't fail just because the watchlist file
        # disappeared — the per-protocol step is best-effort.
        self.assertEqual(
            _load_watchlist_protocol_slugs(Path("/no/such/path.yml")), []
        )

    def test_malformed_yaml_returns_empty_not_raise(self) -> None:
        path = self._write_watchlist("protocols:\n  primary:\n    - slug: [\n")
        self.assertEqual(_load_watchlist_protocol_slugs(path), [])

    def test_skips_malformed_entries(self) -> None:
        path = self._write_watchlist(
            "protocols:\n"
            "  primary:\n"
            "    - slug: aave-v3\n"
            "    - name: missing-slug\n"  # drops
            "    - 'not a mapping'\n"  # drops
        )
        self.assertEqual(_load_watchlist_protocol_slugs(path), ["aave-v3"])


if __name__ == "__main__":
    unittest.main()
