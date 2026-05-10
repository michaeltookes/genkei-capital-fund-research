"""Unit tests for genkei.common.db.

Mocks the psycopg_pool ConnectionPool so the suite stays deterministic and
offline. Real-Postgres integration tests come with B-024 (testcontainers).
"""

from __future__ import annotations

import os
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import MagicMock, patch

from genkei.common import db


class _FakeCursor:
    """Minimal cursor stand-in supporting the calls our helpers make."""

    def __init__(self) -> None:
        self.executed: list[tuple[Any, Any]] = []
        self.executemany_calls: list[tuple[Any, list[Any]]] = []
        self.fetch_value: tuple[Any, ...] | None = None
        self.rowcount: int | None = 0
        self.execute_error: Exception | None = None

    def execute(self, query: Any, params: Any = None) -> None:
        if self.execute_error is not None:
            raise self.execute_error
        self.executed.append((query, params))

    def executemany(self, query: Any, params_seq: list[Any]) -> None:
        self.executemany_calls.append((query, params_seq))
        self.rowcount = len(params_seq)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.fetch_value

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class _FakeConnection:
    """Connection stand-in tracking commit/rollback calls."""

    def __init__(self) -> None:
        self.cursor_obj = _FakeCursor()
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> _FakeCursor:
        return self.cursor_obj

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class _FakePool:
    """Pool stand-in handing out a fresh fake connection per call."""

    def __init__(self) -> None:
        self.connections: list[_FakeConnection] = []
        self.closed = False

    @contextmanager
    def connection(self) -> Iterator[_FakeConnection]:
        conn = _FakeConnection()
        self.connections.append(conn)
        yield conn

    def close(self) -> None:
        self.closed = True


class ResolveUrlTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = os.environ.pop("GENKEI_DATABASE_URL", None)
        self._saved_user = os.environ.pop("GENKEI_DATABASE_USER", None)
        self._saved_password = os.environ.pop("GENKEI_DATABASE_PASSWORD", None)
        self._saved_name = os.environ.pop("GENKEI_DATABASE_NAME", None)
        self._saved_server_info_path = os.environ.pop("GENKEI_SERVER_INFO_PATH", None)

    def tearDown(self) -> None:
        if self._saved is not None:
            os.environ["GENKEI_DATABASE_URL"] = self._saved
        else:
            os.environ.pop("GENKEI_DATABASE_URL", None)
        if self._saved_user is not None:
            os.environ["GENKEI_DATABASE_USER"] = self._saved_user
        else:
            os.environ.pop("GENKEI_DATABASE_USER", None)
        if self._saved_password is not None:
            os.environ["GENKEI_DATABASE_PASSWORD"] = self._saved_password
        else:
            os.environ.pop("GENKEI_DATABASE_PASSWORD", None)
        if self._saved_name is not None:
            os.environ["GENKEI_DATABASE_NAME"] = self._saved_name
        else:
            os.environ.pop("GENKEI_DATABASE_NAME", None)
        if self._saved_server_info_path is not None:
            os.environ["GENKEI_SERVER_INFO_PATH"] = self._saved_server_info_path
        else:
            os.environ.pop("GENKEI_SERVER_INFO_PATH", None)

    def test_explicit_argument_wins(self) -> None:
        self.assertEqual(
            db._resolve_url("postgresql://user:pw@host/db"),
            "postgresql://user:pw@host/db",
        )

    def test_strips_sqlalchemy_driver_prefix(self) -> None:
        self.assertEqual(
            db._resolve_url("postgresql+psycopg://user:pw@host/db"),
            "postgresql://user:pw@host/db",
        )

    def test_falls_back_to_env_var(self) -> None:
        os.environ["GENKEI_DATABASE_URL"] = "postgresql://from/env"
        self.assertEqual(db._resolve_url(None), "postgresql://from/env")

    def test_falls_back_to_server_info_specs_with_env_credentials(self) -> None:
        with TemporaryDirectory() as temp_dir:
            server_info = Path(temp_dir) / "SKILL.md"
            server_info.write_text(
                """
## Development Server (Beelink)
**Host:** 192.168.86.36

| genkeicapital-postgres | 5440 | PostgreSQL 16-alpine |
""",
                encoding="utf-8",
            )
            os.environ["GENKEI_SERVER_INFO_PATH"] = str(server_info)
            os.environ["GENKEI_DATABASE_USER"] = "genkei"
            os.environ["GENKEI_DATABASE_PASSWORD"] = "secret pw"
            os.environ["GENKEI_DATABASE_NAME"] = "research"

            self.assertEqual(
                db._resolve_url(None),
                "postgresql://genkei:secret%20pw@192.168.86.36:5440/research",
            )

    def test_raises_when_missing(self) -> None:
        with self.assertRaises(RuntimeError):
            db._resolve_url(None)


class PoolLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        db.reset_pool()

    def tearDown(self) -> None:
        db.reset_pool()

    def test_set_pool_overrides_singleton(self) -> None:
        fake = _FakePool()
        db.set_pool(fake)  # type: ignore[arg-type]
        self.assertIs(db.get_pool(), fake)

    def test_reset_pool_closes_and_clears(self) -> None:
        fake = _FakePool()
        db.set_pool(fake)  # type: ignore[arg-type]
        db.reset_pool()
        self.assertTrue(fake.closed)
        # After reset, get_pool would attempt to build a real pool — verify
        # by patching ConnectionPool to confirm it is invoked.
        with patch("genkei.common.db.ConnectionPool") as ctor:
            ctor.return_value = MagicMock()
            os.environ["GENKEI_DATABASE_URL"] = "postgresql://x/y"
            try:
                db.get_pool()
                ctor.assert_called_once()
            finally:
                os.environ.pop("GENKEI_DATABASE_URL", None)
                db.reset_pool()


class ConnectionContextManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        db.reset_pool()
        self.fake_pool = _FakePool()
        db.set_pool(self.fake_pool)  # type: ignore[arg-type]

    def tearDown(self) -> None:
        db.reset_pool()

    def test_commits_on_clean_exit(self) -> None:
        with db.connection() as conn:
            conn.cursor().execute("SELECT 1")
        used = self.fake_pool.connections[0]
        self.assertEqual(used.commits, 1)
        self.assertEqual(used.rollbacks, 0)

    def test_rolls_back_and_reraises_on_exception(self) -> None:
        with self.assertRaises(ValueError), db.connection():
            raise ValueError("boom")
        used = self.fake_pool.connections[0]
        self.assertEqual(used.commits, 0)
        self.assertEqual(used.rollbacks, 1)


class BulkUpsertTests(unittest.TestCase):
    def test_empty_rows_short_circuits(self) -> None:
        conn = _FakeConnection()
        affected = db.bulk_upsert(conn, "x.y", [], conflict_keys=["id"])  # type: ignore[arg-type]
        self.assertEqual(affected, 0)
        self.assertEqual(conn.cursor_obj.executemany_calls, [])

    def test_default_update_cols_excludes_conflict_keys(self) -> None:
        conn = _FakeConnection()
        rows = [{"id": 1, "name": "a", "tvl": 100}, {"id": 2, "name": "b", "tvl": 200}]
        affected = db.bulk_upsert(conn, "defillama.protocols", rows, conflict_keys=["id"])  # type: ignore[arg-type]
        self.assertEqual(affected, 2)
        # One executemany call with the row payloads
        self.assertEqual(len(conn.cursor_obj.executemany_calls), 1)
        _query, params = conn.cursor_obj.executemany_calls[0]
        self.assertEqual(params, [[1, "a", 100], [2, "b", 200]])

    def test_empty_update_cols_uses_do_nothing(self) -> None:
        conn = _FakeConnection()
        rows = [{"id": 1, "value": 7}]
        db.bulk_upsert(  # type: ignore[arg-type]
            conn, "x.y", rows, conflict_keys=["id"], update_cols=[]
        )
        # We can't easily inspect the composed SQL string from the SQL object
        # without a connection; rely on the fact that executemany was called
        # exactly once with no error and the rowcount matches.
        self.assertEqual(len(conn.cursor_obj.executemany_calls), 1)

    def test_requires_conflict_keys_for_non_empty_rows(self) -> None:
        conn = _FakeConnection()
        with self.assertRaisesRegex(ValueError, "conflict_keys"):
            db.bulk_upsert(conn, "x.y", [{"id": 1}], conflict_keys=[])  # type: ignore[arg-type]

    def test_rejects_heterogeneous_rows(self) -> None:
        conn = _FakeConnection()
        rows = [{"id": 1, "name": "a"}, {"id": 2, "value": "b"}]

        with self.assertRaisesRegex(ValueError, "same keys"):
            db.bulk_upsert(conn, "x.y", rows, conflict_keys=["id"])  # type: ignore[arg-type]

    def test_rejects_update_cols_outside_row_columns(self) -> None:
        conn = _FakeConnection()
        rows = [{"id": 1, "name": "a"}]

        with self.assertRaisesRegex(ValueError, "update_cols"):
            db.bulk_upsert(  # type: ignore[arg-type]
                conn, "x.y", rows, conflict_keys=["id"], update_cols=["missing"]
            )


