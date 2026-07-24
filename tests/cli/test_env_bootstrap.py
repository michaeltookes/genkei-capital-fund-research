"""Unit tests for the CLI ``.env`` bootstrap (B-135).

``genkei.cli.main`` must call ``load_env_file()`` exactly once at startup,
before Typer dispatches to any subcommand, so a fresh shell (or an MCP
client that spawns the process without sourcing ``.env``) still resolves
``GENKEI_DATABASE_URL``. Existing environment variables must keep winning
— the loader only fills unset keys.

These tests are offline: they never touch Postgres. We invoke ``main``
with a subcommand that fails fast (``--help`` / bad args) so dispatch runs
but no DB connection is opened, and assert the loader was invoked.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from genkei.cli import main
from genkei.common import config as config_mod


class BootstrapInvocationTests(unittest.TestCase):
    def test_main_calls_load_env_file_once(self) -> None:
        """Every ``main`` invocation loads ``.env`` before dispatch."""
        with patch("genkei.cli.load_env_file") as loader:
            # ``--help`` exits 0 without opening a DB connection, so this
            # exercises the startup seam offline.
            main(["--help"])
        loader.assert_called_once_with()

    def test_bootstrap_runs_before_subcommand_dispatch(self) -> None:
        """The loader fires even when a subcommand later errors out."""
        with patch("genkei.cli.load_env_file") as loader:
            # A bad option makes Typer exit non-zero, but the bootstrap
            # must already have run by then.
            code = main(["watchlist", "--nonexistent-flag"])
        self.assertNotEqual(code, 0)
        loader.assert_called_once_with()


class EnvPrecedenceTests(unittest.TestCase):
    """Pin the loader's contract the bootstrap relies on: existing env wins."""

    def test_existing_env_var_is_not_overridden(self) -> None:
        with TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("GENKEI_TEST_KEY=from_file\n")
            with patch.dict(os.environ, {"GENKEI_TEST_KEY": "from_shell"}, clear=False):
                written = config_mod.load_env_file(env_path)
                self.assertEqual(os.environ["GENKEI_TEST_KEY"], "from_shell")
                self.assertEqual(written, 0)

    def test_unset_env_var_is_populated_from_file(self) -> None:
        with TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("GENKEI_TEST_UNSET=from_file\n")
            os.environ.pop("GENKEI_TEST_UNSET", None)
            try:
                written = config_mod.load_env_file(env_path)
                self.assertEqual(os.environ["GENKEI_TEST_UNSET"], "from_file")
                self.assertEqual(written, 1)
            finally:
                os.environ.pop("GENKEI_TEST_UNSET", None)

    def test_missing_file_is_a_no_op(self) -> None:
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist.env"
            self.assertEqual(config_mod.load_env_file(missing), 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
