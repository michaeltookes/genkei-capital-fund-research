"""Unit tests for `genkei holdings` (B-080)."""

from __future__ import annotations

import io
import json as json_mod
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import typer

from genkei.cli import main
from genkei.cli.holdings import (
    _format_cusip_human,
    _format_filer_human,
    _format_summary_human,
    _resolve_filer,
)
from genkei.common.watchlist import FilerEntry, Watchlist


# Bare-minimum watchlist exercising every find_filer path: by name,
# by CIK (bare digits), by CIK (zero-padded).
WATCHLIST_YAML = (
    "filers:\n"
    "  primary:\n"
    "    - cik: 1067983\n"
    "      name: Berkshire Hathaway Inc\n"
    "    - cik: 1418814\n"
    "      name: ValueAct Capital Management LP\n"
)


def _watchlist_path(case: unittest.TestCase) -> Path:
    ctx = TemporaryDirectory()
    case.addCleanup(ctx.cleanup)
    tmp = Path(ctx.name)
    path = tmp / "watchlists.yml"
    path.write_text(WATCHLIST_YAML, encoding="utf-8")
    return path


class ResolveFilerTests(unittest.TestCase):
    def _watchlist(self) -> Watchlist:
        return Watchlist(
            crypto=[],
            equities=[],
            macro=[],
            protocols=[],
            filers=[
                FilerEntry(
                    filer_cik="0001067983",
                    name="Berkshire Hathaway Inc",
                    tier="primary",
                ),
            ],
        )

    def test_resolves_by_zero_padded_cik(self) -> None:
        cik, entry = _resolve_filer("0001067983", self._watchlist())
        self.assertEqual(cik, "0001067983")
        assert entry is not None
        self.assertEqual(entry.name, "Berkshire Hathaway Inc")

    def test_resolves_by_bare_cik(self) -> None:
        # Auto-padding to 10 digits is the documented contract.
        cik, entry = _resolve_filer("1067983", self._watchlist())
        self.assertEqual(cik, "0001067983")
        assert entry is not None
        self.assertEqual(entry.name, "Berkshire Hathaway Inc")

    def test_resolves_by_exact_name(self) -> None:
        cik, entry = _resolve_filer("Berkshire Hathaway Inc", self._watchlist())
        self.assertEqual(cik, "0001067983")
        assert entry is not None

    def test_unknown_name_raises_bad_param(self) -> None:
        with self.assertRaises(typer.BadParameter):
            _resolve_filer("UnknownFundCo", self._watchlist())

    def test_unknown_cik_returns_cik_with_none_entry(self) -> None:
        # Querying historical data for a filer we no longer track is a
        # valid use case — return the CIK + None entry so the caller can
        # render results without a watchlist label.
        cik, entry = _resolve_filer("9999999999", self._watchlist())
        self.assertEqual(cik, "9999999999")
        self.assertIsNone(entry)