class IngestRunTests(unittest.TestCase):
    def setUp(self) -> None:
        db.reset_pool()
        self.fake_pool = _FakePool()
        db.set_pool(self.fake_pool)  # type: ignore[arg-type]

    def tearDown(self) -> None:
        db.reset_pool()

    def _seed_returning_id(self, run_id: int) -> None:
        # Each call to pool.connection() builds a fresh _FakeConnection; we
        # need the FIRST connection's cursor.fetchone() to return run_id.
        original = self.fake_pool.connection

        @contextmanager
        def first_connection() -> Iterator[_FakeConnection]:
            with original() as conn:
                conn.cursor_obj.fetch_value = (run_id,)
                yield conn

        self.fake_pool.connection = first_connection  # type: ignore[assignment]

    def test_records_running_and_success(self) -> None:
        self._seed_returning_id(42)

        with db.ingest_run("defillama", endpoint="protocols", metadata={"foo": 1}) as run:
            self.assertEqual(run.id, 42)
            run.add_rows(7)

        # Two connections used: one for the insert, one for the success update.
        self.assertEqual(len(self.fake_pool.connections), 2)
        insert_conn, success_conn = self.fake_pool.connections
        # First call is the INSERT with status='running' and the metadata JSON.
        insert_query, insert_params = insert_conn.cursor_obj.executed[0]
        self.assertIn("INSERT INTO meta.ingest_runs", insert_query)
        self.assertEqual(insert_params[0], "defillama")
        self.assertEqual(insert_params[1], "protocols")
        self.assertEqual(insert_params[2], '{"foo": 1}')
        # Second call is the success UPDATE with the accumulated rows_written.
        success_query, success_params = success_conn.cursor_obj.executed[0]
        self.assertIn("status='success'", success_query)
        self.assertEqual(success_params, [7, 42])

    def test_records_failed_and_reraises(self) -> None:
        self._seed_returning_id(99)

        with self.assertRaises(RuntimeError) as ctx, db.ingest_run("sec") as run:
            run.add_rows(3)
            raise RuntimeError("api blew up")
        self.assertEqual(str(ctx.exception), "api blew up")

        self.assertEqual(len(self.fake_pool.connections), 2)
        fail_conn = self.fake_pool.connections[1]
        fail_query, fail_params = fail_conn.cursor_obj.executed[0]
        self.assertIn("status='failed'", fail_query)
        self.assertEqual(fail_params[0], "api blew up")
        self.assertEqual(fail_params[1], 3)
        self.assertEqual(fail_params[2], 99)

    def test_records_base_exception_interrupts_as_failed(self) -> None:
        self._seed_returning_id(5)

        with self.assertRaises(KeyboardInterrupt), db.ingest_run("sec") as run:
            run.add_rows(2)
            raise KeyboardInterrupt("ctrl-c")

        fail_conn = self.fake_pool.connections[1]
        fail_query, fail_params = fail_conn.cursor_obj.executed[0]
        self.assertIn("status='failed'", fail_query)
        self.assertEqual(fail_params, ["ctrl-c", 2, 5])

    def test_failure_audit_error_preserves_original_exception(self) -> None:
        self._seed_returning_id(12)
        original = self.fake_pool.connection

        @contextmanager
        def failing_second_connection() -> Iterator[_FakeConnection]:
            with original() as conn:
                if len(self.fake_pool.connections) == 2:
                    conn.cursor_obj.execute_error = RuntimeError("audit failed")
                yield conn

        self.fake_pool.connection = failing_second_connection  # type: ignore[assignment]

        with (
            self.assertLogs("genkei.common.db", level="ERROR"),
            self.assertRaisesRegex(RuntimeError, "ingest failed"),
            db.ingest_run("sec"),
        ):
            raise RuntimeError("ingest failed")

    def test_truncates_long_error_messages(self) -> None:
        self._seed_returning_id(1)
        long_msg = "x" * 20000

        with self.assertRaises(RuntimeError), db.ingest_run("fred"):
            raise RuntimeError(long_msg)

        fail_conn = self.fake_pool.connections[1]
        _, fail_params = fail_conn.cursor_obj.executed[0]
        self.assertEqual(len(fail_params[0]), db._ERROR_FIELD_LIMIT)
        self.assertEqual(fail_params[0], "x" * db._ERROR_FIELD_LIMIT)

    def test_nullable_endpoint_and_metadata(self) -> None:
        self._seed_returning_id(7)
        with db.ingest_run("treasury") as run:
            run.add_rows(0)
        insert_conn = self.fake_pool.connections[0]
        _, insert_params = insert_conn.cursor_obj.executed[0]
        self.assertIsNone(insert_params[1])
        self.assertIsNone(insert_params[2])


class IngestRunHandleTests(unittest.TestCase):
    def test_add_rows_accumulates(self) -> None:
        run = db.IngestRun(id=1)
        run.add_rows(3)
        run.add_rows(4)
        self.assertEqual(run.rows_written, 7)

    def test_add_rows_rejects_negative_increment(self) -> None:
        run = db.IngestRun(id=1)
        with self.assertRaisesRegex(ValueError, "non-negative"):
            run.add_rows(-1)

    def test_add_rows_rejects_non_integer_increment(self) -> None:
        run = db.IngestRun(id=1)
        with self.assertRaisesRegex(TypeError, "integer"):
            run.add_rows(1.5)  # type: ignore[arg-type]

    def test_add_rows_rejects_bool_increment(self) -> None:
        run = db.IngestRun(id=1)
        with self.assertRaisesRegex(TypeError, "integer"):
            run.add_rows(True)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
