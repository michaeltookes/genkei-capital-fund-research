"""Unit tests for the CoinGecko collector helpers (offline)."""

from __future__ import annotations

import os
import sys
import unittest
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stderr
from datetime import date
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from genkei.common.http import HttpClient
from genkei.ingest.coingecko import (
    API_KEY_ENV,
    API_TIER_ENV,
    DEMO_API_KEY_HEADER,
    DEMO_COINGECKO_BASE,
    DEMO_MARKET_CHART_DAYS,
    DEMO_RATE_LIMIT,
    KEYLESS_RATE_LIMIT,
    PRO_API_KEY_HEADER,
    PRO_COINGECKO_BASE,
    CoinTarget,
    api_base_url,
    api_key_headers,
    build_coin_url,
    build_market_chart_range_url,
    build_market_chart_url,
    collect,
    fetch_historical_market_chart,
    iter_date_ranges,
    load_coins,
    main,
    merge_market_chart_payloads,
    parse_args,
    resolve_api_key,
    resolve_api_tier,
    validate_api_key_tier,
)


class _FakeRun:
    id = 42

    def __init__(self) -> None:
        self.rows_written = 0

    def add_rows(self, n: int) -> None:
        self.rows_written += n


@contextmanager
def _fake_ingest_run(*_args: object, **_kwargs: object) -> Iterator[_FakeRun]:
    yield _FakeRun()


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

    def test_rejects_when_no_crypto_or_protocol_has_coingecko_id(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(
                "crypto:\n  primary:\n    - symbol: NOID\n      name: x\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SystemExit, "No watchlist entries with a coingecko_id"):
                load_coins(path)

    def test_rejects_missing_file(self) -> None:
        with self.assertRaisesRegex(SystemExit, "Watchlist file not found"):
            load_coins(Path("/no/such/path.yml"))

    # ---- B-091: union with protocols: section ----

    def test_includes_coingecko_ids_from_protocols_section(self) -> None:
        # A protocol entry with coingecko_id is fetched alongside crypto entries.
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(
                "crypto:\n"
                "  primary:\n"
                "    - symbol: BTC\n"
                "      name: Bitcoin\n"
                "      coingecko_id: bitcoin\n"
                "protocols:\n"
                "  primary:\n"
                "    - slug: aave-v3\n"
                "      name: Aave V3\n"
                "      category: Lending\n"
                "      coingecko_id: aave\n",
                encoding="utf-8",
            )
            coins = load_coins(path)
        ids = [c.coingecko_id for c in coins]
        self.assertEqual(ids, ["bitcoin", "aave"])  # crypto first, then protocols
        # Protocol-derived coin carries the protocol name; symbol is empty.
        aave = next(c for c in coins if c.coingecko_id == "aave")
        self.assertEqual(aave.symbol, "")
        self.assertEqual(aave.name, "Aave V3")

    def test_includes_crypto_price_targets_before_protocols(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(
                "crypto:\n"
                "  primary:\n"
                "    - symbol: BTC\n"
                "      name: Bitcoin\n"
                "      coingecko_id: bitcoin\n"
                "crypto_price_targets:\n"
                "  - symbol: LQTY\n"
                "    name: Liquity\n"
                "    coingecko_id: liquity\n"
                "protocols:\n"
                "  primary:\n"
                "    - slug: aave-v3\n"
                "      name: Aave V3\n"
                "      category: Lending\n"
                "      coingecko_id: aave\n",
                encoding="utf-8",
            )
            coins = load_coins(path)
        self.assertEqual(
            coins,
            [
                CoinTarget("bitcoin", "BTC", "Bitcoin"),
                CoinTarget("liquity", "LQTY", "Liquity", required=False),
                CoinTarget("aave", "", "Aave V3"),
            ],
        )

    def test_crypto_entries_win_over_crypto_price_targets(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(
                "crypto:\n"
                "  primary:\n"
                "    - symbol: BTC\n"
                "      name: Bitcoin\n"
                "      coingecko_id: bitcoin\n"
                "crypto_price_targets:\n"
                "  - symbol: BTCX\n"
                "    name: Bitcoin duplicate\n"
                "    coingecko_id: bitcoin\n",
                encoding="utf-8",
            )
            coins = load_coins(path)
        self.assertEqual(coins, [CoinTarget("bitcoin", "BTC", "Bitcoin")])

    def test_protocol_duplicate_keeps_coin_target_required(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(
                "crypto_price_targets:\n"
                "  - symbol: AAVE\n"
                "    name: Aave Token\n"
                "    coingecko_id: aave\n"
                "protocols:\n"
                "  primary:\n"
                "    - slug: aave-v3\n"
                "      name: Aave V3\n"
                "      category: Lending\n"
                "      coingecko_id: aave\n",
                encoding="utf-8",
            )
            coins = load_coins(path)
        self.assertEqual(coins, [CoinTarget("aave", "AAVE", "Aave Token")])

    def test_dedupes_coingecko_id_across_crypto_and_protocols(self) -> None:
        # chainlink appears in both crypto-core and as the token for two
        # chainlink-* protocols — must be fetched exactly once.
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(
                "crypto:\n"
                "  primary:\n"
                "    - symbol: LINK\n"
                "      name: Chainlink\n"
                "      coingecko_id: chainlink\n"
                "protocols:\n"
                "  primary:\n"
                "    - slug: chainlink-staking\n"
                "      name: Chainlink Staking\n"
                "      category: Oracle\n"
                "      coingecko_id: chainlink\n"
                "    - slug: chainlink-requests\n"
                "      name: Chainlink Requests\n"
                "      category: Oracle\n"
                "      coingecko_id: chainlink\n",
                encoding="utf-8",
            )
            coins = load_coins(path)
        self.assertEqual(len(coins), 1)
        # Crypto-side entry wins (preserves the symbol + name from crypto:).
        self.assertEqual(coins[0].symbol, "LINK")
        self.assertEqual(coins[0].name, "Chainlink")

    def test_protocols_without_coingecko_id_are_skipped(self) -> None:
        # Aftermath has no token in the watchlist; should not produce a coin.
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(
                "crypto:\n"
                "  primary:\n"
                "    - symbol: BTC\n"
                "      name: Bitcoin\n"
                "      coingecko_id: bitcoin\n"
                "protocols:\n"
                "  primary:\n"
                "    - slug: aftermath-amm\n"
                "      name: Aftermath AMM\n"
                "      category: DEX\n",
                encoding="utf-8",
            )
            coins = load_coins(path)
        self.assertEqual([c.coingecko_id for c in coins], ["bitcoin"])

    def test_protocols_only_watchlist_still_produces_coins(self) -> None:
        # If a watchlist somehow has no crypto: section but populated
        # protocols, the union still yields a usable coin list.
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(
                "protocols:\n"
                "  primary:\n"
                "    - slug: aave-v3\n"
                "      name: Aave V3\n"
                "      category: Lending\n"
                "      coingecko_id: aave\n",
                encoding="utf-8",
            )
            coins = load_coins(path)
        self.assertEqual([c.coingecko_id for c in coins], ["aave"])


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


class KeylessModeTests(unittest.TestCase):
    """Cover the keyless (no COINGECKO_API_KEY) request shape."""

    def test_api_key_headers_returns_empty_when_keyless(self) -> None:
        self.assertEqual(api_key_headers("demo", None), {})

    def test_api_key_headers_uses_demo_header_when_keyed(self) -> None:
        self.assertEqual(api_key_headers("demo", "abc"), {DEMO_API_KEY_HEADER: "abc"})

    def test_api_key_headers_uses_pro_header_when_pro(self) -> None:
        self.assertEqual(api_key_headers("pro", "xyz"), {PRO_API_KEY_HEADER: "xyz"})

    def test_keyless_uses_public_host(self) -> None:
        # Keyless and demo share the public host; pro flips to pro-api.
        self.assertEqual(api_base_url("demo"), DEMO_COINGECKO_BASE)
        self.assertEqual(api_base_url("pro"), PRO_COINGECKO_BASE)
        self.assertNotIn("pro-api", DEMO_COINGECKO_BASE)

    def test_keyless_rate_limit_is_tighter_than_demo(self) -> None:
        # Public/keyless gets a stricter cap than authenticated demo.
        # Same window (per_minute), fewer requests.
        self.assertEqual(KEYLESS_RATE_LIMIT.window_seconds, DEMO_RATE_LIMIT.window_seconds)
        self.assertLess(KEYLESS_RATE_LIMIT.requests, DEMO_RATE_LIMIT.requests)

    def test_validate_rejects_pro_tier_without_key(self) -> None:
        with self.assertRaisesRegex(SystemExit, "requires"):
            validate_api_key_tier("pro", backfill=False, api_key=None)

    def test_validate_rejects_backfill_without_key(self) -> None:
        with self.assertRaisesRegex(SystemExit, "requires COINGECKO_API_TIER=pro"):
            validate_api_key_tier("demo", backfill=True, api_key=None)

    def test_validate_allows_keyless_daily(self) -> None:
        # Keyless daily collect is the supported free-tier path; no raise.
        validate_api_key_tier("demo", backfill=False, api_key=None)


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

    def test_returns_none_when_unset(self) -> None:
        # Keyless is now a supported mode — public host, no auth header,
        # tighter rate limit. resolve_api_key() returns None instead of
        # raising so callers can fall through to keyless.
        self.assertIsNone(resolve_api_key())

    def test_returns_none_when_empty(self) -> None:
        os.environ[API_KEY_ENV] = ""
        self.assertIsNone(resolve_api_key())

    def test_returns_none_when_whitespace_only(self) -> None:
        os.environ[API_KEY_ENV] = "   "
        self.assertIsNone(resolve_api_key())

    def test_backfill_requires_api_key(self) -> None:
        # Backfill needs the Pro range endpoint; keyless can't reach it.
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
                collect(path, api_key=None, backfill=True, since=date(2020, 1, 1))

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


class CollectTests(unittest.TestCase):
    def test_optional_crypto_price_target_failure_does_not_fail_collect(self) -> None:
        requests: list[str] = []

        def route(request: httpx.Request) -> httpx.Response:
            requests.append(request.url.path)
            if request.url.path == "/api/v3/coins/bitcoin":
                return httpx.Response(200, json={"symbol": "btc", "name": "Bitcoin"})
            if request.url.path == "/api/v3/coins/bitcoin/market_chart":
                return httpx.Response(
                    200,
                    json={
                        "prices": [[1, 10]],
                        "market_caps": [[1, 100]],
                        "total_volumes": [[1, 1000]],
                    },
                )
            if request.url.path == "/api/v3/coins/liquity":
                return httpx.Response(404, text="delisted")
            return httpx.Response(404, text=f"unexpected: {request.url}")

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(
                "crypto:\n"
                "  primary:\n"
                "    - symbol: BTC\n"
                "      name: Bitcoin\n"
                "      coingecko_id: bitcoin\n"
                "crypto_price_targets:\n"
                "  - symbol: LQTY\n"
                "    name: Liquity\n"
                "    coingecko_id: liquity\n",
                encoding="utf-8",
            )
            transport = httpx.MockTransport(route)
            with (
                HttpClient("coingecko-test", transport=transport) as http,
                patch("genkei.ingest.coingecko.db.ingest_run", _fake_ingest_run),
                patch("genkei.ingest.coingecko.db.store_raw_blob") as store_blob,
                patch("genkei.ingest.coingecko.db.record_partial_endpoints") as partial,
            ):
                self.assertEqual(collect(path, http=http, api_key="demo-test-key"), 42)

        self.assertEqual(
            [call.args[1] for call in store_blob.call_args_list],
            ["coin_bitcoin", "market_chart_bitcoin"],
        )
        self.assertNotIn("/api/v3/coins/liquity/market_chart", requests)
        partial.assert_called_once()
        failure = partial.call_args.args[1][0]
        self.assertEqual(failure["name"], "coin_liquity")
        self.assertEqual(failure["required"], "false")

    def test_optional_invalid_coin_metadata_skips_market_chart(self) -> None:
        requests: list[str] = []

        def route(request: httpx.Request) -> httpx.Response:
            requests.append(request.url.path)
            if request.url.path == "/api/v3/coins/bitcoin":
                return httpx.Response(200, json={"symbol": "btc", "name": "Bitcoin"})
            if request.url.path == "/api/v3/coins/bitcoin/market_chart":
                return httpx.Response(
                    200,
                    json={
                        "prices": [[1, 10]],
                        "market_caps": [[1, 100]],
                        "total_volumes": [[1, 1000]],
                    },
                )
            if request.url.path == "/api/v3/coins/liquity":
                return httpx.Response(200, json={"name": "Liquity"})
            return httpx.Response(404, text=f"unexpected: {request.url}")

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(
                "crypto:\n"
                "  primary:\n"
                "    - symbol: BTC\n"
                "      name: Bitcoin\n"
                "      coingecko_id: bitcoin\n"
                "crypto_price_targets:\n"
                "  - symbol: LQTY\n"
                "    name: Liquity\n"
                "    coingecko_id: liquity\n",
                encoding="utf-8",
            )
            transport = httpx.MockTransport(route)
            with (
                HttpClient("coingecko-test", transport=transport) as http,
                patch("genkei.ingest.coingecko.db.ingest_run", _fake_ingest_run),
                patch("genkei.ingest.coingecko.db.store_raw_blob") as store_blob,
                patch("genkei.ingest.coingecko.db.record_partial_endpoints") as partial,
            ):
                self.assertEqual(collect(path, http=http, api_key="demo-test-key"), 42)

        self.assertEqual(
            [call.args[1] for call in store_blob.call_args_list],
            ["coin_bitcoin", "market_chart_bitcoin"],
        )
        self.assertNotIn("/api/v3/coins/liquity/market_chart", requests)
        partial.assert_called_once()
        failure = partial.call_args.args[1][0]
        self.assertEqual(failure["name"], "coin_liquity")
        self.assertEqual(failure["required"], "false")
        self.assertIn("missing nonempty symbol", failure["error"])

    def test_required_coin_failure_still_fails_collect(self) -> None:
        def route(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v3/coins/bitcoin":
                return httpx.Response(200, json={"symbol": "btc", "name": "Bitcoin"})
            if request.url.path == "/api/v3/coins/bitcoin/market_chart":
                return httpx.Response(404, text="endpoint drift")
            return httpx.Response(404, text=f"unexpected: {request.url}")

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
            transport = httpx.MockTransport(route)
            with (
                HttpClient("coingecko-test", transport=transport) as http,
                patch("genkei.ingest.coingecko.db.ingest_run", _fake_ingest_run),
                patch("genkei.ingest.coingecko.db.store_raw_blob") as store_blob,
                patch("genkei.ingest.coingecko.db.record_partial_endpoints") as partial,
                self.assertRaisesRegex(RuntimeError, "required fetch failed"),
            ):
                collect(path, http=http, api_key="demo-test-key")

        self.assertEqual([call.args[1] for call in store_blob.call_args_list], ["coin_bitcoin"])
        partial.assert_called_once()
        failure = partial.call_args.args[1][0]
        self.assertEqual(failure["name"], "market_chart_bitcoin")
        self.assertEqual(failure["required"], "true")

    def test_required_invalid_coin_metadata_fails_collect(self) -> None:
        requests: list[str] = []

        def route(request: httpx.Request) -> httpx.Response:
            requests.append(request.url.path)
            if request.url.path == "/api/v3/coins/bitcoin":
                return httpx.Response(200, json={"name": "Bitcoin"})
            if request.url.path == "/api/v3/coins/bitcoin/market_chart":
                return httpx.Response(
                    200,
                    json={
                        "prices": [[1, 10]],
                        "market_caps": [[1, 100]],
                        "total_volumes": [[1, 1000]],
                    },
                )
            return httpx.Response(404, text=f"unexpected: {request.url}")

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
            transport = httpx.MockTransport(route)
            with (
                HttpClient("coingecko-test", transport=transport) as http,
                patch("genkei.ingest.coingecko.db.ingest_run", _fake_ingest_run),
                patch("genkei.ingest.coingecko.db.store_raw_blob") as store_blob,
                patch("genkei.ingest.coingecko.db.record_partial_endpoints") as partial,
                self.assertRaisesRegex(RuntimeError, "required fetch failed"),
            ):
                collect(path, http=http, api_key="demo-test-key")

        store_blob.assert_not_called()
        self.assertNotIn("/api/v3/coins/bitcoin/market_chart", requests)
        partial.assert_called_once()
        failure = partial.call_args.args[1][0]
        self.assertEqual(failure["name"], "coin_bitcoin")
        self.assertEqual(failure["required"], "true")
        self.assertIn("missing nonempty symbol", failure["error"])


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
        # main([]) must run parse_args on [] (defaults), not fall back to
        # sys.argv. Verified by stuffing sys.argv with a flag combination
        # parse_args would reject ("--backfill" without "--since") and
        # confirming main([]) does NOT raise that SystemExit. We patch
        # collect to short-circuit before the DB call.
        from unittest.mock import patch as mock_patch

        saved_argv = sys.argv
        try:
            sys.argv = ["coingecko", "--backfill"]
            with mock_patch(
                "genkei.ingest.coingecko.collect", return_value=42
            ) as mocked:
                rc = main([])
            self.assertEqual(rc, 0)
            # Called with the parse_args([]) defaults — backfill False.
            self.assertEqual(mocked.call_args.kwargs.get("backfill"), False)
        finally:
            sys.argv = saved_argv


class RateLimitDefaultsTests(unittest.TestCase):
    def test_demo_under_25_per_min(self) -> None:
        self.assertEqual(DEMO_RATE_LIMIT.requests, 25)
        self.assertEqual(DEMO_RATE_LIMIT.window_seconds, 60.0)


if __name__ == "__main__":
    unittest.main()
