"""Unit tests for the genkei CLI entry point + prices subcommand."""

from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from genkei.cli import main
from genkei.cli.prices import _format_human, _parse_date


class ParseDateTests(unittest.TestCase):
    def test_parses_iso(self) -> None:
        from datetime import date as date_type

        self.assertEqual(_parse_date("2024-01-15", label="since"), date_type(2024, 1, 15))

    def test_returns_none_for_none(self) -> None:
        self.assertIsNone(_parse_date(None, label="since"))

    def test_raises_typer_bad_parameter_for_garbage(self) -> None:
        import typer

        with self.assertRaises(typer.BadParameter) as ctx:
            _parse_date("nope", label="since")
        self.assertIn("--since", str(ctx.exception))


class FormatHumanTests(unittest.TestCase):
    def test_no_rows_message_includes_ticker_and_source(self) -> None:
        out = _format_human("BTC", "coingecko", [])
        self.assertIn("BTC", out)
        self.assertIn("coingecko", out)
        self.assertIn("No price rows", out)

    def test_table_renders_each_row_with_formatted_numbers(self) -> None:
        rows = [
            {
                "ts": "2024-01-03T00:00:00+00:00",
                "price_usd": 41800.5,
                "market_cap_usd": 815_100_000_000,
                "volume_usd": 696_666_666,
            },
        ]
        out = _format_human("BTC", "coingecko", rows)
        self.assertIn("BTC", out)
        self.assertIn("41,800.50", out)
        self.assertIn("815,100,000,000", out)


