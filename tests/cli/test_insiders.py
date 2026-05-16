"""Unit tests for `genkei insiders` (B-079)."""

from __future__ import annotations

import io
import json as json_mod
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from genkei.cli import main
from genkei.cli.insiders import (
    _format_human,
    _format_role,
    _parse_date,
)

EQUITY_AND_CRYPTO_YAML = (
    "crypto:\n  primary:\n    - symbol: BTC\n      name: Bitcoin\n      coingecko_id: bitcoin\n"
    "equities:\n  primary:\n    - symbol: AAPL\n      name: Apple Inc.\n"
    '      cik: "0000320193"\n'
    "    - symbol: NOCIK\n      name: Nocik Co.\n"
)


def _watchlist_path(case: unittest.TestCase) -> Path:
    ctx = TemporaryDirectory()
    case.addCleanup(ctx.cleanup)
    tmp = Path(ctx.name)
    path = tmp / "watchlists.yml"
    path.write_text(EQUITY_AND_CRYPTO_YAML, encoding="utf-8")
    return path


SAMPLE_ISSUER_ROW = {
    "transaction_date": "2026-05-08",
    "transaction_code": "S",
    "acquired_disposed": "D",
    "shares": Decimal("1274"),
    "price_usd": Decimal("290.00"),
    "post_transaction_shares": Decimal("38713"),
    "is_derivative": False,
    "security_title": "Common Stock",
    "ownership_type": "D",
    "reporter_name": "Borders Ben",
    "reporter_cik": "0002100523",
    "is_director": False,
    "is_officer": True,
    "is_ten_percent_owner": False,
    "officer_title": "Principal Accounting Officer",
    "accession_number": "0001140361-26-020871",
}


class FormatHelperTests(unittest.TestCase):
    def test_role_combines_officer_director_owner_flags(self) -> None:
        row = {
            "is_officer": True,
            "officer_title": "CEO",
            "is_director": True,
            "is_ten_percent_owner": True,
        }
        out = _format_role(row)
        self.assertIn("officer(CEO)", out)
        self.assertIn("director", out)
        self.assertIn("10%-owner", out)

    def test_role_returns_dash_when_no_flags(self) -> None:
        self.assertEqual(_format_role({}), "-")

    def test_empty_rows_hints_at_health_check(self) -> None:
        out = _format_human(title="AAPL", rows=[])
        self.assertIn("AAPL", out)
        self.assertIn("genkei watchlist health", out)

    def test_table_renders_shares_with_commas(self) -> None:
        out = _format_human(title="AAPL", rows=[SAMPLE_ISSUER_ROW])
        self.assertIn("AAPL insider transactions", out)
        self.assertIn("1,274", out)
        self.assertIn("290.00", out)
        self.assertIn("Borders Ben", out)
        # transaction_code + acquired_disposed concatenated for display
        self.assertIn("SD", out)

    def test_reporter_view_shows_issuer_ticker_column(self) -> None:
        row = dict(SAMPLE_ISSUER_ROW)
        row["issuer_ticker"] = "AAPL"
        row["issuer_name"] = "Apple Inc."
        out = _format_human(
            title="reporter 0002100523", rows=[row], include_issuer=True
        )
        self.assertIn("AAPL", out)
        self.assertIn("tkr", out)  # header
        self.assertIn("Borders Ben", out)


class ParseDateTests(unittest.TestCase):
    def test_garbage_raises(self) -> None:
        import typer

        with self.assertRaises(typer.BadParameter):
            _parse_date("nope", label="since")


class InsidersCommandTests(unittest.TestCase):
    def test_requires_ticker_or_reporter_cik(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["insiders"])
        self.assertEqual(code, 2)

    def test_ticker_and_reporter_cik_mutually_exclusive(self) -> None:
        path = _watchlist_path(self)
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(
                [
                    "insiders",
                    "--ticker",
                    "AAPL",
                    "--reporter-cik",
                    "0000111111",
                    "--config",
                    str(path),
                ]
            )
        self.assertEqual(code, 2)

    def test_derivative_and_non_derivative_mutually_exclusive(self) -> None:
        path = _watchlist_path(self)
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(
                [
                    "insiders",
                    "--ticker",
                    "AAPL",
                    "--derivative",
                    "--non-derivative",
                    "--config",
                    str(path),
                ]
            )
        self.assertEqual(code, 2)

    def test_crypto_ticker_redirects_to_prices(self) -> None:
        path = _watchlist_path(self)
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["insiders", "--ticker", "BTC", "--config", str(path)])
        self.assertEqual(code, 2)
        self.assertIn("crypto", err.getvalue().lower())
        self.assertIn("genkei prices", err.getvalue())

    def test_equity_without_cik_rejected(self) -> None:
        path = _watchlist_path(self)
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["insiders", "--ticker", "NOCIK", "--config", str(path)])
        self.assertEqual(code, 2)
        self.assertIn("CIK", err.getvalue())

    def test_unknown_ticker_rejected(self) -> None:
        path = _watchlist_path(self)
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["insiders", "--ticker", "UNKNOWN", "--config", str(path)])
        self.assertEqual(code, 2)
        self.assertIn("UNKNOWN", err.getvalue())

    def test_issuer_mode_queries_with_resolved_cik(self) -> None:
        path = _watchlist_path(self)
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.insiders._query_by_issuer",
                return_value=[SAMPLE_ISSUER_ROW],
            ) as mocked,
            redirect_stdout(out),
        ):
            code = main(["insiders", "--ticker", "AAPL", "--config", str(path)])
        self.assertIn(code, (None, 0))
        # AAPL → CIK 0000320193 resolution
        self.assertEqual(mocked.call_args.args[0], "0000320193")
        self.assertIn("Borders Ben", out.getvalue())

    def test_reporter_mode_pads_cik_and_skips_watchlist(self) -> None:
        # Reporter-CIK lookup doesn't need the equities watchlist;
        # insider CIKs aren't always issuers.
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.insiders._query_by_reporter",
                return_value=[],
            ) as mocked,
            redirect_stdout(out),
        ):
            code = main(["insiders", "--reporter-cik", "2100523"])
        self.assertIn(code, (None, 0))
        self.assertEqual(mocked.call_args.args[0], "0002100523")  # zero-padded

    def test_since_after_until_rejected(self) -> None:
        path = _watchlist_path(self)
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(
                [
                    "insiders",
                    "--ticker",
                    "AAPL",
                    "--since",
                    "2024-06-01",
                    "--until",
                    "2024-01-01",
                    "--config",
                    str(path),
                ]
            )
        self.assertEqual(code, 2)

    def test_json_mode_serializes_decimal_to_string(self) -> None:
        path = _watchlist_path(self)
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.insiders._query_by_issuer",
                return_value=[SAMPLE_ISSUER_ROW],
            ),
            redirect_stdout(out),
        ):
            main(["insiders", "--ticker", "AAPL", "--json", "--config", str(path)])
        parsed = json_mod.loads(out.getvalue())
        # Decimal serialized as string to preserve precision (same convention
        # as filings --json after the precision-preserving commit).
        self.assertEqual(parsed[0]["shares"], "1274")
        self.assertEqual(parsed[0]["reporter_cik"], "0002100523")


if __name__ == "__main__":
    unittest.main()
