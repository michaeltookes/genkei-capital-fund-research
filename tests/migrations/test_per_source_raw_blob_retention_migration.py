"""Offline checks for the per-source raw_blobs retention migration (B-140b)."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


def _load_migration_module() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "20261029_per_source_raw_blob_retention.py"
    )
    spec = importlib.util.spec_from_file_location("per_source_retention_migration", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load migration at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION = _load_migration_module()


class PerSourceRetentionMigrationTests(unittest.TestCase):
    def test_chains_after_market_rank_migration(self) -> None:
        self.assertEqual(MIGRATION.down_revision, "a9c4e17d3b65")

    def test_procedure_joins_ingest_runs_for_source(self) -> None:
        sql = MIGRATION._UPGRADE_PROCEDURE
        self.assertIn("LEFT JOIN meta.ingest_runs r ON r.id = b.ingest_run_id", sql)
        self.assertIn("config ->> r.source", sql)
        self.assertIn("config ->> 'default'", sql)
        self.assertIn("INTERVAL '90 days'", sql)

    def test_procedure_is_batched_with_commit(self) -> None:
        sql = MIGRATION._UPGRADE_PROCEDURE
        self.assertIn("batch_size CONSTANT int := 2000", sql)
        self.assertIn("ORDER BY b.id", sql)
        self.assertIn("LIMIT batch_size", sql)
        self.assertIn("EXIT WHEN deleted_in_batch = 0", sql)
        self.assertIn("COMMIT;", sql)
        self.assertIn("RAISE NOTICE", sql)
        self.assertNotIn("VACUUM", sql)

    def test_job_config_keys(self) -> None:
        config = json.loads(MIGRATION.JOB_CONFIG)
        self.assertEqual(config, {"gdelt": "30 days", "default": "90 days"})

    def test_upgrade_replaces_procedure_and_alters_job(self) -> None:
        with patch.object(MIGRATION.op, "execute") as execute:
            MIGRATION.upgrade()
        statements = [call.args[0] for call in execute.call_args_list]
        self.assertEqual(len(statements), 2)
        self.assertIn("CREATE OR REPLACE PROCEDURE meta.delete_old_raw_blobs", statements[0])
        self.assertIn("alter_job(jid, config =>", statements[1])
        self.assertIn(MIGRATION.JOB_CONFIG, statements[1])
        self.assertIn("proc_name = 'delete_old_raw_blobs'", statements[1])
        self.assertNotIn("add_job", statements[1])

    def test_downgrade_restores_flat_90_day_and_clears_config(self) -> None:
        with patch.object(MIGRATION.op, "execute") as execute:
            MIGRATION.downgrade()
        statements = [call.args[0] for call in execute.call_args_list]
        self.assertIn("INTERVAL '90 days'", statements[0])
        self.assertNotIn("ingest_runs", statements[0])
        self.assertIn("config => NULL::jsonb", statements[1])


if __name__ == "__main__":
    unittest.main()
