"""Tests for `genkei query` result caching (B-046)."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from tempfile import TemporaryDirectory
from unittest.mock import patch

from genkei.cli import main


class QueryCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        env = patch.dict("os.environ", {"GENKEI_CACHE_DIR": self._tmp.name}, clear=False)
        env.start()
        self.addCleanup(env.stop)

    def _run(self, argv: list[str]) -> str:
        out = io.StringIO()
        with redirect_stdout(out):
            main(argv)
        return out.getvalue()

    def test_second_identical_query_hits_cache(self) -> None:
        with patch(
            "genkei.cli.query.execute_readonly",
            return_value=(["n"], [(532,)]),
        ) as mocked:
            first = self._run(["query", "SELECT count(*) AS n FROM t"])
            second = self._run(["query", "SELECT count(*) AS n FROM t"])
        # DB hit exactly once; the second call was served from cache.
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(first, second)
        self.assertIn("532", first)

    def test_no_cache_forces_fresh_and_does_not_populate(self) -> None:
        with patch(
            "genkei.cli.query.execute_readonly",
            return_value=(["n"], [(1,)]),
        ) as mocked:
            self._run(["query", "SELECT 1 AS n", "--no-cache"])
            self._run(["query", "SELECT 1 AS n", "--no-cache"])
        # Both bypassed the cache → two DB hits.
        self.assertEqual(mocked.call_count, 2)

    def test_no_cache_ignores_existing_entry(self) -> None:
        with patch(
            "genkei.cli.query.execute_readonly",
            return_value=(["n"], [(1,)]),
        ) as mocked:
            self._run(["query", "SELECT 1 AS n"])  # populate
            self._run(["query", "SELECT 1 AS n", "--no-cache"])  # must still hit DB
        self.assertEqual(mocked.call_count, 2)

    def test_different_limit_is_a_cache_miss(self) -> None:
        with patch(
            "genkei.cli.query.execute_readonly",
            return_value=(["x"], [(1,), (2,), (3,)]),
        ) as mocked:
            self._run(["query", "SELECT x FROM t"])
            self._run(["query", "SELECT x FROM t", "--limit", "2"])
        self.assertEqual(mocked.call_count, 2)

    def test_different_format_is_a_cache_miss(self) -> None:
        with patch(
            "genkei.cli.query.execute_readonly",
            return_value=(["x"], [(1,)]),
        ) as mocked:
            self._run(["query", "SELECT x FROM t"])
            self._run(["query", "SELECT x FROM t", "--json"])
        self.assertEqual(mocked.call_count, 2)

    def test_different_database_url_is_a_cache_miss(self) -> None:
        with patch(
            "genkei.cli.query.execute_readonly",
            return_value=(["x"], [(1,)]),
        ) as mocked:
            with patch.dict(
                "os.environ",
                {"GENKEI_DATABASE_URL": "postgresql://user:pw@prod.example/research"},
                clear=False,
            ):
                self._run(["query", "SELECT x FROM t"])
            with patch.dict(
                "os.environ",
                {"GENKEI_DATABASE_URL": "postgresql://user:pw@test.example/research"},
                clear=False,
            ):
                self._run(["query", "SELECT x FROM t"])
        self.assertEqual(mocked.call_count, 2)

    def test_timeout_does_not_change_key(self) -> None:
        # --timeout-seconds can only affect whether a query errors, not the
        # content of a success, so it is excluded from the cache key.
        with patch(
            "genkei.cli.query.execute_readonly",
            return_value=(["n"], [(1,)]),
        ) as mocked:
            self._run(["query", "SELECT 1 AS n", "--timeout-seconds", "10"])
            self._run(["query", "SELECT 1 AS n", "--timeout-seconds", "30"])
        self.assertEqual(mocked.call_count, 1)

    def test_error_is_not_cached(self) -> None:
        from psycopg.errors import SyntaxError as PgSyntaxError

        with patch(
            "genkei.cli.query.execute_readonly",
            side_effect=PgSyntaxError("boom"),
        ) as mocked:
            code1 = main(["query", "SELECT bad syntax"])
            code2 = main(["query", "SELECT bad syntax"])
        # The failure was not cached — the retry re-hit the DB.
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(code1, 1)
        self.assertEqual(code2, 1)

    def test_expired_entry_refetches(self) -> None:
        # Store at t=1000, advance the clock 2s, read with --cache-ttl 1 → the
        # entry is stale, so the second call re-hits the DB.
        clock = {"t": 1000.0}
        with (
            patch(
                "genkei.cli.query.execute_readonly",
                return_value=(["n"], [(1,)]),
            ) as mocked,
            patch("genkei.common.cache.time.time", side_effect=lambda: clock["t"]),
        ):
            self._run(["query", "SELECT 1 AS n"])
            clock["t"] = 1002.0
            self._run(["query", "SELECT 1 AS n", "--cache-ttl", "1"])
        self.assertEqual(mocked.call_count, 2)


if __name__ == "__main__":
    unittest.main()
