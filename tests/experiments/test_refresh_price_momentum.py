"""Unit tests for the price-momentum matview refresh (B-067)."""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest.mock import patch

from genkei.experiments import refresh_price_momentum as rpm


class _FakeCursor:
    def __init__(self, executed: list[str], conn: _FakeConn) -> None:
        self._executed = executed
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query):
        # query is a psycopg.sql.Composed / SQL — stringify for assertion.
        self._executed.append(str(query))
        self._conn.autocommit_during_execute.append(self._conn.autocommit)
        if self._conn.fail_on_refresh and "REFRESH" in str(query):
            raise RuntimeError("boom")

    def fetchone(self):
        return (67,)


class _FakeConn:
    def __init__(self, executed: list[str], *, autocommit: bool = False) -> None:
        self._executed = executed
        self.autocommit = autocommit
        self.autocommit_during_execute: list[bool] = []
        self.fail_on_refresh = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._executed, self)


class _FakeRun:
    id = 99

    def __init__(self) -> None:
        self.rows_written = 0

    def add_rows(self, n: int) -> None:
        self.rows_written += n


class RefreshTests(unittest.TestCase):
    def _run(
        self, *, autocommit: bool = False
    ) -> tuple[rpm.RefreshResult, list[str], _FakeConn]:
        executed: list[str] = []
        conn = _FakeConn(executed, autocommit=autocommit)

        @contextmanager
        def fake_connection():
            yield conn

        @contextmanager
        def fake_ingest_run(*args, **kwargs):
            yield _FakeRun()

        with (
            patch("genkei.experiments.refresh_price_momentum.db.connection", fake_connection),
            patch(
                "genkei.experiments.refresh_price_momentum.db.ingest_run",
                fake_ingest_run,
            ),
        ):
            result = rpm.refresh()
        return result, executed, conn

    def test_refresh_uses_concurrently(self) -> None:
        _, executed, _ = self._run()
        refresh_stmt = next(s for s in executed if "REFRESH" in s)
        self.assertIn("CONCURRENTLY", refresh_stmt)
        self.assertIn("price_momentum", refresh_stmt)

    def test_refresh_uses_autocommit_and_restores_original_flag(self) -> None:
        # REFRESH ... CONCURRENTLY cannot run in a transaction block, but the
        # pooled connection must not be returned in autocommit mode.
        _, _, conn = self._run()
        self.assertTrue(all(conn.autocommit_during_execute))
        self.assertFalse(conn.autocommit)

    def test_refresh_preserves_preexisting_autocommit_flag(self) -> None:
        _, _, conn = self._run(autocommit=True)
        self.assertTrue(conn.autocommit)

    def test_refresh_restores_autocommit_after_error(self) -> None:
        executed: list[str] = []
        conn = _FakeConn(executed)
        conn.fail_on_refresh = True

        @contextmanager
        def fake_connection():
            yield conn

        @contextmanager
        def fake_ingest_run(*args, **kwargs):
            yield _FakeRun()

        with (
            patch("genkei.experiments.refresh_price_momentum.db.connection", fake_connection),
            patch(
                "genkei.experiments.refresh_price_momentum.db.ingest_run",
                fake_ingest_run,
            ),
            self.assertRaises(RuntimeError),
        ):
            rpm.refresh()
        self.assertFalse(conn.autocommit)

    def test_result_counts_rows_and_views(self) -> None:
        result, _, _ = self._run()
        self.assertEqual(result.views_refreshed, 1)
        self.assertEqual(result.total_rows, 67)
        self.assertEqual(result.ingest_run_id, 99)

    def test_registered_matview_is_analytics_price_momentum(self) -> None:
        self.assertIn(("analytics", "price_momentum"), rpm.MATERIALIZED_VIEWS)
        self.assertEqual(rpm.SOURCE_NAME, "price_momentum")
        self.assertEqual(rpm.ENDPOINT, "refresh")


if __name__ == "__main__":
    unittest.main()
