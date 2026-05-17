"""Unit tests for `genkei query` (B-045)."""

from __future__ import annotations

import io
import json as json_mod
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import typer
from psycopg.errors import QueryCanceled, ReadOnlySqlTransaction
from psycopg.errors import SyntaxError as PgSyntaxError

from genkei.cli import main
from genkei.cli.query import (
    DEFAULT_LIMIT,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_LIMIT,
    MAX_TIMEOUT_SECONDS,
    _strip_trailing_semicolons,
    _validate_sql,
    find_multi_statement_position,
    format_csv,
    format_json,
    format_table,
    wrap_query_for_safety,
)

# ---------------------------------------------------------------------------
# Pure SQL transform / validator tests
# ---------------------------------------------------------------------------


class StripTrailingSemicolonsTests(unittest.TestCase):
    def test_strips_single_trailing_semicolon(self) -> None:
        self.assertEqual(_strip_trailing_semicolons("SELECT 1;"), "SELECT 1")

    def test_strips_whitespace_then_semicolons_then_whitespace(self) -> None:
        # Common shape: "SELECT 1;\n" (file w/ trailing newline).
        self.assertEqual(
            _strip_trailing_semicolons("SELECT 1;\n"), "SELECT 1"
        )
        # Multiple trailing ; in one batch also strip cleanly.
        self.assertEqual(
            _strip_trailing_semicolons("SELECT 1;;;"), "SELECT 1"
        )

    def test_leaves_internal_semicolons(self) -> None:
        # Trailing-only stripper — internal `;` are caught separately by
        # the multi-statement detector.
        self.assertEqual(
            _strip_trailing_semicolons("SELECT 1; SELECT 2"),
            "SELECT 1; SELECT 2",
        )


class FindMultiStatementPositionTests(unittest.TestCase):
    def test_no_semicolon_returns_none(self) -> None:
        self.assertIsNone(find_multi_statement_position("SELECT 1"))

    def test_unquoted_semicolon_flagged(self) -> None:
        # Position of the first ; in "SELECT 1; SELECT 2"
        pos = find_multi_statement_position("SELECT 1; SELECT 2")
        self.assertEqual(pos, 8)

    def test_semicolon_inside_single_quoted_string_ignored(self) -> None:
        # WHERE col = 'a;b' should not flag — the ; is part of the literal.
        self.assertIsNone(
            find_multi_statement_position("SELECT * FROM t WHERE col = 'a;b'")
        )

    def test_semicolon_inside_quoted_identifier_ignored(self) -> None:
        self.assertIsNone(find_multi_statement_position('SELECT 1 AS "a;b"'))

    def test_semicolon_inside_escaped_quoted_identifier_ignored(self) -> None:
        self.assertIsNone(find_multi_statement_position('SELECT 1 AS "a"";b"'))

    def test_semicolon_after_quoted_identifier_close_caught(self) -> None:
        pos = find_multi_statement_position('SELECT 1 AS "a;b"; SELECT 2')
        self.assertEqual(pos, 17)

    def test_semicolon_inside_dollar_quoted_string_ignored(self) -> None:
        self.assertIsNone(find_multi_statement_position("SELECT $$a;b$$"))

    def test_semicolon_inside_tagged_dollar_quoted_string_ignored(self) -> None:
        self.assertIsNone(find_multi_statement_position("SELECT $tag$a;b$tag$"))

    def test_semicolon_inside_line_comment_ignored(self) -> None:
        self.assertIsNone(find_multi_statement_position("SELECT 1 -- a;b"))

    def test_semicolon_inside_block_comment_ignored(self) -> None:
        self.assertIsNone(find_multi_statement_position("SELECT /* a;b */ 1"))

    def test_semicolon_inside_nested_block_comment_ignored(self) -> None:
        self.assertIsNone(find_multi_statement_position("SELECT /* /* a;b */ */ 1"))

    def test_semicolon_after_dollar_quote_close_caught(self) -> None:
        pos = find_multi_statement_position("SELECT $$a;b$$; SELECT 2")
        self.assertEqual(pos, 14)

    def test_semicolon_after_block_comment_close_caught(self) -> None:
        pos = find_multi_statement_position("SELECT /* a;b */ 1; SELECT 2")
        self.assertEqual(pos, 18)

    def test_escaped_quote_inside_string_handled(self) -> None:
        # Postgres escapes ' inside a string by doubling it. The detector
        # must not treat '' as ending the string.
        self.assertIsNone(
            find_multi_statement_position(
                "SELECT * FROM t WHERE col = 'O''Brien;was;here'"
            )
        )

    def test_escaped_quote_inside_escape_string_handled(self) -> None:
        self.assertIsNone(
            find_multi_statement_position("SELECT E'O\\';Brien;was;here'")
        )

    def test_semicolon_after_string_close_caught(self) -> None:
        # WHERE col = 'a'; SELECT 2 — the ; after the close-quote is real.
        pos = find_multi_statement_position("SELECT * FROM t WHERE col = 'a'; SELECT 2")
        self.assertIsNotNone(pos)


