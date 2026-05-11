"""Unit tests for the CoinGecko collector helpers (offline)."""

from __future__ import annotations

import os
import sys
import unittest
from contextlib import redirect_stderr
from datetime import date
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from genkei.common.http import HttpClient
from genkei.ingest.coingecko import (
    API_KEY_ENV,
    API_TIER_ENV,
    DEMO_MARKET_CHART_DAYS,
    DEMO_RATE_LIMIT,
    CoinTarget,
    build_coin_url,
    build_market_chart_url,
    build_market_chart_range_url,
    collect,
    fetch_historical_market_chart,
    iter_date_ranges,
    load_coins,
    main,
    merge_market_chart_payloads,
    parse_args,
    resolve_api_key,
    resolve_api_tier,
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

    def test_market_chart_range_uses_iso_dates(self) -> None:
        url = build_market_chart_range_url(
            "bitcoin", since=date(2020, 1, 1), until=date(2020, 12, 31)
        )
        self.assertIn("pro-api.coingecko.com", url)
        self.assertIn("/coins/bitcoin/market_chart/range", url)
        self.assertIn("from=2020-01-01", url)
        self.assertIn("to=2020-12-31", url)
        self.assertIn("interval=daily", url)


class ResolveApiKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = os.environ.pop(API_KEY_ENV, None)
        self._saved_tier = os.environ.pop(API_TIER_ENV, None)

    def tearDown(self) -> None:
        if self._saved is not None:
            os.environ[API_KEY_ENV] = self._saved
        else:
            os.environ.pop(API_KEY_ENV, None)
        if self._saved_tier is not None:
            os.environ[API_TIER_ENV] = self._saved_tier
        else:
            os.environ.pop(API_TIER_ENV, None)

    def test_returns_env_value_when_set(self) -> None:
        os.environ[API_KEY_ENV] = "demo-abc123"
        self.assertEqual(resolve_api_key(), "demo-abc123")

    def test_trims_env_value_when_set(self) -> None:
        os.environ[API_KEY_ENV] = "  demo-abc123  "
        self.assertEqual(resolve_api_key(), "demo-abc123")

    def test_rejects_when_unset(self) -> None:
        with self.assertRaisesRegex(SystemExit, API_KEY_ENV):
            resolve_api_key()

    def test_rejects_when_empty(self) -> None:
        os.environ[API_KEY_ENV] = ""
        with self.assertRaisesRegex(SystemExit, API_KEY_ENV):
            resolve_api_key()

    def test_rejects_when_whitespace_only(self) -> None:
        os.environ[API_KEY_ENV] = "   "
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

    def test_collect_rejects_whitespace_api_key_before_ingest_run(self) -> None:
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
                collect(path, api_key="   ")

    def test_api_tier_defaults_to_demo(self) -> None:
        self.assertEqual(resolve_api_tier(), "demo")

    def test_api_tier_reads_env_value(self) -> None:
        os.environ[API_TIER_ENV] = "pro"
        self.assertEqual(resolve_api_tier(), "pro")

    def test_api_tier_rejects_unknown_value(self) -> None:
        os.environ[API_TIER_ENV] = "enterprise"
        with self.assertRaisesRegex(SystemExit, API_TIER_ENV):
            resolve_api_tier()

    def test_backfill_rejects_demo_tier_before_ingest_run(self) -> None:
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
            with self.assertRaisesRegex(SystemExit, "requires COINGECKO_API_TIER=pro"):
                collect(path, api_key="demo-key", backfill=True, since=date(2020, 1, 1))


class BackfillHelperTests(unittest.TestCase):
    def test_iter_date_ranges_chunks_inclusive_windows(self) -> None:
        ranges = iter_date_ranges(date(2020, 1, 1), date(2020, 1, 5), chunk_days=2)
        self.assertEqual(
            ranges,
            [
                (date(2020, 1, 1), date(2020, 1, 2)),
                (date(2020, 1, 3), date(2020, 1, 4)),
                (date(2020, 1, 5), date(2020, 1, 5)),
            ],
        )

    def test_merge_market_chart_payloads_sorts_and_dedupes(self) -> None:
        payload = merge_market_chart_payloads(
            [
                {"prices": [[2, 20], [1, 10]], "market_caps": [[1, 100]], "total_volumes": []},
                {
                    "prices": [[2, 22], [3, 30]],
                    "market_caps": [[2, 200]],
                    "total_volumes": [[3, 3000]],
                },
            ]
        )
        self.assertEqual(payload["prices"], [[1, 10], [2, 22], [3, 30]])
        self.assertEqual(payload["market_caps"], [[1, 100], [2, 200]])
        self.assertEqual(payload["total_volumes"], [[3, 3000]])

    def test_fetch_historical_market_chart_uses_range_chunks(self) -> None:
        requests: list[str] = []

        def route(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            if request.url.params["from"] == "2020-01-01":
                return httpx.Response(
                    200,
                    json={
                        "prices": [[1, 10]],
                        "market_caps": [[1, 100]],
                        "total_volumes": [[1, 1000]],
                    },
                )
            return httpx.Response(
                200,
                json={"prices": [[2, 20]], "market_caps": [[2, 200]], "total_volumes": [[2, 2000]]},
            )

        transport = httpx.MockTransport(route)
        with HttpClient("coingecko-test", transport=transport) as http:
            payload = fetch_historical_market_chart(
                CoinTarget("bitcoin", "BTC", "Bitcoin"),
                http,
                headers={"x-cg-pro-api-key": "pro-test-key"},
                since=date(2020, 1, 1),
                until=date(2020, 1, 2),
                chunk_days=1,
            )

        self.assertEqual(len(requests), 2)
        self.assertIn("/market_chart/range", requests[0])
        self.assertEqual(payload["prices"], [[1, 10], [2, 20]])


class ParseArgsTests(unittest.TestCase):
    def test_backfill_parses_since_date(self) -> None:
        args = parse_args(["--backfill", "--since", "2020-01-01"])
        self.assertTrue(args.backfill)
        self.assertEqual(args.since, date(2020, 1, 1))

    def test_since_requires_backfill(self) -> None:
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            parse_args(["--since", "2020-01-01"])

    def test_backfill_requires_since(self) -> None:
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            parse_args(["--backfill"])

    def test_main_honors_explicit_empty_argv(self) -> None:
        saved_argv = sys.argv
        saved_key = os.environ.pop(API_KEY_ENV, None)
        try:
            sys.argv = ["coingecko", "--backfill"]
            with self.assertRaisesRegex(SystemExit, API_KEY_ENV):
                main([])
        finally:
            sys.argv = saved_argv
            if saved_key is not None:
                os.environ[API_KEY_ENV] = saved_key
            else:
                os.environ.pop(API_KEY_ENV, None)


class RateLimitDefaultsTests(unittest.TestCase):
    def test_demo_under_25_per_min(self) -> None:
        self.assertEqual(DEMO_RATE_LIMIT.requests, 25)
        self.assertEqual(DEMO_RATE_LIMIT.window_seconds, 60.0)


if __name__ == "__main__":
    unittest.main()
