"""Integration tests for the backup heartbeat migrations (B-138).

Verifies the live shape after ``alembic upgrade head``: the table exists, a
well-formed heartbeat row (the exact INSERT ``backup_postgres.sh`` issues)
lands, and the two CHECK constraints the monitor relies on actually bite —
``status`` is constrained and ``offsite_status`` only accepts the three values
``backup-staleness-check.yml`` reasons about (plus NULL for pre-off-site
history).

Each test uses :meth:`PostgresHarness.connection` so any rows it writes roll
back at the end of the block — no per-test cleanup needed.
"""

from __future__ import annotations

import unittest

from tests._postgres import get_harness, postgres_required

# The literal INSERT backup_postgres.sh runs (psql ``:'…'`` params become %s
# here). Kept verbatim so a schema change that breaks the script's write is
# caught by this test rather than only at 04:00 UTC on the Beelink.
_HEARTBEAT_INSERT = (
    "INSERT INTO meta.backup_runs "
    "(started_at, finished_at, status, dump_file, dump_bytes, "
    " duration_seconds, offsite_status, host) "
    "VALUES (to_timestamp(%s), to_timestamp(%s), 'ok', %s, %s, %s, %s, %s)"
)

_BLOB_HEARTBEAT_INSERT = (
    "INSERT INTO meta.backup_blob_runs "
    "(started_at, finished_at, status, blob_table, remote, archive_file, "
    " archive_bytes, duration_seconds, host) "
    "VALUES (to_timestamp(%s), to_timestamp(%s), 'ok', %s, %s, %s, %s, %s, %s)"
)


@postgres_required
class BackupRunsSchemaIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.harness = get_harness()

    def test_backup_runs_table_exists(self) -> None:
        with self.harness.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'meta' AND table_name = 'backup_runs' "
                "ORDER BY column_name"
            )
            columns = [row[0] for row in cur.fetchall()]
        self.assertEqual(
            columns,
            [
                "backup_id",
                "created_at",
                "dump_bytes",
                "dump_file",
                "duration_seconds",
                "finished_at",
                "host",
                "offsite_status",
                "started_at",
                "status",
            ],
        )

    def test_heartbeat_insert_matches_the_script(self) -> None:
        with self.harness.connection() as conn, conn.cursor() as cur:
            cur.execute(
                _HEARTBEAT_INSERT,
                [
                    1_700_000_000,
                    1_700_000_073,
                    "genkei_capital_x.pgcustom",
                    511_000_000,
                    73,
                    "uploaded",
                    "beelink",
                ],
            )
            cur.execute(
                "SELECT status, offsite_status, dump_bytes "
                "FROM meta.backup_runs ORDER BY finished_at DESC LIMIT 1"
            )
            status, offsite, dump_bytes = cur.fetchone()
        self.assertEqual((status, offsite, dump_bytes), ("ok", "uploaded", 511_000_000))

    def test_offsite_status_check_rejects_unknown_value(self) -> None:
        with self.harness.connection() as conn, conn.cursor() as cur:
            with self.assertRaises(Exception) as ctx:
                cur.execute(
                    _HEARTBEAT_INSERT,
                    [
                        1_700_000_000,
                        1_700_000_073,
                        "genkei_capital_x.pgcustom",
                        511_000_000,
                        73,
                        "bogus",
                        "beelink",
                    ],
                )
            self.assertIn("check", str(ctx.exception).lower())

    def test_offsite_status_allows_null_for_pre_offsite_history(self) -> None:
        with self.harness.connection() as conn, conn.cursor() as cur:
            cur.execute(
                _HEARTBEAT_INSERT,
                [
                    1_700_000_000,
                    1_700_000_073,
                    "genkei_capital_x.pgcustom",
                    511_000_000,
                    73,
                    None,
                    "beelink",
                ],
            )
            cur.execute(
                "SELECT offsite_status FROM meta.backup_runs ORDER BY finished_at DESC LIMIT 1"
            )
            self.assertIsNone(cur.fetchone()[0])

    def test_split_backup_core_tables_do_not_reference_excluded_raw_blobs(self) -> None:
        """The nightly core dump excludes raw_blob rows, so core tables must not FK to them."""
        with self.harness.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT conrelid::regclass::text
                FROM pg_constraint
                WHERE contype = 'f'
                  AND confrelid = 'meta.raw_blobs'::regclass
                  AND conrelid <> 'meta.raw_blobs'::regclass
                ORDER BY 1
                """
            )
            referencing_tables = [row[0] for row in cur.fetchall()]

        self.assertEqual(referencing_tables, [])

    def test_backup_blob_runs_table_exists(self) -> None:
        with self.harness.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'meta' AND table_name = 'backup_blob_runs' "
                "ORDER BY column_name"
            )
            columns = [row[0] for row in cur.fetchall()]
        self.assertEqual(
            columns,
            [
                "archive_bytes",
                "archive_file",
                "blob_backup_id",
                "blob_table",
                "created_at",
                "duration_seconds",
                "finished_at",
                "host",
                "remote",
                "started_at",
                "status",
            ],
        )

    def test_blob_heartbeat_insert_matches_the_script(self) -> None:
        with self.harness.connection() as conn, conn.cursor() as cur:
            cur.execute(
                _BLOB_HEARTBEAT_INSERT,
                [
                    1_700_000_000,
                    1_700_054_000,
                    "meta.raw_blobs",
                    "r2:genkei-backups/blobs",
                    "raw_blobs_20260805T050000Z.pgcustom",
                    20_700_000_000,
                    54_000,
                    "beelink",
                ],
            )
            cur.execute(
                "SELECT status, blob_table, archive_bytes "
                "FROM meta.backup_blob_runs ORDER BY finished_at DESC LIMIT 1"
            )
            status, blob_table, archive_bytes = cur.fetchone()
        self.assertEqual(
            (status, blob_table, archive_bytes),
            ("ok", "meta.raw_blobs", 20_700_000_000),
        )

    def test_blob_status_check_rejects_unknown_value(self) -> None:
        with self.harness.connection() as conn, conn.cursor() as cur:
            with self.assertRaises(Exception) as ctx:
                cur.execute(
                    "INSERT INTO meta.backup_blob_runs "
                    "(started_at, finished_at, status, blob_table, remote, archive_file, "
                    " archive_bytes, duration_seconds, host) "
                    "VALUES "
                    "(to_timestamp(%s), to_timestamp(%s), %s, %s, %s, %s, %s, %s, %s)",
                    [
                        1_700_000_000,
                        1_700_054_000,
                        "failed",
                        "meta.raw_blobs",
                        "r2:genkei-backups/blobs",
                        "raw_blobs_20260805T050000Z.pgcustom",
                        20_700_000_000,
                        54_000,
                        "beelink",
                    ],
                )
            self.assertIn("check", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
