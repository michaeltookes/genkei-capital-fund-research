"""Server response formatting tests for the MCP wrapper (B-130).

These stay SDK-free: the full MCP server imports the optional ``mcp`` package
inside ``serve()``, but the success formatting contract is pure string handling.
"""

from __future__ import annotations

import unittest

from genkei.mcp.server import successful_tool_text


class SuccessfulToolTextTests(unittest.TestCase):
    def test_preserves_json_stdout_without_appending_stderr_warning(self) -> None:
        text = successful_tool_text('[{"ticker": "BTC"}]\n', '{"warning": "stale"}\n')
        self.assertEqual(text, '[{"ticker": "BTC"}]')

    def test_empty_success_uses_placeholder(self) -> None:
        self.assertEqual(successful_tool_text("", "freshness warning"), "(no output)")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
