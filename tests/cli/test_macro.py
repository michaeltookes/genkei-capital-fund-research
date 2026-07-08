"""Unit tests for the `genkei macro` subcommand (B-042)."""

from __future__ import annotations

import io
import json as json_mod
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from genkei.cli import main
from genkei.cli.macro import (
    _annotate_with_regime,
    _format_human,
    _parse_date,
    _query_observations,
)

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

    def test_date_filters_bind_utc_datetime_bounds(self) -> None:
        _sql, params = self._capture_sql(
            since=date(2024, 6, 1),
            until=date(2024, 6, 30),
            as_of=None,
            all_vintages=False,
            limit=10,
        )
        self.assertIn(datetime(2024, 6, 1, 0, 0, tzinfo=timezone.utc), params)
        self.assertIn(
            datetime(2024, 6, 30, 23, 59, 59, 999999, tzinfo=timezone.utc),
            params,
        )


class AnnotateWithRegimeTests(unittest.TestCase):
    """B-066 — as-of regime annotation over the loaded regime calendar."""

    _CALENDAR = [
        (date(2026, 6, 28), "mixed"),
        (date(2026, 6, 29), "risk_on"),
        (date(2026, 7, 1), "risk_on"),
    ]

    def _annotate(self, rows: list[dict]) -> list[dict]:
        with patch(
            "genkei.cli.macro._load_regime_calendar", return_value=self._CALENDAR
        ):
            return _annotate_with_regime(rows)

    def test_exact_date_match(self) -> None:
        out = self._annotate([{"ts": "2026-06-29T00:00:00+00:00", "value": 4.4}])
        self.assertEqual(out[0]["regime"], "risk_on")
        self.assertEqual(out[0]["regime_as_of"], "2026-06-29")

    def test_carries_forward_when_no_row_that_day(self) -> None:
        # 07/02 has no calendar entry → uses the prevailing 07/01 regime.
        out = self._annotate([{"ts": "2026-07-02T00:00:00+00:00", "value": None}])
        self.assertEqual(out[0]["regime"], "risk_on")
        self.assertEqual(out[0]["regime_as_of"], "2026-07-01")

    def test_before_calendar_start_is_none(self) -> None:
        out = self._annotate([{"ts": "2026-06-01T00:00:00+00:00", "value": 4.0}])
        self.assertIsNone(out[0]["regime"])
        self.assertIsNone(out[0]["regime_as_of"])

    def test_row_without_ts_is_tolerated(self) -> None:
        out = self._annotate([{"ts": None, "value": None}])
        self.assertIsNone(out[0]["regime"])

    def test_empty_rows_short_circuit_without_db(self) -> None:
        # No dates → no calendar load at all (helper must not touch the DB).
        with patch("genkei.cli.macro._load_regime_calendar") as loader:
            out = _annotate_with_regime([])
        loader.assert_not_called()
        self.assertEqual(out, [])


class FormatWithRegimeTests(unittest.TestCase):
    def test_regime_column_renders_with_carry_forward_suffix(self) -> None:
        rows = [
            {
                "ts": "2026-07-02T00:00:00+00:00",
                "realtime_start": "2026-07-03",
                "realtime_end": "9999-12-31",
                "value": 4.48,
                "regime": "risk_on",
                "regime_as_of": "2026-07-01",
            }
        ]
        out = _format_human(
            "DGS10", rows, as_of=None, all_vintages=False, with_regime=True
        )
        self.assertIn("regime", out)
        # Carried-forward label flags the source date.
        self.assertIn("risk_on (as of 2026-07-01)", out)

    def test_no_regime_column_when_flag_off(self) -> None:
        rows = [
            {
                "ts": "2026-07-02T00:00:00+00:00",
                "realtime_start": "2026-07-03",
                "realtime_end": "9999-12-31",
                "value": 4.48,
            }
        ]
        out = _format_human("DGS10", rows, as_of=None, all_vintages=False)
        self.assertNotIn("regime", out)


class MacroRegimeFlagTests(unittest.TestCase):
    def test_regime_rejects_as_of_until_regime_labels_are_vintage_aware(self) -> None:
        path = _watchlist_path(self)
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(
                [
                    "macro",
                    "--series",
                    "DGS10",
                    "--config",
                    str(path),
                    "--as-of",
                    "2024-06-30",
                    "--regime",
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("vintage-aware", err.getvalue())

    def test_regime_rejects_all_vintages_until_regime_labels_are_vintage_aware(
        self,
    ) -> None:
        path = _watchlist_path(self)
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(
                [
                    "macro",
                    "--series",
                    "DGS10",
                    "--config",
                    str(path),
                    "--all-vintages",
                    "--regime",
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("vintage-aware", err.getvalue())

    def test_regime_flag_annotates_output(self) -> None:
        path = _watchlist_path(self)
        rows = [
            {
                "ts": "2026-07-01T00:00:00+00:00",
                "realtime_start": "2026-07-02",
                "realtime_end": "9999-12-31",
                "value": 4.49,
            }
        ]
        out = io.StringIO()
        with (
            patch("genkei.cli.macro._query_observations", return_value=rows),
            patch(
                "genkei.cli.macro._load_regime_calendar",
                return_value=[(date(2026, 7, 1), "risk_on")],
            ),
            redirect_stdout(out),
        ):
            code = main(
                ["macro", "--series", "DGS10", "--config", str(path), "--regime"]
            )
        self.assertIn(code, (None, 0))
        self.assertIn("risk_on", out.getvalue())

    def test_regime_flag_json_includes_regime_fields(self) -> None:
        path = _watchlist_path(self)
        rows = [
            {
                "ts": "2026-07-01T00:00:00+00:00",
                "realtime_start": "2026-07-02",
                "realtime_end": "9999-12-31",
                "value": 4.49,
            }
        ]
        out = io.StringIO()
        with (
            patch("genkei.cli.macro._query_observations", return_value=rows),
            patch(
                "genkei.cli.macro._load_regime_calendar",
                return_value=[(date(2026, 7, 1), "risk_on")],
            ),
            redirect_stdout(out),
        ):
            main(
                [
                    "macro",
                    "--series",
                    "DGS10",
                    "--config",
                    str(path),
                    "--regime",
                    "--json",
                ]
            )
        parsed = json_mod.loads(out.getvalue())
        self.assertEqual(parsed[0]["regime"], "risk_on")
        self.assertEqual(parsed[0]["regime_as_of"], "2026-07-01")


if __name__ == "__main__":
    unittest.main()
