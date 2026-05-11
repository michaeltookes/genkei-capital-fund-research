"""Unit tests for the CoinGecko collector helpers (offline)."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from genkei.ingest.coingecko import (
    API_KEY_ENV,
    DEMO_MARKET_CHART_DAYS,
    DEMO_RATE_LIMIT,
    CoinTarget,
    build_coin_url,
    build_market_chart_url,
    collect,
    load_coins,
    resolve_api_key,
)


class LoadCoinsTests(unittest.TestCase):
    def test_reads_primary_and_secondary_tiers(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(
                "crypto:\n"
                "  primary:\n"
                "    - symbol: BTC\n"
                "      name: Bitcoin\n"
                "      coingecko_id: bitcoin\n"
                "  secondary:\n"
                "    - symbol: PYTH\n"
                "      name: Pyth Network\n"
                "      coingecko_id: pyth-network\n",
                encoding="utf-8",
            )
            coins = load_coins(path)
        self.assertEqual(len(coins), 2)
        self.assertEqual(coins[0], CoinTarget("bitcoin", "BTC", "Bitcoin"))
        self.assertEqual(coins[1].coingecko_id, "pyth-network")

    def test_dedupes_by_coingecko_id(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(
                "crypto:\n"
                "  primary:\n"
                "    - symbol: BTC\n"
                "      name: Bitcoin\n"
                "      coingecko_id: bitcoin\n"
                "  secondary:\n"
                "    - symbol: BTC2\n"
                "      name: Bitcoin (dup)\n"
                "      coingecko_id: bitcoin\n",
                encoding="utf-8",
            )
            coins = load_coins(path)
        self.assertEqual(len(coins), 1)
        self.assertEqual(coins[0].symbol, "BTC")  # first-seen wins

    def test_skips_entries_without_coingecko_id(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(
                "crypto:\n"
                "  primary:\n"
                "    - symbol: NOID\n"
                "      name: No ID Coin\n"
                "    - symbol: BTC\n"
                "      name: Bitcoin\n"
                "      coingecko_id: bitcoin\n",
                encoding="utf-8",
            )
            coins = load_coins(path)
        self.assertEqual([c.symbol for c in coins], ["BTC"])

    def test_rejects_when_no_crypto_has_coingecko_id(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(
                "crypto:\n  primary:\n    - symbol: NOID\n      name: x\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SystemExit, "No crypto entries with coingecko_id"):
                load_coins(path)

    def test_rejects_missing_file(self) -> None:
        with self.assertRaisesRegex(SystemExit, "Watchlist file not found"):
            load_coins(Path("/no/such/path.yml"))


class UrlBuilderTests(unittest.TestCase):
    def test_coin_url_suppresses_optional_payloads(self) -> None:
        url = build_coin_url("bitcoin")
        self.assertIn("/coins/bitcoin", url)
        self.assertIn("market_data=true", url)
        # We don't store these; suppress to keep raw_blobs small.
        self.assertIn("community_data=false", url)
        self.assertIn("developer_data=false", url)
        self.assertIn("tickers=false", url)
        self.assertIn("sparkline=false", url)

    def test_market_chart_uses_daily_resolution_demo_window(self) -> None:
        url = build_market_chart_url("bitcoin")
        self.assertIn("/coins/bitcoin/market_chart", url)
        self.assertIn(f"days={DEMO_MARKET_CHART_DAYS}", url)
        self.assertIn("interval=daily", url)
        self.assertIn("vs_currency=usd", url)


class ResolveApiKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = os.environ.pop(API_KEY_ENV, None)

    def tearDown(self) -> None:
        if self._saved is not None:
            os.environ[API_KEY_ENV] = self._saved
        else:
            os.environ.pop(API_KEY_ENV, None)

    def test_returns_env_value_when_set(self) -> None:
        os.environ[API_KEY_ENV] = "demo-abc123"
        self.assertEqual(resolve_api_key(), "demo-abc123")

    def test_rejects_when_unset(self) -> None:
        with self.assertRaisesRegex(SystemExit, API_KEY_ENV):
            resolve_api_key()

    def test_rejects_when_empty(self) -> None:
        os.environ[API_KEY_ENV] = ""
        with self.assertRaisesRegex(SystemExit, API_KEY_ENV):
            resolve_api_key()

    def test_collect_rejects_missing_api_key_before_ingest_run(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(
                "crypto:\n"
                "  primary:\n"
                "    - symbol: BTC\n"
                "      name: Bitcoin\n"
                "      coingecko_id: bitcoin\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SystemExit, API_KEY_ENV):
                collect(path)


class RateLimitDefaultsTests(unittest.TestCase):
    def test_demo_under_25_per_min(self) -> None:
        self.assertEqual(DEMO_RATE_LIMIT.requests, 25)
        self.assertEqual(DEMO_RATE_LIMIT.window_seconds, 60.0)


if __name__ == "__main__":
    unittest.main()