class WrapQueryForSafetyTests(unittest.TestCase):
    def test_wraps_simple_select(self) -> None:
        wrapped = wrap_query_for_safety("SELECT 1", limit=5)
        # The user's SQL is wrapped in an outer LIMIT subquery
        self.assertIn("SELECT 1", wrapped)
        self.assertIn("LIMIT 5", wrapped)
        self.assertTrue(wrapped.startswith("SELECT * FROM ("))

    def test_strips_trailing_semicolon_before_wrap(self) -> None:
        # An unstripped trailing ; would make the wrap a syntax error.
        wrapped = wrap_query_for_safety("SELECT 1;", limit=5)
        self.assertNotIn(";)", wrapped)
        self.assertNotIn("; )", wrapped)


class ValidateSqlTests(unittest.TestCase):
    def test_semicolon_only_sql_rejected_as_empty(self) -> None:
        with self.assertRaisesRegex(typer.BadParameter, "SQL is empty"):
            _validate_sql(" ;;; ")


# ---------------------------------------------------------------------------
# Output formatter tests
# ---------------------------------------------------------------------------


class FormatTableTests(unittest.TestCase):
    def test_renders_header_separator_and_rows(self) -> None:
        out = format_table(
            ["ticker", "shares"], [(1, 2), (3, 4)], limit=10, capped=False
        )
        lines = out.splitlines()
        self.assertIn("ticker", lines[0])
        self.assertIn("shares", lines[0])
        # Separator is dashes per column; with multi-char headers we
        # expect multi-dash runs.
        self.assertIn("--", lines[1])
        self.assertTrue(any("1" in line and "2" in line for line in lines))

    def test_empty_rows_returns_zero_row_note(self) -> None:
        out = format_table(["a", "b"], [], limit=10, capped=False)
        self.assertIn("0 rows", out)

    def test_capped_marker_appears(self) -> None:
        out = format_table(["a"], [(1,)], limit=1, capped=True)
        self.assertIn("(row cap)", out)

    def test_no_cap_marker_when_under_limit(self) -> None:
        out = format_table(["a"], [(1,)], limit=10, capped=False)
        self.assertNotIn("(row cap)", out)


class FormatJsonTests(unittest.TestCase):
    def test_emits_list_of_dicts_with_decimal_as_string(self) -> None:
        out = format_json(
            ["price", "ts"],
            [(Decimal("123.456789"), datetime(2026, 5, 1, tzinfo=timezone.utc))],
        )
        parsed = json_mod.loads(out)
        self.assertEqual(parsed[0]["price"], "123.456789")
        self.assertIn("2026-05-01", parsed[0]["ts"])

    def test_none_serializes_to_null(self) -> None:
        out = format_json(["a"], [(None,)])
        self.assertEqual(json_mod.loads(out), [{"a": None}])


class FormatCsvTests(unittest.TestCase):
    def test_emits_header_and_rows(self) -> None:
        out = format_csv(
            ["ticker", "shares"],
            [("AAPL", Decimal("100")), ("MSFT", Decimal("200"))],
        )
        lines = out.splitlines()
        self.assertEqual(lines[0], "ticker,shares")
        self.assertEqual(lines[1], "AAPL,100")
        self.assertEqual(lines[2], "MSFT,200")

    def test_renders_dates_isoformat_and_none_as_empty(self) -> None:
        out = format_csv(["d", "x"], [(date(2026, 5, 1), None)])
        self.assertIn("2026-05-01,", out)
        self.assertTrue(out.endswith(",") or out.splitlines()[-1].endswith(","))

    def test_csv_quotes_embedded_commas(self) -> None:
        out = format_csv(["name"], [("Smith, Joe",)])
        # csv module's QUOTE_MINIMAL → "Smith, Joe"
        self.assertIn('"Smith, Joe"', out)


# ---------------------------------------------------------------------------
# CLI behavior tests (execution path mocked)
# ---------------------------------------------------------------------------


