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

from psycopg import pq

from genkei.common import notebook


class _FakeCursor:
    """A minimal psycopg-cursor stand-in: records execute() args and replays
    a configured (description, rows) pair."""

    def __init__(
        self,
        description: list[tuple[str]] | None,
        rows: list[tuple[Any, ...]],
        failures: dict[str, Exception] | None = None,
    ):
        self._description = description
        self._rows = rows
        self._failures = failures or {}
        self.executed: list[tuple[str, Any]] = []

    @property
    def description(self) -> list[tuple[str]] | None:
        return self._description

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))
        failure = self._failures.get(sql)
        if failure is not None:
            raise failure

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
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class _FakePgConn:
    def __init__(self, transaction_status: pq.TransactionStatus):
        self.transaction_status = transaction_status


class _FakePsycopgConn(_FakeConn):
    """A fake psycopg connection with connection-level read_only support."""

    def __init__(
        self,
        cursor: _FakeCursor,
        *,
        read_only: bool | None = None,
        autocommit: bool = False,
        transaction_status: pq.TransactionStatus = pq.TransactionStatus.IDLE,
    ):
        super().__init__(cursor)
        self.pgconn = _FakePgConn(transaction_status)
        self.read_only = read_only
        self.autocommit = autocommit


class _FakePoolContext:
    def __init__(self, conn: _FakeConn):
        self._conn = conn
        self.exited = False

    def __enter__(self) -> _FakeConn:
        return self._conn

    def __exit__(self, *exc: object) -> None:
        self.exited = True


class _FakePool:
    def __init__(self, conn: _FakeConn):
        self.context = _FakePoolContext(conn)

    def connection(self) -> _FakePoolContext:
        return self.context


def _cols(*names: str) -> list[tuple[str]]:
    """Build a psycopg-style description (only the name in slot 0 matters)."""
    return [(n,) for n in names]


