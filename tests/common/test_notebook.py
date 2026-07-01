"""Unit tests for the notebook / experiment helpers (B-054 + B-055)."""

from __future__ import annotations

import json
import random
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from genkei.common import notebook


class _FakeCursor:
    """A minimal psycopg-cursor stand-in: records execute() args and replays
    a configured (description, rows) pair."""

    def __init__(self, description: list[tuple[str]] | None, rows: list[tuple[Any, ...]]):
        self._description = description
        self._rows = rows
        self.executed: list[tuple[str, Any]] = []

    @property
    def description(self) -> list[tuple[str]] | None:
        return self._description

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _FakeConn:
    """A connection whose cursor() yields a pre-seeded _FakeCursor."""

    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor

    def cursor(self) -> _FakeCursor:
        return self._cursor


def _cols(*names: str) -> list[tuple[str]]:
    """Build a psycopg-style description (only the name in slot 0 matters)."""
    return [(n,) for n in names]


# ---------------------------------------------------------------------------
# read_sql_rows — dict shaping + Decimal coercion
# ---------------------------------------------------------------------------


class ReadSqlRowsTests(unittest.TestCase):
    """The pandas-free query path returns column-keyed dicts."""

    def test_zips_columns_and_coerces_decimals(self) -> None:
        """Rows become dicts keyed by column; Decimals become floats."""
        cur = _FakeCursor(_cols("ticker", "nav"), [("BITB", Decimal("32.71"))])
        rows = notebook.read_sql_rows("select ...", conn=_FakeConn(cur))
        self.assertEqual(rows, [{"ticker": "BITB", "nav": 32.71}])
        self.assertIsInstance(rows[0]["nav"], float)

    def test_passes_sql_and_params_through(self) -> None:
        """The SQL + params reach the cursor unchanged."""
        cur = _FakeCursor(_cols("x"), [(1,)])
        notebook.read_sql_rows("select %s", [7], conn=_FakeConn(cur))
        self.assertEqual(cur.executed, [("select %s", [7])])

    def test_no_description_returns_empty(self) -> None:
        """A non-SELECT (description None) yields an empty list, not a crash."""
        cur = _FakeCursor(None, [])
        self.assertEqual(notebook.read_sql_rows("do nothing", conn=_FakeConn(cur)), [])

    def test_one_shot_borrows_a_pooled_connection(self) -> None:
        """Without conn=, the helper borrows/returns via db.connection()."""
        cur = _FakeCursor(_cols("a"), [(1,)])

        @contextmanager
        def fake_connection() -> Any:
            yield _FakeConn(cur)

        with patch("genkei.common.notebook.db.connection", fake_connection):
            rows = notebook.read_sql_rows("select a")
        self.assertEqual(rows, [{"a": 1}])


# ---------------------------------------------------------------------------
# snapshot_manifest — reproducibility pin
# ---------------------------------------------------------------------------


class SnapshotManifestTests(unittest.TestCase):
    """The snapshot pin captures latest usable run id per (source, endpoint)."""

    def test_isoformats_timestamps_and_shapes_rows(self) -> None:
        cur = _FakeCursor(
            _cols("source", "endpoint", "ingest_run_id", "status", "started_at",
                  "finished_at", "rows_written"),
            [("bitwise", "collect", 1101, "success",
              datetime(2026, 6, 30, 13, 30, tzinfo=timezone.utc),
              datetime(2026, 6, 30, 13, 31, tzinfo=timezone.utc), 1)],
        )
        rows = notebook.snapshot_manifest(conn=_FakeConn(cur))
        self.assertEqual(rows[0]["source"], "bitwise")
        self.assertEqual(rows[0]["ingest_run_id"], 1101)
        self.assertEqual(rows[0]["started_at"], "2026-06-30T13:30:00+00:00")
        self.assertEqual(rows[0]["finished_at"], "2026-06-30T13:31:00+00:00")

    def test_sources_filter_adds_param(self) -> None:
        """Passing sources= narrows the query with a second array param."""
        cur = _FakeCursor(_cols("source"), [])
        notebook.snapshot_manifest(sources=["bitwise", "ishares"], conn=_FakeConn(cur))
        sql, params = cur.executed[0]
        self.assertIn("source = ANY", sql)
        self.assertEqual(params[-1], ["bitwise", "ishares"])

    def test_only_usable_states_queried(self) -> None:
        """The status filter pins to success/partial (never running/failed)."""
        cur = _FakeCursor(_cols("source"), [])
        notebook.snapshot_manifest(conn=_FakeConn(cur))
        _sql, params = cur.executed[0]
        self.assertEqual(params[0], ["success", "partial"])