class FormatHumanTests(unittest.TestCase):
    def test_empty_filer_view_points_at_health_check(self) -> None:
        text = _format_filer_human(
            filer_label="Berkshire Hathaway Inc",
            filer_cik="0001067983",
            period_label="latest period 2025-03-31",
            rows=[],
        )
        self.assertIn("No 13F holdings", text)
        self.assertIn("watchlist health", text)

    def test_filer_view_renders_value_with_dollar_sign_and_commas(self) -> None:
        rows = [
            {
                "period_of_report": "2025-03-31",
                "cusip": "037833100",
                "issuer_name": "APPLE INC",
                "class_title": "COM",
                "value_usd": Decimal("42000000000"),
                "shares_or_principal": Decimal("200000000"),
                "shares_or_principal_type": "SH",
                "put_call": None,
                "investment_discretion": "SOLE",
                "accession_number": "0001067983-25-000001",
            }
        ]
        text = _format_filer_human(
            filer_label="Berkshire Hathaway Inc",
            filer_cik="0001067983",
            period_label="latest period 2025-03-31",
            rows=rows,
        )
        # Dollar sign + comma grouping for the value column.
        self.assertIn("42,000,000,000", text)
        self.assertIn("200,000,000", text)
        self.assertIn("APPLE INC", text)

    def test_cusip_view_shows_filer_name_and_discretion(self) -> None:
        rows = [
            {
                "period_of_report": "2025-03-31",
                "cusip": "037833100",
                "issuer_name": "APPLE INC",
                "class_title": "COM",
                "value_usd": Decimal("42000000000"),
                "shares_or_principal": Decimal("200000000"),
                "shares_or_principal_type": "SH",
                "put_call": None,
                "investment_discretion": "SOLE",
                "accession_number": "0001067983-25-000001",
                "filer_cik": "0001067983",
                "filer_name": "Berkshire Hathaway Inc",
            }
        ]
        text = _format_cusip_human(
            cusip="037833100", period_label="latest period 2025-03-31", rows=rows
        )
        self.assertIn("Berkshire Hathaway Inc", text)
        self.assertIn("SOLE", text)

    def test_summary_view_renders_each_filer_row(self) -> None:
        rows = [
            {
                "filer_cik": "0001067983",
                "filer_name": "Berkshire Hathaway Inc",
                "latest_period": "2025-03-31",
                "holdings_count": 41,
                "total_value_usd": Decimal("300000000000"),
            },
            {
                "filer_cik": "0001418814",
                "filer_name": "ValueAct Capital Management LP",
                "latest_period": "2025-03-31",
                "holdings_count": 9,
                "total_value_usd": Decimal("4500000000"),
            },
        ]
        text = _format_summary_human(rows)
        self.assertIn("Berkshire Hathaway Inc", text)
        self.assertIn("ValueAct Capital Management LP", text)
        self.assertIn("300,000,000,000", text)


class CommandValidationTests(unittest.TestCase):
    def test_filer_and_cusip_mutually_exclusive(self) -> None:
        wpath = _watchlist_path(self)
        # main() catches typer's SystemExit and returns the exit code;
        # BadParameter renders as a UsageError on stderr with exit 2.
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = main(
                [
                    "holdings",
                    "--filer",
                    "Berkshire Hathaway Inc",
                    "--cusip",
                    "037833100",
                    "--config",
                    str(wpath),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("mutually exclusive", buf.getvalue())

    def test_since_after_until_rejected(self) -> None:
        wpath = _watchlist_path(self)
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = main(
                [
                    "holdings",
                    "--filer-cik",
                    "1067983",
                    "--since",
                    "2025-02-01",
                    "--until",
                    "2024-12-31",
                    "--config",
                    str(wpath),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("--since", buf.getvalue())

    def test_period_and_all_periods_rejected(self) -> None:
        wpath = _watchlist_path(self)
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = main(
                [
                    "holdings",
                    "--filer-cik",
                    "1067983",
                    "--period",
                    "2025-03-31",
                    "--all-periods",
                    "--config",
                    str(wpath),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("mutually exclusive", buf.getvalue())


class FilerQueryEndToEndTests(unittest.TestCase):
    """Mock the DB layer; exercise the typer command path + JSON shape."""

    SAMPLE_ROW = {
        "period_of_report": "2025-03-31",
        "cusip": "037833100",
        "issuer_name": "APPLE INC",
        "class_title": "COM",
        "value_usd": Decimal("42000000000"),
        "shares_or_principal": Decimal("200000000"),
        "shares_or_principal_type": "SH",
        "put_call": None,
        "investment_discretion": "SOLE",
        "accession_number": "0001067983-25-000001",
    }

    def test_filer_view_emits_json_with_decimal_as_string(self) -> None:
        wpath = _watchlist_path(self)
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.holdings._latest_period_for_filer",
                return_value=date(2025, 3, 31),
            ),
            patch(
                "genkei.cli.holdings._query_by_filer",
                return_value=[self.SAMPLE_ROW],
            ),
            redirect_stdout(out),
        ):
            exit_code = main(
                [
                    "holdings",
                    "--filer-cik",
                    "1067983",
                    "--json",
                    "--config",
                    str(wpath),
                ]
            )
        self.assertEqual(exit_code, 0)
        parsed = json_mod.loads(out.getvalue())
        self.assertEqual(len(parsed), 1)
        # Decimal serialized as string (preserves precision; matches the
        # B-079 fix that other subcommands follow).
        self.assertEqual(parsed[0]["value_usd"], "42000000000")
        self.assertEqual(parsed[0]["cusip"], "037833100")


if __name__ == "__main__":
    unittest.main()