def _query_execs(cur: _FakeCursor) -> list[tuple[str, Any]]:
    """Return user-query executions, ignoring the read-only transaction guard."""
    return [
        executed
        for executed in cur.executed
        if executed != ("SET TRANSACTION READ ONLY", None)
    ]


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
        self.assertEqual(
            cur.executed,
            [("SET TRANSACTION READ ONLY", None), ("select %s", [7])],
        )

    def test_supplied_psycopg_connection_uses_transaction_local_guard(self) -> None:
        """Explicit psycopg connections do not leak read_only into the pool."""
        cur = _FakeCursor(_cols("x"), [(1,)])
        conn = _FakePsycopgConn(cur)

        notebook.read_sql_rows("select 1", conn=conn)
        notebook.read_sql_rows("select 2", conn=conn)

        self.assertIsNone(conn.read_only)
        self.assertEqual(conn.commits, 2)
        self.assertEqual(conn.rollbacks, 0)
        self.assertEqual(
            cur.executed,
            [
                ("SET TRANSACTION READ ONLY", None),
                ("select 1", None),
                ("SET TRANSACTION READ ONLY", None),
                ("select 2", None),
            ],
        )

    def test_supplied_psycopg_connection_restores_prior_read_only_default(self) -> None:
        """A pooled writer connection does not come back with read_only flipped."""
        cur = _FakeCursor(_cols("x"), [(1,)])
        conn = _FakePsycopgConn(cur, read_only=False)

        notebook.read_sql_rows("select 1", conn=conn)

        self.assertIs(conn.read_only, False)
        self.assertEqual(conn.commits, 1)
        self.assertEqual(
            cur.executed,
            [("SET TRANSACTION READ ONLY", None), ("select 1", None)],
        )

    def test_supplied_psycopg_connection_rolls_back_after_query_error(self) -> None:
        """A failed helper-owned read-only transaction is cleaned up."""
        cur = _FakeCursor(
            _cols("x"),
            [(1,)],
            failures={"select broken": RuntimeError("syntax error")},
        )
        conn = _FakePsycopgConn(cur, read_only=False)

        with self.assertRaisesRegex(RuntimeError, "syntax error"):
            notebook.read_sql_rows("select broken", conn=conn)

        self.assertIs(conn.read_only, False)
        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.rollbacks, 1)
        self.assertEqual(
            cur.executed,
            [("SET TRANSACTION READ ONLY", None), ("select broken", None)],
        )

    def test_supplied_psycopg_connection_rejects_active_writable_transaction(self) -> None:
        """Do not rollback caller-owned work to force read-only mode."""
        cur = _FakeCursor(_cols("x"), [(1,)])
        conn = _FakePsycopgConn(
            cur,
            read_only=None,
            transaction_status=pq.TransactionStatus.INTRANS,
        )

        with self.assertRaisesRegex(ValueError, "already in a transaction"):
            notebook.read_sql_rows("select 1", conn=conn)

        self.assertEqual(cur.executed, [])
        self.assertEqual(conn.rollbacks, 0)

    def test_supplied_psycopg_connection_rejects_autocommit(self) -> None:
        """Autocommit statements are not protected by SET TRANSACTION READ ONLY."""
        cur = _FakeCursor(_cols("x"), [(1,)])
        conn = _FakePsycopgConn(cur, autocommit=True)

        with self.assertRaisesRegex(ValueError, "Disable autocommit"):
            notebook.read_sql_rows("select 1", conn=conn)

        self.assertEqual(cur.executed, [])
        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.rollbacks, 0)

    def test_no_description_returns_empty(self) -> None:
        """A read query with no cursor description yields empty, not a crash."""
        cur = _FakeCursor(None, [])
        self.assertEqual(
            notebook.read_sql_rows("SELECT pg_notify('chan', 'msg')", conn=_FakeConn(cur)),
            [],
        )

    def test_rejects_write_sql_before_execute(self) -> None:
        """Notebook query helpers refuse obvious writes."""
        cur = _FakeCursor(_cols("x"), [])
        with self.assertRaisesRegex(ValueError, "expected SELECT or WITH"):
            notebook.read_sql_rows("UPDATE coinbase.candles SET close = close", conn=_FakeConn(cur))
        self.assertEqual(cur.executed, [])

    def test_rejects_select_into_before_execute(self) -> None:
        """Postgres SELECT INTO creates a table, so it is not notebook-read-only."""
        cur = _FakeCursor(_cols("x"), [])
        with self.assertRaisesRegex(ValueError, "prohibited token INTO"):
            notebook.read_sql_rows(
                "SELECT close INTO scratch_close FROM coinbase.candles",
                conn=_FakeConn(cur),
            )
        self.assertEqual(cur.executed, [])

    def test_rejects_writable_cte(self) -> None:
        """Writable CTEs are rejected even when the top-level statement is WITH."""
        cur = _FakeCursor(_cols("x"), [])
        sql = """
            WITH changed AS (
                DELETE FROM coinbase.candles RETURNING product
            )
            SELECT * FROM changed
        """
        with self.assertRaisesRegex(ValueError, "prohibited token DELETE"):
            notebook.read_sql_rows(sql, conn=_FakeConn(cur))
        self.assertEqual(cur.executed, [])

    def test_rejects_multi_statement_sql(self) -> None:
        cur = _FakeCursor(_cols("x"), [])
        with self.assertRaisesRegex(ValueError, "single read-only statement"):
            notebook.read_sql_rows("SELECT 1; SELECT 2", conn=_FakeConn(cur))
        self.assertEqual(cur.executed, [])

    def test_ignores_write_words_inside_literals_and_comments(self) -> None:
        """The validator does not flag keywords hidden in strings/comments."""
        cur = _FakeCursor(_cols("note"), [("UPDATE is text",)])
        rows = notebook.read_sql_rows(
            "SELECT 'UPDATE is text' AS note -- DELETE is a comment",
            conn=_FakeConn(cur),
        )
        self.assertEqual(rows, [{"note": "UPDATE is text"}])

    def test_rejects_duplicate_result_columns_before_dict_conversion(self) -> None:
        """Duplicate result names would otherwise overwrite values in row dicts."""
        cur = _FakeCursor(_cols("ingest_run_id", "ingest_run_id"), [(7, 8)])
        with self.assertRaisesRegex(ValueError, "duplicate column names.*ingest_run_id"):
            notebook.read_sql_rows("select ...", conn=_FakeConn(cur))
        self.assertEqual(
            cur.executed,
            [("SET TRANSACTION READ ONLY", None), ("select ...", None)],
        )

    def test_describe_columns_rejects_duplicate_names(self) -> None:
        """The empty-DataFrame describe path uses the same duplicate guard."""
        cur = _FakeCursor(_cols("ticker", "ticker"), [])
        with self.assertRaisesRegex(ValueError, "duplicate column names.*ticker"):
            notebook._describe_columns("select ...", None, conn=_FakeConn(cur))

    def test_one_shot_borrows_a_pooled_connection(self) -> None:
        """Without conn=, the helper borrows/returns via db.connection()."""
        cur = _FakeCursor(_cols("a"), [(1,)])

        @contextmanager
        def fake_connection() -> Any:
            yield _FakeConn(cur)

        with patch("genkei.common.notebook.db.connection", fake_connection):
            rows = notebook.read_sql_rows("select a")
        self.assertEqual(rows, [{"a": 1}])
        self.assertEqual(cur.executed[0], ("SET TRANSACTION READ ONLY", None))
        self.assertEqual(cur.executed[1], ("select a", None))

    def test_get_session_marks_connection_read_only(self) -> None:
        """Session connections enter read-only mode before notebook queries."""
        cur = _FakeCursor(_cols("a"), [(1,)])
        pool = _FakePool(_FakeConn(cur))
        with patch("genkei.common.notebook.db.get_pool", return_value=pool):
            session = notebook.get_session()
            rows = session.read_sql_rows("select a")
            session.close()
        self.assertEqual(rows, [{"a": 1}])
        self.assertEqual(cur.executed[0], ("SET TRANSACTION READ ONLY", None))
        self.assertEqual(cur.executed[1], ("select a", None))
        self.assertTrue(pool.context.exited)

    def test_session_recovers_read_only_transaction_after_query_error(self) -> None:
        """A failed cell does not leave a long-lived session transaction aborted."""
        cur = _FakeCursor(
            _cols("a"),
            [(1,)],
            failures={"select broken": RuntimeError("syntax error")},
        )
        conn = _FakeConn(cur)
        pool = _FakePool(conn)
        with patch("genkei.common.notebook.db.get_pool", return_value=pool):
            session = notebook.get_session()
            with self.assertRaisesRegex(RuntimeError, "syntax error"):
                session.read_sql_rows("select broken")
            rows = session.read_sql_rows("select a")
            session.close()
        self.assertEqual(rows, [{"a": 1}])
        self.assertEqual(conn.rollbacks, 1)
        self.assertEqual(
            cur.executed,
            [
                ("SET TRANSACTION READ ONLY", None),
                ("select broken", None),
                ("SET TRANSACTION READ ONLY", None),
                ("select a", None),
            ],
        )
        self.assertTrue(pool.context.exited)

    def test_session_snapshot_reuses_existing_read_only_transaction(self) -> None:
        """Session manifest reads do not reissue SET after earlier session queries."""
        cur = _FakeCursor(_cols("source"), [])
        pool = _FakePool(_FakeConn(cur))
        with patch("genkei.common.notebook.db.get_pool", return_value=pool):
            session = notebook.get_session()
            session.read_sql_rows("select a")
            session.snapshot_manifest()
            session.close()
        self.assertEqual(
            [
                executed
                for executed in cur.executed
                if executed == ("SET TRANSACTION READ ONLY", None)
            ],
            [("SET TRANSACTION READ ONLY", None)],
        )
        queries = _query_execs(cur)
        self.assertEqual(queries[0], ("select a", None))
        self.assertIn("SELECT DISTINCT ON", queries[1][0])
        self.assertTrue(pool.context.exited)

    def test_session_methods_raise_after_close(self) -> None:
        """A closed session must not keep using a returned pooled connection."""
        cur = _FakeCursor(_cols("a"), [(1,)])
        pool = _FakePool(_FakeConn(cur))
        with patch("genkei.common.notebook.db.get_pool", return_value=pool):
            session = notebook.get_session()
            session.close()
            with self.assertRaisesRegex(RuntimeError, "NotebookSession is closed"):
                session.read_sql_rows("select a")
            with self.assertRaisesRegex(RuntimeError, "NotebookSession is closed"):
                session.read_sql_df("select a")
            with self.assertRaisesRegex(RuntimeError, "NotebookSession is closed"):
                session.snapshot_manifest()

        self.assertEqual(cur.executed, [("SET TRANSACTION READ ONLY", None)])
        self.assertTrue(pool.context.exited)


