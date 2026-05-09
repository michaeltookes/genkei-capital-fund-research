"""Unit tests for genkei.common.config."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from genkei.common import config


class LoadEnvFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved: dict[str, str | None] = {}
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def tearDown(self) -> None:
        # Restore any env vars we touched.
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _track(self, *keys: str) -> None:
        for k in keys:
            self._saved[k] = os.environ.get(k)
            os.environ.pop(k, None)

    def _write(self, contents: str, name: str = ".env") -> Path:
        path = self.tmpdir / name
        path.write_text(contents)
        return path

    def test_returns_zero_when_file_missing(self) -> None:
        path = self.tmpdir / "does-not-exist"
        self.assertEqual(config.load_env_file(path), 0)

    def test_loads_simple_pairs(self) -> None:
        self._track("GENKEI_TEST_A", "GENKEI_TEST_B")
        path = self._write("GENKEI_TEST_A=value-a\nGENKEI_TEST_B=value-b\n")
        written = config.load_env_file(path)
        self.assertEqual(written, 2)
        self.assertEqual(os.environ["GENKEI_TEST_A"], "value-a")
        self.assertEqual(os.environ["GENKEI_TEST_B"], "value-b")

    def test_skips_comments_and_blanks(self) -> None:
        self._track("GENKEI_TEST_KEY")
        path = self._write(
            "\n# a comment\n\nGENKEI_TEST_KEY=value\n# trailing\n"
        )
        self.assertEqual(config.load_env_file(path), 1)
        self.assertEqual(os.environ["GENKEI_TEST_KEY"], "value")

    def test_skips_lines_without_equals(self) -> None:
        self._track("GENKEI_TEST_OK")
        path = self._write("garbage line\nGENKEI_TEST_OK=fine\nanother garbage\n")
        self.assertEqual(config.load_env_file(path), 1)
        self.assertEqual(os.environ["GENKEI_TEST_OK"], "fine")

    def test_strips_double_quoted_values(self) -> None:
        self._track("GENKEI_TEST_QUOTED")
        path = self._write('GENKEI_TEST_QUOTED="quoted value"\n')
        config.load_env_file(path)
        self.assertEqual(os.environ["GENKEI_TEST_QUOTED"], "quoted value")

    def test_strips_single_quoted_values(self) -> None:
        self._track("GENKEI_TEST_SQ")
        path = self._write("GENKEI_TEST_SQ='single quoted'\n")
        config.load_env_file(path)
        self.assertEqual(os.environ["GENKEI_TEST_SQ"], "single quoted")

    def test_existing_env_vars_take_precedence(self) -> None:
        self._track("GENKEI_TEST_PRECEDENCE")
        os.environ["GENKEI_TEST_PRECEDENCE"] = "from-shell"
        path = self._write("GENKEI_TEST_PRECEDENCE=from-file\n")
        written = config.load_env_file(path)
        self.assertEqual(written, 0)
        self.assertEqual(os.environ["GENKEI_TEST_PRECEDENCE"], "from-shell")

    def test_value_containing_equals_is_preserved(self) -> None:
        self._track("GENKEI_TEST_URL")
        path = self._write(
            "GENKEI_TEST_URL=postgresql://u:pw@host/db?option=value\n"
        )
        config.load_env_file(path)
        self.assertEqual(
            os.environ["GENKEI_TEST_URL"],
            "postgresql://u:pw@host/db?option=value",
        )

    def test_blank_key_is_skipped(self) -> None:
        path = self._write("=lonely-value\n")
        self.assertEqual(config.load_env_file(path), 0)

    def test_count_returned_matches_writes_only(self) -> None:
        # One key already set; second is new. Count should be 1.
        self._track("GENKEI_TEST_EXISTING", "GENKEI_TEST_NEW")
        os.environ["GENKEI_TEST_EXISTING"] = "preserved"
        path = self._write(
            "GENKEI_TEST_EXISTING=overwritten\nGENKEI_TEST_NEW=new\n"
        )
        self.assertEqual(config.load_env_file(path), 1)
        self.assertEqual(os.environ["GENKEI_TEST_EXISTING"], "preserved")
        self.assertEqual(os.environ["GENKEI_TEST_NEW"], "new")


if __name__ == "__main__":
    unittest.main()
