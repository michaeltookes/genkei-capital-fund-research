"""End-to-end tests for genkei.common.db against a real Postgres (B-024).

Mocked unit tests in test_db.py cover branching logic and edge cases; these
tests pin down behaviour at the SQL boundary — that ``connection()`` really
commits/rolls back, that ``bulk_upsert`` actually upserts, and that
``ingest_run`` writes the audit lifecycle the way the schema expects.

Tests that exercise the real helpers cannot rely on the harness's
auto-rollback connection (the helpers commit on their own). We swap in a
fresh psycopg pool pointed at the harness URL and call
``harness.truncate_all()`` in tearDown.
"""

from __future__ import annotations

import unittest

from genkei.common import db
from tests._postgres import PostgresTestCase


class DbHelperIntegrationTests(PostgresTestCase):
    def test_connection_commits_on_clean_exit(self) -> None:
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO meta.ingest_runs (source, status) VALUES ('test', 'running')")

        with db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM meta.ingest_runs WHERE source = 'test'")
            self.assertEqual(cur.fetchone()[0], 1)

    def test_connection_rolls_back_on_exception(self) -> None:
        with self.assertRaises(RuntimeError), db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO meta.ingest_runs (source, status) VALUES ('rollback', 'running')"
            )
            raise RuntimeError("boom")

        with db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM meta.ingest_runs WHERE source = 'rollback'")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_bulk_upsert_inserts_then_updates_on_conflict(self) -> None:
        with db.ingest_run("defillama", endpoint="protocols") as run:
            with db.connection() as conn:
                first = [
                    {
                        "slug": "aave-v3",
                        "name": "Aave V3",
                        "category": "Lending",
                        "source_endpoint": "x",
                        "ingest_run_id": run.id,
                    },
                    {
                        "slug": "uniswap-v3",
                        "name": "Uniswap V3",
                        "category": "DEX",
                        "source_endpoint": "x",
                        "ingest_run_id": run.id,
                    },
                ]
                affected = db.bulk_upsert(
                    conn, "defillama.protocols", first, conflict_keys=["slug"]
                )
                self.assertEqual(affected, 2)

            with db.connection() as conn:
                second = [
                    {
                        "slug": "aave-v3",
                        "name": "Aave V3",
                        "category": "CDP",  # changed
                        "source_endpoint": "x",
                        "ingest_run_id": run.id,
                    }
                ]
                db.bulk_upsert(conn, "defillama.protocols", second, conflict_keys=["slug"])

            run.add_rows(2)

        with db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT slug, category FROM defillama.protocols ORDER BY slug")
            rows = cur.fetchall()
        self.assertEqual(rows, [("aave-v3", "CDP"), ("uniswap-v3", "DEX")])

    def test_ingest_run_records_running_then_success(self) -> None:
        with db.ingest_run("defillama", endpoint="chains", metadata={"chains": 3}) as run:
            run.add_rows(3)
            handle_id = run.id

        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT source, endpoint, status, rows_written, metadata "
                "FROM meta.ingest_runs WHERE id = %s",
                [handle_id],
            )
            source, endpoint, status, rows_written, metadata = cur.fetchone()
        self.assertEqual(source, "defillama")
        self.assertEqual(endpoint, "chains")
        self.assertEqual(status, "success")
        self.assertEqual(rows_written, 3)
        self.assertEqual(metadata, {"chains": 3})

    def test_ingest_run_records_failed_on_exception(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "api timeout"), db.ingest_run("fred") as run:
            run.add_rows(1)
            raise RuntimeError("api timeout")

        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT status, rows_written, error FROM meta.ingest_runs WHERE source = 'fred'"
            )
            status, rows_written, error = cur.fetchone()
        self.assertEqual(status, "failed")
        self.assertEqual(rows_written, 1)
        self.assertEqual(error, "api timeout")


if __name__ == "__main__":
    unittest.main()