# ---------------------------------------------------------------------------
# set_seeds — determinism
# ---------------------------------------------------------------------------


class SetSeedsTests(unittest.TestCase):
    def test_seeds_random_deterministically(self) -> None:
        """Same seed → same random draw; the seed is returned."""
        returned = notebook.set_seeds(20260701)
        self.assertEqual(returned, 20260701)
        first = [random.random() for _ in range(3)]
        notebook.set_seeds(20260701)
        second = [random.random() for _ in range(3)]
        self.assertEqual(first, second)


# ---------------------------------------------------------------------------
# build_manifest / write_manifest
# ---------------------------------------------------------------------------


class ManifestTests(unittest.TestCase):
    def _fake_conn(self) -> _FakeConn:
        return _FakeConn(_FakeCursor(_cols("source", "endpoint", "ingest_run_id",
                                            "status", "started_at", "finished_at",
                                            "rows_written"), []))

    def test_build_manifest_bundles_seed_config_and_snapshot(self) -> None:
        stamp = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
        manifest = notebook.build_manifest(
            seed=7, config={"window": 30}, conn=self._fake_conn(), captured_at=stamp
        )
        self.assertEqual(manifest["seed"], 7)
        self.assertEqual(manifest["config"], {"window": 30})
        self.assertEqual(manifest["captured_at"], "2026-07-01T12:00:00+00:00")
        self.assertEqual(manifest["snapshot_runs"], [])

    def test_write_manifest_roundtrips_json(self) -> None:
        stamp = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            returned = notebook.write_manifest(
                path, seed=1, config={"a": 1}, conn=self._fake_conn(), captured_at=stamp
            )
            on_disk = json.loads(path.read_text())
        self.assertEqual(on_disk, returned)
        self.assertEqual(on_disk["seed"], 1)


class JsonDefaultTests(unittest.TestCase):
    def test_serializes_dates_and_decimals(self) -> None:
        self.assertEqual(notebook._json_default(date(2026, 7, 1)), "2026-07-01")
        self.assertEqual(notebook._json_default(Decimal("1.5")), "1.5")

    def test_rejects_unknown_type(self) -> None:
        with self.assertRaises(TypeError):
            notebook._json_default(object())


# ---------------------------------------------------------------------------
# new_experiment — scaffolding
# ---------------------------------------------------------------------------


class NewExperimentTests(unittest.TestCase):
    def _make_template(self, root: Path) -> Path:
        template = root / "_template"
        template.mkdir(parents=True)
        (template / "experiment.md").write_text("# Template\n", encoding="utf-8")
        return template

    def test_scaffolds_dated_folder_from_template(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_template(root)
            folder = notebook.new_experiment(
                "btc-eth-rs", on=date(2026, 7, 1), root=root
            )
            self.assertEqual(folder.name, "2026-07-01-btc-eth-rs")
            self.assertTrue((folder / "experiment.md").is_file())

    def test_refuses_to_overwrite_existing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_template(root)
            notebook.new_experiment("dup", on=date(2026, 7, 1), root=root)
            with self.assertRaises(FileExistsError):
                notebook.new_experiment("dup", on=date(2026, 7, 1), root=root)

    def test_missing_template_raises(self) -> None:
        with TemporaryDirectory() as tmp, self.assertRaises(FileNotFoundError):
            notebook.new_experiment("x", on=date(2026, 7, 1), root=Path(tmp))


# ---------------------------------------------------------------------------
# read_sql_df — pandas path (skipped when the extra isn't installed)
# ---------------------------------------------------------------------------


class ReadSqlDfTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            import pandas  # noqa: F401
        except ImportError:
            self.skipTest("pandas not installed (notebooks extra)")

    def test_returns_dataframe_with_rows(self) -> None:
        cur = _FakeCursor(_cols("ticker", "nav"), [("BITB", Decimal("32.71"))])
        df = notebook.read_sql_df("select ...", conn=_FakeConn(cur))
        self.assertEqual(list(df.columns), ["ticker", "nav"])
        self.assertEqual(df.iloc[0]["ticker"], "BITB")

    def test_empty_result_keeps_named_columns(self) -> None:
        """An empty result still carries its column names (no KeyError later)."""
        cur = _FakeCursor(_cols("ticker", "nav"), [])
        df = notebook.read_sql_df("select ...", conn=_FakeConn(cur))
        self.assertTrue(df.empty)
        self.assertEqual(list(df.columns), ["ticker", "nav"])


if __name__ == "__main__":
    unittest.main()