class CliHelpTests(unittest.TestCase):
    def test_help_lists_all_seven_subcommand_groups(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            main(["--help"])
        out = buf.getvalue()
        for cmd in ["prices", "filings", "tvl", "macro", "news", "watchlist", "query"]:
            self.assertIn(cmd, out)


class PricesCommandTests(unittest.TestCase):
    def _watchlist(self, body: str) -> Path:
        ctx = TemporaryDirectory()
        self.addCleanup(ctx.cleanup)
        tmp = Path(ctx.name)
        path = tmp / "watchlists.yml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_unknown_ticker_friendly_error(self) -> None:
        path = self._watchlist(
            "crypto:\n  primary:\n    - symbol: BTC\n"
            "      name: Bitcoin\n      coingecko_id: bitcoin\n"
        )
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["prices", "--ticker", "UNKNOWN", "--config", str(path)])
        self.assertEqual(code, 2)
        self.assertIn("UNKNOWN", err.getvalue())
        self.assertIn("not found", err.getvalue())

    def test_equity_ticker_with_crypto_source_errors_loudly(self) -> None:
        # B-092 wired Yahoo as the equity price source. The legacy
        # "no price source yet" branch is gone; what remains is the
        # source/asset-class mismatch error path — passing a crypto
        # source for an equity ticker should still error loudly.
        path = self._watchlist(
            "crypto:\n  primary:\n    - symbol: BTC\n"
            "      name: Bitcoin\n      coingecko_id: bitcoin\n"
            "equities:\n  primary:\n    - symbol: AAPL\n"
            '      name: Apple Inc.\n      cik: "0000320193"\n'
        )
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(
                ["prices", "--ticker", "AAPL", "--source", "coingecko", "--config", str(path)]
            )
        self.assertEqual(code, 2)
        self.assertIn("equity", err.getvalue().lower())
        self.assertIn("yahoo", err.getvalue().lower())

    def test_overlapping_symbol_uses_explicit_yahoo_source_for_equity(self) -> None:
        path = self._watchlist(
            "crypto:\n  primary:\n    - symbol: ABC\n"
            "      name: ABC Token\n      coingecko_id: abc-token\n"
            "equities:\n  primary:\n    - symbol: ABC\n"
            '      name: AmerisourceBergen\n      cik: "0001140859"\n'
        )
        rows = [
            {
                "ts": "2024-01-01T00:00:00+00:00",
                "price_usd": 100.0,
                "market_cap_usd": None,
                "volume_usd": 1000.0,
                "close_unadjusted": 100.0,
            }
        ]
        out = io.StringIO()
        with (
            patch("genkei.cli.prices._query_yahoo_candles", return_value=rows) as query,
            redirect_stdout(out),
        ):
            code = main(["prices", "--ticker", "ABC", "--source", "yahoo", "--config", str(path)])
        self.assertIn(code, (None, 0))
        query.assert_called_once()
        self.assertIn("ABC", out.getvalue())
        self.assertIn("100.00", out.getvalue())

    def test_overlapping_symbol_without_source_errors_loudly(self) -> None:
        path = self._watchlist(
            "crypto:\n  primary:\n    - symbol: ABC\n"
            "      name: ABC Token\n      coingecko_id: abc-token\n"
            "equities:\n  primary:\n    - symbol: ABC\n"
            '      name: AmerisourceBergen\n      cik: "0001140859"\n'
        )
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["prices", "--ticker", "ABC", "--config", str(path)])
        self.assertEqual(code, 2)
        self.assertIn("both crypto and equities", err.getvalue())
        self.assertIn("--source yahoo", err.getvalue())

    def test_crypto_ticker_queries_coingecko_market_data(self) -> None:
        # Patch the DB query to avoid hitting Postgres.
        path = self._watchlist(
            "crypto:\n  primary:\n    - symbol: BTC\n"
            "      name: Bitcoin\n      coingecko_id: bitcoin\n"
        )
        rows = [
            {
                "ts": "2024-01-01T00:00:00+00:00",
                "price_usd": 42000.0,
                "market_cap_usd": 819_000_000_000,
                "volume_usd": 700_000_000,
            }
        ]
        out = io.StringIO()
        with (
            patch("genkei.cli.prices._query_coingecko_market_data", return_value=rows),
            redirect_stdout(out),
        ):
            code = main(["prices", "--ticker", "BTC", "--config", str(path)])
        self.assertIn(code, (None, 0))
        self.assertIn("BTC", out.getvalue())
        self.assertIn("42,000.00", out.getvalue())

    def test_prices_default_config_is_independent_of_cwd(self) -> None:
        ctx = TemporaryDirectory()
        self.addCleanup(ctx.cleanup)
        original_cwd = Path.cwd()
        rows = [
            {
                "ts": "2024-01-01T00:00:00+00:00",
                "price_usd": 42000.0,
                "market_cap_usd": 819_000_000_000,
                "volume_usd": 700_000_000,
            }
        ]
        out = io.StringIO()
        try:
            os.chdir(ctx.name)
            with (
                patch("genkei.cli.prices._query_coingecko_market_data", return_value=rows),
                redirect_stdout(out),
            ):
                code = main(["prices", "--ticker", "BTC"])
        finally:
            os.chdir(original_cwd)
        self.assertIn(code, (None, 0))
        self.assertIn("BTC", out.getvalue())

    def test_json_mode_emits_valid_json_array(self) -> None:
        import json as json_mod

        path = self._watchlist(
            "crypto:\n  primary:\n    - symbol: BTC\n"
            "      name: Bitcoin\n      coingecko_id: bitcoin\n"
        )
        rows = [
            {
                "ts": "2024-01-01T00:00:00+00:00",
                "price_usd": 42000.0,
                "market_cap_usd": 1.0,
                "volume_usd": 1.0,
            }
        ]
        out = io.StringIO()
        with (
            patch("genkei.cli.prices._query_coingecko_market_data", return_value=rows),
            redirect_stdout(out),
        ):
            main(["prices", "--ticker", "BTC", "--config", str(path), "--json"])
        parsed = json_mod.loads(out.getvalue())
        self.assertEqual(parsed[0]["price_usd"], 42000.0)

    def test_since_after_until_rejected(self) -> None:
        path = self._watchlist(
            "crypto:\n  primary:\n    - symbol: BTC\n"
            "      name: Bitcoin\n      coingecko_id: bitcoin\n"
        )
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(
                [
                    "prices",
                    "--ticker",
                    "BTC",
                    "--since",
                    "2024-06-01",
                    "--until",
                    "2024-01-01",
                    "--config",
                    str(path),
                ]
            )
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