class CliArgumentTests(unittest.TestCase):
    def test_no_sql_and_no_file_errors(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["query"])
        self.assertEqual(code, 2)

    def test_both_sql_and_file_errors(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "q.sql"
            path.write_text("SELECT 1", encoding="utf-8")
            err = io.StringIO()
            with redirect_stderr(err):
                code = main(["query", "SELECT 1", "--file", str(path)])
            self.assertEqual(code, 2)

    def test_missing_file_errors(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["query", "--file", "/no/such/path.sql"])
        self.assertEqual(code, 2)

    def test_limit_above_max_rejected(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["query", "SELECT 1", "--limit", str(MAX_LIMIT + 1)])
        self.assertEqual(code, 2)

    def test_timeout_above_max_rejected(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(
                ["query", "SELECT 1", "--timeout-seconds", str(MAX_TIMEOUT_SECONDS + 1)]
            )
        self.assertEqual(code, 2)

    def test_unknown_format_rejected(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["query", "SELECT 1", "--format", "xml"])
        self.assertEqual(code, 2)

    def test_multi_statement_input_rejected(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["query", "SELECT 1; SELECT 2"])
        self.assertEqual(code, 2)

    def test_empty_sql_rejected(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["query", "   "])
        self.assertEqual(code, 2)

    def test_semicolon_only_sql_rejected(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["query", " ;;; "])
        self.assertEqual(code, 2)


class CliExecutionTests(unittest.TestCase):
    def test_default_format_renders_table(self) -> None:
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.query.execute_readonly",
                return_value=(["n"], [(1,), (2,)]),
            ) as mocked,
            redirect_stdout(out),
        ):
            code = main(["query", "SELECT generate_series(1,2) AS n"])
        self.assertIn(code, (None, 0))
        # Over-fetch one row so the command can detect capping precisely.
        self.assertEqual(mocked.call_args.kwargs["limit"], DEFAULT_LIMIT + 1)
        self.assertEqual(
            mocked.call_args.kwargs["timeout_seconds"], DEFAULT_TIMEOUT_SECONDS
        )
        self.assertIn("n", out.getvalue())  # header
        self.assertIn("1", out.getvalue())

    def test_json_flag_emits_json(self) -> None:
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.query.execute_readonly",
                return_value=(["x"], [(Decimal("42"),)]),
            ),
            redirect_stdout(out),
        ):
            main(["query", "SELECT 42 AS x", "--json"])
        parsed = json_mod.loads(out.getvalue())
        self.assertEqual(parsed, [{"x": "42"}])

    def test_json_output_trims_overfetch_row(self) -> None:
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.query.execute_readonly",
                return_value=(["x"], [(1,), (2,), (3,)]),
            ),
            redirect_stdout(out),
        ):
            main(["query", "SELECT x FROM t", "--json", "--limit", "2"])
        parsed = json_mod.loads(out.getvalue())
        self.assertEqual(parsed, [{"x": 1}, {"x": 2}])

    def test_format_csv_emits_csv(self) -> None:
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.query.execute_readonly",
                return_value=(["x", "y"], [(1, 2)]),
            ),
            redirect_stdout(out),
        ):
            main(["query", "SELECT 1 AS x, 2 AS y", "--format", "csv"])
        lines = out.getvalue().splitlines()
        self.assertEqual(lines[0], "x,y")
        self.assertEqual(lines[1], "1,2")

    def test_file_input_path(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "q.sql"
            path.write_text("SELECT 7 AS lucky", encoding="utf-8")
            out = io.StringIO()
            with (
                patch(
                    "genkei.cli.query.execute_readonly",
                    return_value=(["lucky"], [(7,)]),
                ) as mocked,
                redirect_stdout(out),
            ):
                main(["query", "--file", str(path), "--json"])
            # The SQL passed downstream must match what we read from disk
            self.assertEqual(mocked.call_args.args[0], "SELECT 7 AS lucky")

    def test_readonly_violation_renders_clean_error_line(self) -> None:
        err = io.StringIO()
        with (
            patch(
                "genkei.cli.query.execute_readonly",
                side_effect=ReadOnlySqlTransaction(
                    "cannot execute INSERT in a read-only transaction"
                ),
            ),
            redirect_stderr(err),
        ):
            code = main(["query", "INSERT INTO t VALUES (1)"])
        self.assertEqual(code, 1)
        self.assertIn("ReadOnlySqlTransaction", err.getvalue())
        # No traceback — just the one-line error
        self.assertNotIn("Traceback", err.getvalue())

    def test_query_canceled_renders_clean_error(self) -> None:
        err = io.StringIO()
        with (
            patch(
                "genkei.cli.query.execute_readonly",
                side_effect=QueryCanceled("canceling statement due to statement timeout"),
            ),
            redirect_stderr(err),
        ):
            code = main(["query", "SELECT pg_sleep(60)"])
        self.assertEqual(code, 1)
        self.assertIn("QueryCanceled", err.getvalue())

    def test_syntax_error_renders_clean_error(self) -> None:
        err = io.StringIO()
        with (
            patch(
                "genkei.cli.query.execute_readonly",
                side_effect=PgSyntaxError("syntax error at or near \"SELEC\""),
            ),
            redirect_stderr(err),
        ):
            code = main(["query", "SELEC 1"])
        self.assertEqual(code, 1)
        self.assertIn("SyntaxError", err.getvalue())

    def test_capped_marker_shown_in_table_when_result_exceeds_limit(self) -> None:
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.query.execute_readonly",
                return_value=(["x"], [(i,) for i in range(6)]),
            ),
            redirect_stdout(out),
        ):
            main(["query", "SELECT * FROM t", "--limit", "5"])
        self.assertIn("(row cap)", out.getvalue())

    def test_capped_marker_not_shown_when_result_exactly_hits_limit(self) -> None:
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.query.execute_readonly",
                return_value=(["x"], [(i,) for i in range(5)]),
            ),
            redirect_stdout(out),
        ):
            main(["query", "SELECT * FROM t", "--limit", "5"])
        self.assertNotIn("(row cap)", out.getvalue())


if __name__ == "__main__":
    unittest.main()
