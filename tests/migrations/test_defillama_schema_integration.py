"""Integration tests for the DeFiLlama schema migrations (B-016 + B-024).

Verifies the live shape of the database after ``alembic upgrade head``:
the four tables exist, the three time-series tables are TimescaleDB
hypertables, primary keys and uniqueness constraints behave, and the
``ingest_run_id`` foreign key back to ``meta.ingest_runs`` is enforced.

Each test uses :meth:`PostgresHarness.connection` so any rows it writes
roll back at the end of the block — no per-test cleanup needed.
"""

from __future__ import annotations

import unittest

from tests._postgres import get_harness, postgres_required


@postgres_required
class DefillamaSchemaIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.harness = get_harness()

    def test_four_tables_exist(self) -> None:
        with self.harness.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'defillama' ORDER BY table_name"
            )
            tables = [row[0] for row in cur.fetchall()]
        self.assertEqual(tables, ["chain_tvl", "prices", "protocols", "stablecoins"])

    def test_time_series_tables_are_hypertables(self) -> None:
        with self.harness.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT hypertable_name FROM timescaledb_information.hypertables "
                "WHERE hypertable_schema = 'defillama' ORDER BY hypertable_name"
            )
            hypertables = [row[0] for row in cur.fetchall()]
        self.assertEqual(hypertables, ["chain_tvl", "prices", "stablecoins"])

    def test_protocols_slug_is_unique(self) -> None:
        with self.harness.connection() as conn, conn.cursor() as cur:
            run_id = self._insert_ingest_run(cur, "defillama")
            cur.execute(
                "INSERT INTO defillama.protocols "
                "(slug, name, source_endpoint, ingest_run_id) "
                "VALUES (%s, %s, %s, %s)",
                ["aave-v3", "Aave V3", "https://api.llama.fi/protocols", run_id],
            )
            with self.assertRaises(Exception) as ctx:
                cur.execute(
                    "INSERT INTO defillama.protocols "
                    "(slug, name, source_endpoint, ingest_run_id) "
                    "VALUES (%s, %s, %s, %s)",
                    ["aave-v3", "Aave V3 dup", "https://api.llama.fi/protocols", run_id],
                )
            self.assertIn("duplicate", str(ctx.exception).lower())

    def test_chain_tvl_pk_is_composite_chain_ts(self) -> None:
        with self.harness.connection() as conn, conn.cursor() as cur:
            run_id = self._insert_ingest_run(cur, "defillama")
            cur.execute(
                "INSERT INTO defillama.chain_tvl "
                "(chain, ts, tvl_usd, source_endpoint, ingest_run_id) "
                "VALUES (%s, now(), %s, %s, %s)",
                ["Ethereum", 1234.5, "https://api.llama.fi/v2/historicalChainTvl/Ethereum", run_id],
            )
            cur.execute(
                "INSERT INTO defillama.chain_tvl "
                "(chain, ts, tvl_usd, source_endpoint, ingest_run_id) "
                "VALUES (%s, now() - interval '1 day', %s, %s, %s)",
                ["Ethereum", 1200.0, "https://api.llama.fi/v2/historicalChainTvl/Ethereum", run_id],
            )
            cur.execute("SELECT count(*) FROM defillama.chain_tvl WHERE chain = 'Ethereum'")
            self.assertEqual(cur.fetchone()[0], 2)

    def test_ingest_run_id_fk_is_enforced(self) -> None:
        with self.harness.connection() as conn, conn.cursor() as cur:
            with self.assertRaises(Exception) as ctx:
                cur.execute(
                    "INSERT INTO defillama.protocols "
                    "(slug, name, source_endpoint, ingest_run_id) "
                    "VALUES (%s, %s, %s, %s)",
                    ["orphan", "Orphan", "x", 9_999_999_999],
                )
            self.assertIn("foreign key", str(ctx.exception).lower())

    def test_protocols_chains_array_round_trips(self) -> None:
        with self.harness.connection() as conn, conn.cursor() as cur:
            run_id = self._insert_ingest_run(cur, "defillama")
            cur.execute(
                "INSERT INTO defillama.protocols "
                "(slug, name, chains, source_endpoint, ingest_run_id) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING chains",
                ["uniswap-v3", "Uniswap V3", ["Ethereum", "Arbitrum", "Optimism"], "x", run_id],
            )
            chains = cur.fetchone()[0]
        self.assertEqual(chains, ["Ethereum", "Arbitrum", "Optimism"])

    def test_price_requires_value(self) -> None:
        with self.harness.connection() as conn, conn.cursor() as cur:
            run_id = self._insert_ingest_run(cur, "defillama")
            with self.assertRaises(Exception) as ctx:
                cur.execute(
                    "INSERT INTO defillama.prices "
                    "(asset_key, ts, price_usd, source_endpoint, ingest_run_id) "
                    "VALUES (%s, now(), NULL, %s, %s)",
                    ["coingecko:bitcoin", "x", run_id],
                )
            self.assertIn("not-null", str(ctx.exception).lower())

    @staticmethod
    def _insert_ingest_run(cur, source: str) -> int:
        cur.execute(
            "INSERT INTO meta.ingest_runs (source, status) VALUES (%s, 'running') RETURNING id",
            [source],
        )
        return cur.fetchone()[0]


if __name__ == "__main__":
    unittest.main()
