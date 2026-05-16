"""Unit tests for the `genkei tvl` subcommand (B-041)."""

from __future__ import annotations

import io
import json as json_mod
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from genkei.cli import main
from genkei.cli.tvl import (
    _format_chain_tvl_human,
    _format_chains_overview_human,
    _format_protocol_tvl_human,
    _parse_date,
)


class FormatTests(unittest.TestCase):
    def test_chain_tvl_empty_hints_at_case_sensitivity(self) -> None:
        out = _format_chain_tvl_human("ethereum", [])
        self.assertIn("ethereum", out)
        self.assertIn("Ethereum", out)  # case hint
        self.assertIn("--since", out)

    def test_chain_tvl_renders_with_commas(self) -> None:
        rows = [
            {"ts": "2026-05-15T19:00:00+00:00", "tvl_usd": 73_500_000_000},
            {"ts": "2026-05-14T19:00:00+00:00", "tvl_usd": 73_100_000_000},
        ]
        out = _format_chain_tvl_human("Ethereum", rows)
        self.assertIn("Ethereum", out)
        self.assertIn("73,500,000,000", out)

    def test_protocol_tvl_empty_points_at_watchlist_health(self) -> None:
        # Critical: this is the message users see today since protocol_tvl
        # is empty. It must steer them at `genkei watchlist health`.
        out = _format_protocol_tvl_human("aave-v3", [])
        self.assertIn("aave-v3", out)
        self.assertIn("genkei watchlist health", out)
        self.assertIn("--chain", out)

    def test_chains_overview_sorts_by_tvl_desc(self) -> None:
        rows = [
            {"chain": "Ethereum", "ts": "2026-05-15", "tvl_usd": 73_500_000_000},
            {"chain": "Solana", "ts": "2026-05-15", "tvl_usd": 12_000_000_000},
        ]
        out = _format_chains_overview_human(rows)
        # Ethereum appears before Solana in the rendered text
        self.assertLess(out.index("Ethereum"), out.index("Solana"))
        self.assertIn("73,500,000,000", out)


class ParseDateTests(unittest.TestCase):
    def test_garbage_raises(self) -> None:
        import typer

        with self.assertRaises(typer.BadParameter):
            _parse_date("nope", label="since")


class TvlCommandTests(unittest.TestCase):
    def test_chain_and_protocol_mutually_exclusive(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(
                ["tvl", "--chain", "Ethereum", "--protocol", "aave-v3"]
            )
        self.assertEqual(code, 2)

    def test_since_after_until_rejected(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(
                [
                    "tvl",
                    "--chain",
                    "Ethereum",
                    "--since",
                    "2024-06-01",
                    "--until",
                    "2024-01-01",
                ]
            )
        self.assertEqual(code, 2)

    def test_chain_mode_queries_chain_tvl(self) -> None:
        rows = [{"ts": "2026-05-15T19:00:00+00:00", "tvl_usd": 73_500_000_000}]
        out = io.StringIO()
        with (
            patch("genkei.cli.tvl._query_chain_tvl", return_value=rows) as mocked,
            redirect_stdout(out),
        ):
            code = main(["tvl", "--chain", "Ethereum"])
        self.assertIn(code, (None, 0))
        self.assertEqual(mocked.call_args.args[0], "Ethereum")
        self.assertIn("73,500,000,000", out.getvalue())

    def test_protocol_mode_queries_protocol_tvl(self) -> None:
        out = io.StringIO()
        with (
            patch("genkei.cli.tvl._query_protocol_tvl", return_value=[]) as mocked,
            redirect_stdout(out),
        ):
            code = main(["tvl", "--protocol", "aave-v3"])
        self.assertIn(code, (None, 0))
        self.assertEqual(mocked.call_args.args[0], "aave-v3")
        # Empty path renders the steer-to-health hint
        self.assertIn("genkei watchlist health", out.getvalue())

    def test_default_mode_calls_chains_overview(self) -> None:
        rows = [
            {"chain": "Ethereum", "ts": "2026-05-15", "tvl_usd": 73_500_000_000}
        ]
        out = io.StringIO()
        with (
            patch("genkei.cli.tvl._query_chains_overview", return_value=rows),
            redirect_stdout(out),
        ):
            code = main(["tvl"])
        self.assertIn(code, (None, 0))
        self.assertIn("Ethereum", out.getvalue())
        self.assertIn("chains overview", out.getvalue())

    def test_json_mode_emits_valid_array(self) -> None:
        rows = [{"ts": "2026-05-15T19:00:00+00:00", "tvl_usd": 73_500_000_000}]
        out = io.StringIO()
        with (
            patch("genkei.cli.tvl._query_chain_tvl", return_value=rows),
            redirect_stdout(out),
        ):
            main(["tvl", "--chain", "Ethereum", "--json"])
        parsed = json_mod.loads(out.getvalue())
        self.assertEqual(parsed[0]["tvl_usd"], 73_500_000_000)


if __name__ == "__main__":
    unittest.main()
