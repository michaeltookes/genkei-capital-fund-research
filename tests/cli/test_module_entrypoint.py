"""Unit tests for the `python3 -m genkei.cli` module entry point.

`python3 -m genkei.cli ...` was failing in the first live /research
session with "No module named genkei.cli.__main__". The fix is a
five-line `__main__.py` that forwards to `genkei.cli:main`. These
tests pin both the import path and the argv pass-through so the
papercut doesn't re-appear silently.
"""

from __future__ import annotations

import importlib
import runpy
import sys
import unittest
from unittest.mock import patch


class ModuleEntrypointTests(unittest.TestCase):
    def test_main_module_imports_cleanly(self) -> None:
        # The module itself imports without raising — this catches the
        # "no module named genkei.cli.__main__" papercut at the import
        # layer, before runpy semantics get involved.
        mod = importlib.import_module("genkei.cli.__main__")
        # Sanity: it re-exports the same `main` callable that the
        # console-script entry point uses.
        from genkei.cli import main

        self.assertIs(mod.main, main)

    def test_module_run_invokes_main_with_argv_minus_module(self) -> None:
        # Simulate `python3 -m genkei.cli --help` — set sys.argv to what
        # the interpreter would set, then runpy the module with __main__
        # semantics. The console-script `main()` should be called with
        # sys.argv[1:] (i.e. ["--help"]).
        with (
            patch("genkei.cli.main") as mocked,
            patch.object(sys, "argv", ["genkei.cli", "--help"]),
            # SystemExit is the expected normal-exit shape per the
            # __main__ module's `raise SystemExit(main(...))`.
            self.assertRaises(SystemExit),
        ):
            runpy.run_module("genkei.cli", run_name="__main__", alter_sys=True)
        mocked.assert_called_once_with(["--help"])


if __name__ == "__main__":
    unittest.main()
