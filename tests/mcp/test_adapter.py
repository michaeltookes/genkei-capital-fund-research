"""Adapter tests for the MCP server (B-130).

Pins argv construction and subprocess routing. The subprocess call is stubbed
(``subprocess.run`` patched), so these are offline and never touch the DB or
spawn a real CLI. SDK-free — no ``mcp`` import.
"""

from __future__ import annotations

import sys
import unittest
from subprocess import TimeoutExpired
from unittest.mock import patch

from genkei.mcp import adapter
from genkei.mcp.adapter import (
    CLI_MODULE,
    ToolInvocationError,
    build_argv,
    run_tool,
)
from genkei.mcp.registry import tool_by_name


class BuildArgvTests(unittest.TestCase):
    def test_prefix_targets_the_same_interpreter_and_module(self) -> None:
        argv = build_argv(tool_by_name("watchlist_health"), {})
        self.assertEqual(argv[:5], [sys.executable, "-m", CLI_MODULE, "watchlist", "health"])
        self.assertEqual(argv[-1], "--json")

    def test_string_option_renders_flag_and_value(self) -> None:
        argv = build_argv(tool_by_name("prices"), {"ticker": "BTC", "since": "2024-01-01"})
        self.assertIn("--ticker", argv)
        self.assertEqual(argv[argv.index("--ticker") + 1], "BTC")
        self.assertIn("--since", argv)
        self.assertEqual(argv[argv.index("--since") + 1], "2024-01-01")

    def test_boolean_true_is_a_bare_flag(self) -> None:
        argv = build_argv(tool_by_name("zcash_usage"), {"by_pool": True})
        self.assertIn("--by-pool", argv)
        # No value follows a bare boolean flag.
        self.assertNotEqual(
            argv[argv.index("--by-pool") + 1 : argv.index("--by-pool") + 2], ["True"]
        )

    def test_boolean_false_is_omitted(self) -> None:
        argv = build_argv(tool_by_name("zcash_usage"), {"by_pool": False})
        self.assertNotIn("--by-pool", argv)

    def test_positional_precedes_options(self) -> None:
        argv = build_argv(tool_by_name("query"), {"sql": "SELECT 1", "limit": 5})
        # The positional SQL string appears before any --flag.
        sql_idx = argv.index("SELECT 1")
        limit_idx = argv.index("--limit")
        self.assertLess(sql_idx, limit_idx)
        self.assertEqual(argv[-1], "--json")

    def test_missing_required_param_raises(self) -> None:
        with self.assertRaises(ToolInvocationError):
            build_argv(tool_by_name("prices"), {})  # ticker required

    def test_unknown_param_raises(self) -> None:
        with self.assertRaises(ToolInvocationError):
            build_argv(tool_by_name("prices"), {"ticker": "BTC", "bogus": 1})


class _FakeCompleted:
    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class RunToolTests(unittest.TestCase):
    def test_run_tool_returns_stdout_on_success(self) -> None:
        fake = _FakeCompleted(0, '[{"ts": "2024-01-01"}]', "")
        with patch.object(adapter.subprocess, "run", return_value=fake) as mock_run:
            result = run_tool(tool_by_name("prices"), {"ticker": "BTC"})
        self.assertTrue(result.ok)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("ts", result.stdout)
        # shell=False path: argv passed positionally, not a shell string.
        called_argv = mock_run.call_args[0][0]
        self.assertEqual(called_argv[:3], [sys.executable, "-m", CLI_MODULE])
        self.assertIn("--json", called_argv)

    def test_run_tool_surfaces_nonzero_exit(self) -> None:
        fake = _FakeCompleted(2, "", "Ticker 'ZZZ' not found in watchlist.")
        with patch.object(adapter.subprocess, "run", return_value=fake):
            result = run_tool(tool_by_name("prices"), {"ticker": "ZZZ"})
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 2)
        self.assertIn("not found", result.stderr)

    def test_run_tool_never_uses_a_shell(self) -> None:
        fake = _FakeCompleted(0, "[]", "")
        with patch.object(adapter.subprocess, "run", return_value=fake) as mock_run:
            run_tool(tool_by_name("watchlist_health"), {})
        # subprocess.run must be called with a list argv, not shell=True.
        _, kwargs = mock_run.call_args
        self.assertNotEqual(kwargs.get("shell"), True)
        self.assertIsInstance(mock_run.call_args[0][0], list)

    def test_run_tool_surfaces_timeout_as_failure_result(self) -> None:
        timeout = TimeoutExpired(
            cmd=[sys.executable, "-m", CLI_MODULE],
            timeout=1.5,
            output=b'{"partial": true}',
            stderr=b"freshness warning\n",
        )
        with patch.object(adapter.subprocess, "run", side_effect=timeout):
            result = run_tool(tool_by_name("watchlist_health"), {}, timeout_seconds=1.5)
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, -1)
        self.assertEqual(result.stdout, '{"partial": true}')
        self.assertIn("freshness warning", result.stderr)
        self.assertIn("timed out after 1.5s", result.stderr)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
