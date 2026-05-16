"""Unit tests for the `genkei macro` subcommand (B-042)."""

from __future__ import annotations

import io
import json as json_mod
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from genkei.cli import main
from genkei.cli.macro import _format_human, _parse_date, _query_observations

MACRO_YAML = (
    "macro_series:\n"
    "  - id: DGS10\n    name: 10Y Treasury\n"
    "    sleeve: cross-sleeve\n"
    "  - id: CPIAUCSL\n    name: CPI\n"
)


def _watchlist_path(case: unittest.TestCase, body: str = MACRO_YAML) -> Path:
    ctx = TemporaryDirectory()
    case.addCleanup(ctx.cleanup)
    tmp = Path(ctx.name)
    path = tmp / "watchlists.yml"
    path.write_text(body, encoding="utf-8")
    return path


class FormatTests(unittest.TestCase):
    def test_no_rows_default_message(self) -> None:
        out = _format_human("DGS10", [], as_of=None, all_vintages=False)
        self.assertIn("DGS10", out)
        self.assertIn("No observations", out)
        self.assertIn("watchlist", out)

    def test_no_rows_with_as_of_mentions_as_of(self) -> None:
        out = _format_human(
            "DGS10", [], as_of=date(2024, 1, 1), all_vintages=False
        )
        self.assertIn("--as-of", out)

    def test_table_tags_latest_vintage_by_default(self) -> None:
        rows = [
            {
                "ts": "2024-06-14T00:00:00+00:00",
                "realtime_start": "2024-06-15",
                "realtime_end": "9999-12-31",
                "value": 4.2543,
            }
        ]
        out = _format_human(
            "DGS10",
            rows,
            as_of=None,
            all_vintages=False,
            horizon_tag="macro:cross-sleeve:primary",
        )
        self.assertIn("latest-vintage", out)
        self.assertIn("horizon=macro:cross-sleeve:primary", out)
        self.assertIn("4.2543", out)

    def test_table_tags_as_of_when_set(self) -> None:
        rows = [
            {
                "ts": "2024-06-14T00:00:00+00:00",
                "realtime_start": "2024-06-15",
                "realtime_end": "9999-12-31",
                "value": 4.2543,
            }
        ]
        out = _format_human(
            "DGS10",
            rows,
            as_of=date(2024, 6, 30),
            all_vintages=False,
            horizon_tag="macro:cross-sleeve:primary",
        )
        self.assertIn("as-of 2024-06-30", out)


class ParseDateTests(unittest.TestCase):
    def test_garbage_raises(self) -> None:
        import typer

        with self.assertRaises(typer.BadParameter):
            _parse_date("nope", label="since")


class MacroCommandTests(unittest.TestCase):
    def test_unknown_series_friendly_error(self) -> None:
        path = _watchlist_path(self)
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["macro", "--series", "UNKNOWN", "--config", str(path)])
        self.assertEqual(code, 2)
        self.assertIn("UNKNOWN", err.getvalue())
        self.assertIn("watchlist", err.getvalue())

    def test_known_series_queries_observations(self) -> None:
        path = _watchlist_path(self)
        rows = [
            {
                "ts": "2024-06-14T00:00:00+00:00",
                "realtime_start": "2024-06-15",
                "realtime_end": "9999-12-31",
                "value": 4.2543,
            }
        ]
        out = io.StringIO()
        with (
            patch("genkei.cli.macro._query_observations", return_value=rows) as mocked,
            redirect_stdout(out),
        ):
            code = main(["macro", "--series", "DGS10", "--config", str(path)])
        self.assertIn(code, (None, 0))
        self.assertEqual(mocked.call_args.args[0], "DGS10")
        self.assertIn("DGS10", out.getvalue())
        self.assertIn("horizon=macro:cross-sleeve:primary", out.getvalue())
        self.assertIn("4.2543", out.getvalue())

    def test_all_vintages_and_as_of_mutually_exclusive(self) -> None:
        path = _watchlist_path(self)
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(
                [
                    "macro",
                    "--series",
                    "DGS10",
                    "--as-of",
                    "2024-06-01",
                    "--all-vintages",
                    "--config",
                    str(path),
                ]
            )
        self.assertEqual(code, 2)

    def test_since_after_until_rejected(self) -> None:
        path = _watchlist_path(self)
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(
                [
                    "macro",
                    "--series",
                    "DGS10",
                    "--since",
                    "2024-06-01",
                    "--until",
                    "2024-01-01",
                    "--config",
                    str(path),
                ]
            )
        self.assertEqual(code, 2)

    def test_json_mode_emits_valid_array(self) -> None:
        path = _watchlist_path(self)
        rows = [
            {
                "ts": "2024-06-14T00:00:00+00:00",
                "realtime_start": "2024-06-15",
                "realtime_end": "9999-12-31",
                "value": 4.2543,
            }
        ]
        out = io.StringIO()
        with (
            patch("genkei.cli.macro._query_observations", return_value=rows),
            redirect_stdout(out),
        ):
            main(["macro", "--series", "DGS10", "--config", str(path), "--json"])
        parsed = json_mod.loads(out.getvalue())
        self.assertEqual(parsed[0]["value"], 4.2543)
        self.assertEqual(parsed[0]["horizon_tag"], "macro:cross-sleeve:primary")


class QueryObservationsSqlShapeTests(unittest.TestCase):
    """Verify the SQL the helper builds without hitting Postgres."""

    def _capture_sql(self, **kwargs) -> tuple[str, list]:
        captured: dict = {}

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

        with patch("genkei.cli.macro.db.connection", return_value=FakeConn()):
            _query_observations("DGS10", **kwargs)
        return captured["sql"], captured["params"]

    def test_latest_vintage_uses_distinct_on(self) -> None:
        sql, _ = self._capture_sql(
            since=None, until=None, as_of=None, all_vintages=False, limit=10
        )
        self.assertIn("DISTINCT ON (ts)", sql)

    def test_all_vintages_skips_distinct_on(self) -> None:
        sql, _ = self._capture_sql(
            since=None, until=None, as_of=None, all_vintages=True, limit=10
        )
        self.assertNotIn("DISTINCT ON", sql)

    def test_as_of_filters_realtime_start(self) -> None:
        sql, params = self._capture_sql(
            since=None,
            until=None,
            as_of=date(2024, 6, 30),
            all_vintages=False,
            limit=10,
        )
        self.assertIn("realtime_start <= %s", sql)
        self.assertIn(date(2024, 6, 30), params)


if __name__ == "__main__":
    unittest.main()
