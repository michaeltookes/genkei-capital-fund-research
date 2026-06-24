"""End-to-end test for the B-019 backfill collector + normalizer.

Mocks every DeFiLlama HTTP route and runs collect → normalize against the
testcontainers harness. Verifies blobs land with the right endpoint_name
prefixes, normalizer dispatch routes them to the right tables, and
re-running the backfill is resumable (no duplicate API calls).
"""

from __future__ import annotations

import json
import unittest
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from genkei.common import db
from genkei.common.http import HttpClient
from genkei.ingest import defillama as ingest
from genkei.normalize import defillama as normalizer
from tests._postgres import PostgresTestCase

CONFIG = {
    "defillama_base_urls": {
        "core": "https://api.llama.fi",
        "coins": "https://coins.llama.fi",
        "stablecoins": "https://stablecoins.llama.fi",
    },
    "target_assets": [{"coingecko_id": "bitcoin"}],
    "chain_focus": [],
    "collection_endpoints": [],
}


def _route(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    # Historical prices: /prices/historical/<ts>/<keys>
    if path.startswith("/prices/historical/"):
        # echo back a per-day price; uses ts component to vary the value
        return httpx.Response(
            200,
            json={
                "coins": {
                    "coingecko:bitcoin": {
                        "price": 50_000.0,
                        "timestamp": int(path.split("/")[3]),
                        "symbol": "BTC",
                        "decimals": 8,
                    }
                }
            },
        )
    if path == "/protocol/aave-v3":
        return httpx.Response(
            200,
            json={
                "id": "111",
                "name": "Aave V3",
                "slug": "aave-v3",
                "chainTvls": {
                    "Ethereum": {
                        "tvl": [
                            {"date": 1_700_000_000, "totalLiquidityUSD": 100.0},
                            {"date": 1_700_086_400, "totalLiquidityUSD": 110.0},
                        ]
                    }
                },
            },
        )
    if path == "/stablecoin/1":
        return httpx.Response(
            200,
            json={
                "id": "1",
                "name": "Tether",
                "symbol": "USDT",
                "pegType": "peggedUSD",
                "chainBalances": {
                    "Ethereum": {
                        "tokens": [
                            {"date": 1_700_000_000, "current": {"peggedUSD": 50_000_000_000}},
                            {"date": 1_700_086_400, "current": {"peggedUSD": 50_500_000_000}},
                        ]
                    }
                },
            },
        )
    return httpx.Response(404, text=f"unmocked: {request.url}")


def _seed_known_protocols_and_stablecoins() -> None:
    """Backfill iterates rows already in defillama.protocols/stablecoins.
    Seed one of each so the backfill has something to walk."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO meta.ingest_runs (source, endpoint, status) "
            "VALUES ('defillama', 'collect', 'success') RETURNING id"
        )
        seed_run_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO defillama.protocols (slug, name, source_endpoint, ingest_run_id) "
            "VALUES (%s, %s, %s, %s)",
            ["aave-v3", "Aave V3", "x", seed_run_id],
        )
        cur.execute(
            "INSERT INTO defillama.stablecoins "
            "(asset_id, chain, ts, symbol, supply_usd, source_endpoint, ingest_run_id) "
            "VALUES (%s, %s, now(), %s, %s, %s, %s)",
            ["1", "Ethereum", "USDT", 50_000_000_000, "x", seed_run_id],
        )


class BackfillIntegrationTests(PostgresTestCase):
    def _run_backfill(self, since: date, http: HttpClient) -> int:
        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(json.dumps(CONFIG))
            return ingest.backfill(config_path, since=since, http=http)

    def test_full_backfill_writes_to_protocol_tvl_prices_stablecoins(self) -> None:
        _seed_known_protocols_and_stablecoins()
        since = date.today() - timedelta(days=2)  # 3 daily price blobs
        transport = httpx.MockTransport(_route)

        with HttpClient("defillama-test", transport=transport) as http:
            backfill_run = self._run_backfill(since, http)

        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*), array_agg(DISTINCT split_part(endpoint_name, '_', 1)) "
                "FROM meta.raw_blobs WHERE ingest_run_id = %s",
                [backfill_run],
            )
            blob_count, prefixes = cur.fetchone()

        self.assertEqual(blob_count, 5)  # 3 prices + 1 protocol + 1 stablecoin
        self.assertEqual(set(prefixes), {"prices", "protocol", "stablecoin"})

        # Normalize the backfill run end-to-end
        normalizer_run = normalizer.normalize_backfill(source_run_id=backfill_run)

        with db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM defillama.protocol_tvl WHERE slug = 'aave-v3'")
            self.assertEqual(cur.fetchone()[0], 2)
            cur.execute(
                "SELECT count(*) FROM defillama.stablecoins "
                "WHERE asset_id = '1' AND ts < now() - interval '7 days'"
            )
            self.assertEqual(cur.fetchone()[0], 2)
            cur.execute(
                "SELECT count(*) FROM defillama.prices WHERE asset_key = 'coingecko:bitcoin'"
            )
            self.assertEqual(cur.fetchone()[0], 3)
            cur.execute(
                "SELECT status, metadata->>'source_run_id' FROM meta.ingest_runs WHERE id = %s",
                [normalizer_run],
            )
            status, source_run_str = cur.fetchone()
        self.assertEqual(status, "success")
        self.assertEqual(int(source_run_str), backfill_run)

    def test_resume_skips_already_fetched_urls(self) -> None:
        _seed_known_protocols_and_stablecoins()
        since = date.today() - timedelta(days=1)  # 2 daily price blobs

        # First pass: fetches everything.
        call_count = 0
        original_route = _route

        def counting_route(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return original_route(request)

        with HttpClient("defillama-test", transport=httpx.MockTransport(counting_route)) as http:
            self._run_backfill(since, http)
        first_pass_calls = call_count

        # Second pass: every blob is already in raw_blobs within RESUME_WINDOW.
        with HttpClient("defillama-test", transport=httpx.MockTransport(counting_route)) as http:
            second_run = self._run_backfill(since, http)
        second_pass_calls = call_count - first_pass_calls

        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM meta.raw_blobs WHERE ingest_run_id = %s",
                [second_run],
            )
            second_run_blob_count = cur.fetchone()[0]

        self.assertEqual(first_pass_calls, 4)  # 2 prices + 1 protocol + 1 stablecoin
        self.assertEqual(second_pass_calls, 0)  # all skipped via resume window
        self.assertEqual(second_run_blob_count, 4)  # copied forward for current-run normalize

        normalizer.normalize_backfill(source_run_id=second_run)


if __name__ == "__main__":
    unittest.main()
