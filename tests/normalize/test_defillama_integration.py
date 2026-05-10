"""End-to-end test for genkei.normalize.defillama against real Postgres."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from genkei.common import db
from genkei.normalize import defillama as normalizer
from tests._postgres import get_harness, postgres_required

CONFIG = {"chain_focus": ["Ethereum"]}
FETCHED_AT = datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc)

PROTOCOLS_PAYLOAD = [
    {
        "id": 11,
        "slug": "aave-v3",
        "name": "Aave V3",
        "category": "Lending",
        "chains": ["Ethereum", "Arbitrum"],
    },
    {
        "id": 12,
        "slug": "uniswap-v3",
        "name": "Uniswap V3",
        "category": "DEX",
        "chains": ["Ethereum"],
    },
]
CHAIN_HISTORY_PAYLOAD = [
    {"date": 1_700_000_000, "tvl": 49_000_000_000.0},
    {"date": 1_700_086_400, "tvl": 50_000_000_000.0},
]
STABLECOINS_PAYLOAD = {
    "peggedAssets": [
        {
            "id": "1",
            "name": "Tether",
            "symbol": "USDT",
            "pegType": "peggedUSD",
            "chainBalances": {
                "Ethereum": {"current": {"peggedUSD": 60_000_000_000}},
            },
        }
    ]
}
PRICES_PAYLOAD = {
    "coins": {
        "coingecko:bitcoin": {
            "price": 64_000.0,
            "symbol": "BTC",
            "decimals": 8,
        }
    }
}


@postgres_required
class NormalizerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.harness = get_harness()

    def setUp(self) -> None:
        from psycopg_pool import ConnectionPool

        db.reset_pool()
        self._pool = ConnectionPool(conninfo=self.harness.url, min_size=1, max_size=2, open=True)
        db.set_pool(self._pool)

    def tearDown(self) -> None:
        db.reset_pool()
        self._pool.close()
        self.harness.truncate_all()

    def _seed_collector_run(self) -> int:
        """Insert a fake collector ingest_run + matching raw_blobs."""
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO meta.ingest_runs (source, endpoint, status, finished_at) "
                "VALUES ('defillama', 'collect', 'success', now()) RETURNING id"
            )
            run_id = cur.fetchone()[0]
            for endpoint, url, payload in [
                ("protocols", "https://api.llama.fi/protocols", PROTOCOLS_PAYLOAD),
                (
                    "chain_tvl_history_ethereum",
                    "https://api.llama.fi/v2/historicalChainTvl/Ethereum",
                    CHAIN_HISTORY_PAYLOAD,
                ),
                ("stablecoins", "https://stablecoins.llama.fi/stablecoins", STABLECOINS_PAYLOAD),
                (
                    "prices_current",
                    "https://coins.llama.fi/prices/current/coingecko:bitcoin",
                    PRICES_PAYLOAD,
                ),
            ]:
                cur.execute(
                    "INSERT INTO meta.raw_blobs "
                    "(ingest_run_id, endpoint_name, url, payload, fetched_at) "
                    "VALUES (%s, %s, %s, %s::jsonb, %s)",
                    [run_id, endpoint, url, json.dumps(payload), FETCHED_AT],
                )
        return run_id

    def _run_normalizer(self, source_run_id: int) -> int:
        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(json.dumps(CONFIG))
            return normalizer.normalize(config_path, source_run_id=source_run_id)

    def test_full_run_writes_to_all_four_tables(self) -> None:
        source_run_id = self._seed_collector_run()
        normalizer_run_id = self._run_normalizer(source_run_id)

        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT status, metadata FROM meta.ingest_runs WHERE id = %s",
                [normalizer_run_id],
            )
            status, metadata = cur.fetchone()
            cur.execute("SELECT count(*) FROM defillama.protocols")
            protocols_count = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM defillama.chain_tvl WHERE chain = 'Ethereum'")
            chain_count = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM defillama.stablecoins WHERE symbol = 'USDT'")
            stable_count = cur.fetchone()[0]
            cur.execute(
                "SELECT count(*) FROM defillama.prices WHERE asset_key = 'coingecko:bitcoin'"
            )
            price_count = cur.fetchone()[0]
            cur.execute("SELECT ts, fetched_at FROM defillama.stablecoins WHERE symbol = 'USDT'")
            stable_ts, stable_fetched_at = cur.fetchone()
            cur.execute(
                "SELECT ts, fetched_at FROM defillama.prices WHERE asset_key = 'coingecko:bitcoin'"
            )
            price_ts, price_fetched_at = cur.fetchone()

        self.assertEqual(status, "success")
        self.assertEqual(metadata["source_run_id"], source_run_id)
        self.assertEqual(protocols_count, 2)
        self.assertEqual(chain_count, 2)
        self.assertEqual(stable_count, 1)
        self.assertEqual(price_count, 1)
        self.assertEqual(stable_ts, FETCHED_AT)
        self.assertEqual(stable_fetched_at, FETCHED_AT)
        self.assertEqual(price_ts, FETCHED_AT)
        self.assertEqual(price_fetched_at, FETCHED_AT)

    def test_rerun_is_idempotent(self) -> None:
        source_run_id = self._seed_collector_run()
        first_run = self._run_normalizer(source_run_id)
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT first_seen_at FROM defillama.protocols WHERE slug = 'aave-v3'")
            first_seen_initial = cur.fetchone()[0]

        second_run = self._run_normalizer(source_run_id)

        with db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM defillama.protocols")
            protocols_count = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM defillama.stablecoins")
            stablecoins_count = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM defillama.prices")
            prices_count = cur.fetchone()[0]
            cur.execute(
                "SELECT first_seen_at, last_updated_at, ingest_run_id FROM defillama.protocols "
                "WHERE slug = 'aave-v3'"
            )
            first_seen_after, last_updated_after, ingest_run_after = cur.fetchone()

        self.assertNotEqual(first_run, second_run)
        self.assertEqual(protocols_count, 2)  # no new rows on re-run
        self.assertEqual(stablecoins_count, 1)
        self.assertEqual(prices_count, 1)
        self.assertEqual(first_seen_after, first_seen_initial)  # never overwritten
        self.assertEqual(ingest_run_after, second_run)  # provenance updated to latest run
        self.assertEqual(last_updated_after, FETCHED_AT)


if __name__ == "__main__":
    unittest.main()
