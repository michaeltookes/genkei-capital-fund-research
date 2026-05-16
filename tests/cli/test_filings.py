"""Unit tests for the `genkei filings` subcommand (B-040)."""

from __future__ import annotations

import io
import json as json_mod
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from genkei.cli import main
from genkei.cli.filings import (
    _format_facts_human,
    _format_filings_human,
    _parse_date,
    _query_facts,
)

EQUITY_AND_CRYPTO_YAML = (
    "crypto:\n  primary:\n    - symbol: BTC\n      name: Bitcoin\n"
    "      coingecko_id: bitcoin\n"
    "equities:\n  primary:\n    - symbol: AAPL\n      name: Apple Inc.\n"
    '      cik: "0000320193"\n'
    "      sleeve: core\n"
    "    - symbol: NOCIK\n      name: Nocik Co.\n"
)


def _watchlist_path(case: unittest.TestCase, body: str = EQUITY_AND_CRYPTO_YAML) -> Path:
    ctx = TemporaryDirectory()
    case.addCleanup(ctx.cleanup)
    tmp = Path(ctx.name)
    path = tmp / "watchlists.yml"
    path.write_text(body, encoding="utf-8")
    return path


class FormatTests(unittest.TestCase):
    def test_no_filings_message_hints_at_widening_filters(self) -> None:
        out = _format_filings_human("AAPL", [])
        self.assertIn("AAPL", out)
        self.assertIn("No filings", out)
        self.assertIn("--since", out)

    def test_filings_table_shows_form_filed_accession(self) -> None:
        rows = [
            {
                "accession_number": "0000320193-24-000123",
                "form_type": "10-K",
                "filed_at": "2024-11-01",
                "report_date": "2024-09-28",
                "primary_document": "aapl-20240928.htm",
                "primary_doc_description": "10-K",
                "items": None,
                "is_xbrl": True,
            }
        ]
        out = _format_filings_human("AAPL", rows, horizon_tag="equity:core:primary")
        self.assertIn("AAPL", out)
        self.assertIn("horizon=equity:core:primary", out)
        self.assertIn("10-K", out)
        self.assertIn("2024-11-01", out)
        self.assertIn("0000320193-24-000123", out)

    def test_no_facts_message_mentions_unit_options(self) -> None:
        out = _format_facts_human("AAPL", "Revenues", [])
        self.assertIn("Revenues", out)
        self.assertIn("--unit", out)

    def test_facts_table_formats_value_with_commas(self) -> None:
        rows = [
            {
                "taxonomy": "us-gaap",
                "concept": "Revenues",
                "unit": "USD",
                "period_start": "2024-07-01",
                "period_end": "2024-09-28",
                "value": 94_930_000_000,
                "accession_number": "0000320193-24-000123",
                "form_type": "10-K",
                "filed_at": "2024-11-01",
                "fy": 2024,
                "fp": "Q4",
            }
        ]
        out = _format_facts_human("AAPL", "Revenues", rows, horizon_tag="equity:core:primary")
        self.assertIn("AAPL", out)
        self.assertIn("Revenues", out)
        self.assertIn("horizon=equity:core:primary", out)
        self.assertIn("94,930,000,000", out)

    def test_facts_table_preserves_fractional_values(self) -> None:
        rows = [
            {
                "taxonomy": "us-gaap",
                "concept": "EarningsPerShareDiluted",
                "unit": "USD/shares",
                "period_start": "2024-01-01",
                "period_end": "2024-12-31",
                "value": 6.37,
                "accession_number": "0000320193-24-000123",
                "form_type": "10-K",
                "filed_at": "2024-11-01",
                "fy": 2024,
                "fp": "FY",
            },
            {
                "taxonomy": "us-gaap",
                "concept": "OperatingMargin",
                "unit": "pure",
                "period_start": "2024-01-01",
                "period_end": "2024-12-31",
                "value": 0.315,
                "accession_number": "0000320193-24-000123",
                "form_type": "10-K",
                "filed_at": "2024-11-01",
                "fy": 2024,
                "fp": "FY",
            },
        ]
        out = _format_facts_human("AAPL", "EarningsPerShareDiluted", rows)
        self.assertIn("6.37", out)
        self.assertIn("0.315", out)
        self.assertNotIn(" 6  ", out)


class ParseDateTests(unittest.TestCase):
    def test_garbage_raises_typer_bad_parameter(self) -> None:
        import typer

        with self.assertRaises(typer.BadParameter):
            _parse_date("nope", label="since")


