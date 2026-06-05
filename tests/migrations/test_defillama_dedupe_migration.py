"""Offline checks for the DeFiLlama timestamp dedupe migration."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


def _load_migration_module() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "20260604_dedupe_defillama_ts.py"
    )
    spec = importlib.util.spec_from_file_location("dedupe_defillama_ts_migration", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load migration at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION = _load_migration_module()


class DefillamaDedupeMigrationTests(unittest.TestCase):
    def test_dedupe_queries_have_deterministic_tie_breaker(self) -> None:
        for sql in (
            MIGRATION._DEDUPE_STABLECOINS,
            MIGRATION._DEDUPE_PROTOCOL_TVL,
            MIGRATION._DEDUPE_PROTOCOL_FEES,
        ):
            self.assertIn("ORDER BY ingest_run_id DESC, ts DESC", sql)

    def test_protocol_fees_merge_preserves_non_null_values(self) -> None:
        sql = MIGRATION._MERGE_PROTOCOL_FEES

        self.assertIn("FILTER (WHERE fees_usd IS NOT NULL)", sql)
        self.assertIn("FILTER (WHERE revenue_usd IS NOT NULL)", sql)
        self.assertIn("COALESCE(t.fees_usd, m.fees_usd)", sql)
        self.assertIn("COALESCE(t.revenue_usd, m.revenue_usd)", sql)

    def test_protocol_fees_merge_runs_before_dedupe_delete(self) -> None:
        calls: list[str] = []

        class FakeOp:
            def execute(self, sql: str) -> None:
                calls.append(sql)

        with patch.object(MIGRATION, "op", FakeOp()):
            MIGRATION.upgrade()

        self.assertLess(
            calls.index(MIGRATION._MERGE_PROTOCOL_FEES),
            calls.index(MIGRATION._DEDUPE_PROTOCOL_FEES),
        )