# ---------------------------------------------------------------------------
# snapshot_manifest — reproducibility pin
# ---------------------------------------------------------------------------


class SnapshotManifestTests(unittest.TestCase):
    """The snapshot pin captures latest usable run id per (source, endpoint)."""

    def test_isoformats_timestamps_and_shapes_rows(self) -> None:
        cur = _FakeCursor(
            _cols("source", "endpoint", "ingest_run_id", "status", "started_at",
                  "finished_at", "rows_written", "metadata"),
            [("bitwise", "collect", 1101, "success",
              datetime(2026, 6, 30, 13, 30, tzinfo=timezone.utc),
              datetime(2026, 6, 30, 13, 31, tzinfo=timezone.utc), 1,
              {"source_run_id": 1100})],
        )
        rows = notebook.snapshot_manifest(conn=_FakeConn(cur))
        self.assertEqual(rows[0]["source"], "bitwise")
        self.assertEqual(rows[0]["ingest_run_id"], 1101)
        self.assertEqual(rows[0]["metadata"], {"source_run_id": 1100})
        self.assertEqual(rows[0]["started_at"], "2026-06-30T13:30:00+00:00")
        self.assertEqual(rows[0]["finished_at"], "2026-06-30T13:31:00+00:00")

    def test_exact_ingest_run_ids_query_specific_runs(self) -> None:
        cur = _FakeCursor(
            _cols("source", "endpoint", "ingest_run_id", "status", "started_at",
                  "finished_at", "rows_written", "metadata"),
            [("coinbase", "normalize", 42, "success",
              datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc),
              datetime(2026, 7, 1, 9, 1, tzinfo=timezone.utc), 3,
              {"source_run_id": 41})],
        )
        rows = notebook.snapshot_manifest(
            ingest_run_ids=[42, "42", Decimal("42")], conn=_FakeConn(cur)
        )
        sql, params = _query_execs(cur)[0]
        self.assertIn("id = ANY", sql)
        self.assertNotIn("DISTINCT ON", sql)
        self.assertEqual(params[0], [42])
        self.assertEqual(rows[0]["ingest_run_id"], 42)

    def test_empty_exact_ingest_run_ids_skip_query(self) -> None:
        cur = _FakeCursor(_cols("source"), [])
        rows = notebook.snapshot_manifest(ingest_run_ids=[], conn=_FakeConn(cur))
        self.assertEqual(rows, [])
        self.assertEqual(cur.executed, [])

    def test_exact_ingest_run_ids_raise_when_any_requested_run_is_missing(self) -> None:
        """Exact provenance snapshots must not silently drop requested run ids."""
        cur = _FakeCursor(
            _cols("source", "endpoint", "ingest_run_id", "status", "started_at",
                  "finished_at", "rows_written", "metadata"),
            [("coinbase", "normalize", 42, "success",
              datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc),
              datetime(2026, 7, 1, 9, 1, tzinfo=timezone.utc), 3,
              {"source_run_id": 41})],
        )
        with self.assertRaisesRegex(ValueError, "missing ingest_run_id.*99"):
            notebook.snapshot_manifest(
                sources=["coinbase"],
                ingest_run_ids=[42, 99],
                conn=_FakeConn(cur),
            )
        sql, params = _query_execs(cur)[0]
        self.assertIn("id = ANY", sql)
        self.assertIn("source = ANY", sql)
        self.assertEqual(params, [[42, 99], ["coinbase"]])

    def test_sources_filter_adds_param(self) -> None:
        """Passing sources= narrows the query with a second array param."""
        cur = _FakeCursor(_cols("source"), [])
        notebook.snapshot_manifest(sources=["bitwise", "ishares"], conn=_FakeConn(cur))
        sql, params = _query_execs(cur)[0]
        self.assertIn("source = ANY", sql)
        self.assertEqual(params[-1], ["bitwise", "ishares"])

    def test_only_usable_states_queried(self) -> None:
        """The status filter pins to success/partial (never running/failed)."""
        cur = _FakeCursor(_cols("source"), [])
        notebook.snapshot_manifest(conn=_FakeConn(cur))
        _sql, params = _query_execs(cur)[0]
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
                                            "rows_written", "metadata"), []))

    def test_build_manifest_bundles_seed_config_and_snapshot(self) -> None:
        stamp = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
        manifest = notebook.build_manifest(
            seed=7, config={"window": 30}, conn=self._fake_conn(), captured_at=stamp
        )
        self.assertEqual(manifest["seed"], 7)
        self.assertEqual(manifest["config"], {"window": 30})
        self.assertEqual(manifest["captured_at"], "2026-07-01T12:00:00+00:00")
        self.assertEqual(manifest["snapshot_runs"], [])

    def test_build_manifest_extracts_run_ids_from_data(self) -> None:
        stamp = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
        cur = _FakeCursor(_cols("source", "endpoint", "ingest_run_id",
                                "status", "started_at", "finished_at",
                                "rows_written", "metadata"),
                          [
                              ("coinbase", "normalize", 12, "success",
                               datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc),
                               datetime(2026, 7, 1, 9, 1, tzinfo=timezone.utc),
                               3, {}),
                              ("coinbase", "normalize", 8, "success",
                               datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc),
                               datetime(2026, 6, 1, 9, 1, tzinfo=timezone.utc),
                               3, {}),
                              ("coinbase", "normalize", 9, "success",
                               datetime(2026, 6, 2, 9, 0, tzinfo=timezone.utc),
                               datetime(2026, 6, 2, 9, 1, tzinfo=timezone.utc),
                               3, {}),
                          ])
        manifest = notebook.build_manifest(
            seed=7,
            data=[
                {"product": "BTC-USD", "latest_ingest_run_id": 12,
                 "prior_ingest_run_id": 8},
                {"product": "ETH-USD", "latest_ingest_run_id": 12,
                 "prior_ingest_run_id": "9"},
            ],
            conn=_FakeConn(cur),
            captured_at=stamp,
        )
        _sql, params = _query_execs(cur)[0]
        self.assertEqual(params[0], [12, 8, 9])
        self.assertEqual(
            [run["ingest_run_id"] for run in manifest["snapshot_runs"]],
            [12, 8, 9],
        )

    def test_build_manifest_rejects_data_without_provenance_columns(self) -> None:
        stamp = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "must include an ingest_run_id column"):
            notebook.build_manifest(
                seed=7,
                data=[{"product": "BTC-USD"}],
                conn=self._fake_conn(),
                captured_at=stamp,
            )

    def test_ingest_run_ids_from_data_handles_plural_columns(self) -> None:
        self.assertEqual(
            notebook.ingest_run_ids_from_data(
                [{"consumed_ingest_run_ids": [1, 2, 1], "product": "BTC-USD"}]
            ),
            [1, 2],
        )

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