class FilingsCommandTests(unittest.TestCase):
    def test_unknown_ticker_friendly_error(self) -> None:
        path = _watchlist_path(self)
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["filings", "--ticker", "UNKNOWN", "--config", str(path)])
        self.assertEqual(code, 2)
        self.assertIn("UNKNOWN", err.getvalue())

    def test_crypto_ticker_redirects_to_prices(self) -> None:
        path = _watchlist_path(self)
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["filings", "--ticker", "BTC", "--config", str(path)])
        self.assertEqual(code, 2)
        self.assertIn("crypto", err.getvalue().lower())
        self.assertIn("genkei prices", err.getvalue())

    def test_equity_without_cik_rejected(self) -> None:
        path = _watchlist_path(self)
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["filings", "--ticker", "NOCIK", "--config", str(path)])
        self.assertEqual(code, 2)
        self.assertIn("CIK", err.getvalue())

    def test_filings_mode_queries_sec_filings(self) -> None:
        path = _watchlist_path(self)
        rows = [
            {
                "accession_number": "0000320193-24-000123",
                "form_type": "10-K",
                "filed_at": "2024-11-01",
                "report_date": "2024-09-28",
                "primary_document": "aapl-20240928.htm",
                "primary_doc_description": "10-K",
                "items": None,
                "is_xbrl": True,
            }
        ]
        out = io.StringIO()
        with (
            patch("genkei.cli.filings._query_filings", return_value=rows) as mocked,
            redirect_stdout(out),
        ):
            code = main(["filings", "--ticker", "AAPL", "--config", str(path)])
        self.assertIn(code, (None, 0))
        self.assertEqual(mocked.call_args.args[0], "0000320193")
        self.assertIn("10-K", out.getvalue())

    def test_concept_mode_switches_to_sec_facts(self) -> None:
        path = _watchlist_path(self)
        rows = [
            {
                "taxonomy": "us-gaap",
                "concept": "Revenues",
                "unit": "USD",
                "period_start": "2024-07-01",
                "period_end": "2024-09-28",
                "value": 94_930_000_000,
                "accession_number": "0000320193-24-000123",
                "form_type": "10-K",
                "filed_at": "2024-11-01",
                "fy": 2024,
                "fp": "Q4",
            }
        ]
        out = io.StringIO()
        with (
            patch("genkei.cli.filings._query_facts", return_value=rows) as mocked,
            redirect_stdout(out),
        ):
            code = main(
                [
                    "filings",
                    "--ticker",
                    "AAPL",
                    "--concept",
                    "us-gaap:Revenues",
                    "--unit",
                    "USD",
                    "--config",
                    str(path),
                ]
            )
        self.assertIn(code, (None, 0))
        self.assertEqual(mocked.call_args.kwargs["concept"], "us-gaap:Revenues")
        self.assertIsNone(mocked.call_args.kwargs["form"])
        self.assertEqual(mocked.call_args.kwargs["unit"], "USD")
        self.assertIn("horizon=equity:core:primary", out.getvalue())
        self.assertIn("94,930,000,000", out.getvalue())

    def test_concept_mode_passes_form_filter_to_sec_facts(self) -> None:
        path = _watchlist_path(self)
        out = io.StringIO()
        with (
            patch("genkei.cli.filings._query_facts", return_value=[]) as mocked,
            redirect_stdout(out),
        ):
            code = main(
                [
                    "filings",
                    "--ticker",
                    "AAPL",
                    "--concept",
                    "Revenues",
                    "--form",
                    "10-K",
                    "--config",
                    str(path),
                ]
            )
        self.assertIn(code, (None, 0))
        self.assertEqual(mocked.call_args.kwargs["form"], "10-K")

    def test_unit_without_concept_rejected(self) -> None:
        path = _watchlist_path(self)
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(
                ["filings", "--ticker", "AAPL", "--unit", "USD", "--config", str(path)]
            )
        self.assertEqual(code, 2)

    def test_since_after_until_rejected(self) -> None:
        path = _watchlist_path(self)
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(
                [
                    "filings",
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

    def test_json_filings_emits_valid_array(self) -> None:
        path = _watchlist_path(self)
        rows = [
            {
                "accession_number": "x",
                "form_type": "10-K",
                "filed_at": "2024-01-01",
                "report_date": None,
                "primary_document": None,
                "primary_doc_description": None,
                "items": None,
                "is_xbrl": True,
            }
        ]
        out = io.StringIO()
        with (
            patch("genkei.cli.filings._query_filings", return_value=rows),
            redirect_stdout(out),
        ):
            main(["filings", "--ticker", "AAPL", "--config", str(path), "--json"])
        parsed = json_mod.loads(out.getvalue())
        self.assertEqual(parsed[0]["form_type"], "10-K")
        self.assertEqual(parsed[0]["horizon_tag"], "equity:core:primary")

    def test_json_facts_emits_horizon_tag(self) -> None:
        path = _watchlist_path(self)
        rows = [
            {
                "taxonomy": "us-gaap",
                "concept": "Revenues",
                "unit": "USD",
                "period_start": "2024-07-01",
                "period_end": "2024-09-28",
                "value": 94_930_000_000,
                "accession_number": "0000320193-24-000123",
                "form_type": "10-K",
                "filed_at": "2024-11-01",
                "fy": 2024,
                "fp": "Q4",
            }
        ]
        out = io.StringIO()
        with (
            patch("genkei.cli.filings._query_facts", return_value=rows),
            redirect_stdout(out),
        ):
            main(
                [
                    "filings",
                    "--ticker",
                    "AAPL",
                    "--concept",
                    "Revenues",
                    "--config",
                    str(path),
                    "--json",
                ]
            )
        parsed = json_mod.loads(out.getvalue())
        self.assertEqual(parsed[0]["horizon_tag"], "equity:core:primary")


class QueryFactsSqlShapeTests(unittest.TestCase):
    def test_form_filter_is_applied_in_concept_mode(self) -> None:
        captured = {}

        class FakeCursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params):
                captured["sql"] = sql
                captured["params"] = list(params)

            def fetchall(self):
                return []

        class FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return FakeCursor()

        with patch("genkei.cli.filings.db.connection", return_value=FakeConn()):
            _query_facts(
                "0000320193",
                concept="Revenues",
                form="10-K",
                unit=None,
                since=None,
                until=None,
                limit=10,
            )

        self.assertIn("form_type = %s", captured["sql"])
        self.assertIn("10-K", captured["params"])


if __name__ == "__main__":
    unittest.main()
