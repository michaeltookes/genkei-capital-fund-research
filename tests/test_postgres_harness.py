"""Offline tests for the Postgres integration-test harness."""

from __future__ import annotations

import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests import _postgres


class PostgresHarnessHelperTests(unittest.TestCase):
    """Offline tests for the Postgres harness helper behavior."""

    def test_sqlalchemy_url_uses_psycopg_driver(self) -> None:
        """SQLAlchemy URLs should select the installed psycopg 3 driver."""
        self.assertEqual(
            _postgres._sqlalchemy_url("postgresql://test:test@localhost:5432/test"),
            "postgresql+psycopg://test:test@localhost:5432/test",
        )
        self.assertEqual(
            _postgres._sqlalchemy_url("postgresql+psycopg2://test:test@localhost:5432/test"),
            "postgresql+psycopg://test:test@localhost:5432/test",
        )

    def test_user_table_sql_returns_schema_and_table_names(self) -> None:
        """The cleanup query should return identifier parts, not formatted SQL."""
        self.assertIn("SELECT table_schema, table_name", _postgres.USER_TABLES_SQL)
        self.assertNotIn("%I", _postgres.USER_TABLES_SQL)

    @patch("tests._postgres.subprocess.run")
    @patch("tests._postgres.shutil.which", return_value="/usr/bin/docker")
    def test_docker_available_uses_docker_info(self, _which: MagicMock, run: MagicMock) -> None:
        """Docker availability should be probed through the daemon."""
        run.return_value = SimpleNamespace(returncode=0)

        self.assertTrue(_postgres._docker_available())
        run.assert_called_once_with(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    @patch("tests._postgres.subprocess.run")
    @patch("tests._postgres.shutil.which", return_value=None)
    def test_docker_available_skips_probe_without_cli(
        self, _which: MagicMock, run: MagicMock
    ) -> None:
        """Missing Docker CLI should short-circuit the daemon probe."""
        self.assertFalse(_postgres._docker_available())
        run.assert_not_called()

    @patch("tests._postgres.PostgresContainer", create=True)
    @patch.object(_postgres.PostgresHarness, "_apply_migrations")
    def test_harness_start_failure_skips_integration_tests(
        self, apply_migrations: MagicMock, container_cls: MagicMock
    ) -> None:
        """Container startup failures should skip instead of erroring."""
        container = MagicMock()
        container.start.side_effect = RuntimeError("registry timeout")
        container_cls.return_value = container

        with self.assertRaises(unittest.SkipTest) as ctx:
            _postgres.PostgresHarness()

        self.assertIn("could not start", str(ctx.exception))
        apply_migrations.assert_not_called()


if __name__ == "__main__":
    unittest.main()