class ExampleNotebookTests(unittest.TestCase):
    def test_crypto_core_query_anchors_prior_to_latest_candle(self) -> None:
        """The reference notebook must not let wall-clock time shift the lookback."""
        path = (
            Path(__file__).resolve().parents[2]
            / "notebooks"
            / "experiments"
            / "2026-07-01-crypto-core-trailing-returns"
            / "experiment.ipynb"
        )
        notebook_json = json.loads(path.read_text(encoding="utf-8"))
        source = "\n".join(
            "".join(cell.get("source", [])) for cell in notebook_json["cells"]
        )

        self.assertNotIn("now() - make_interval", source)
        self.assertIn("JOIN latest l USING (product)", source)
        self.assertIn("c.ts <= l.ts_now - make_interval(days => %s)", source)
        self.assertIn("session.read_sql_df(SQL, [ASSETS, WINDOW_DAYS])", source)


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

    def test_empty_result_keeps_named_columns_without_rerunning_query(self) -> None:
        """An empty result still carries its column names (no KeyError later)."""
        cur = _FakeCursor(_cols("ticker", "nav"), [])
        df = notebook.read_sql_df("select ...", conn=_FakeConn(cur))
        self.assertTrue(df.empty)
        self.assertEqual(list(df.columns), ["ticker", "nav"])
        self.assertEqual(
            cur.executed,
            [("SET TRANSACTION READ ONLY", None), ("select ...", None)],
        )


if __name__ == "__main__":
    unittest.main()
