"""Offline tests for the Postgres integration-test harness."""

from __future__ import annotations

import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests import _postgres


class PostgresHarnessHelperTests(unittest.TestCase):
    def test_sqlalchemy_url_uses_psycopg_driver(self) -> None:
        self.assertEqual(
            _postgres._sqlalchemy_url("postgresql://test:test@localhost:5432/test"),
            "postgresql+psycopg://test:test@localhost:5432/test",
        )
        self.assertEqual(
            _postgres._sqlalchemy_url("postgresql+psycopg2://test:test@localhost:5432/test"),
            "postgresql+psycopg://test:test@localhost:5432/test",
        )

    def test_user_table_sql_returns_schema_and_table_names(self) -> None:
        self.assertIn("SELECT table_schema, table_name", _postgres.USER_TABLES_SQL)
        self.assertNotIn("%I", _postgres.USER_TABLES_SQL)

    @patch("tests._postgres.subprocess.run")
    @patch("tests._postgres.shutil.which", return_value="/usr/bin/docker")
    def test_docker_available_uses_docker_info(self, _which: MagicMock, run: MagicMock) -> None:
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
        self.assertFalse(_postgres._docker_available())
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
