"""Unit tests for the `genkei momentum` subcommand (B-067)."""

from __future__ import annotations

import io
import json as json_mod
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from genkei.cli import main
from genkei.cli.momentum import _format_human


class _FakeCursor:
    def __init__(self, store: dict, rows: list) -> None:
        self._store = store
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self._store["sql"] = sql
        self._store["params"] = list(params)

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, store: dict, rows: list) -> None:
        self._store = store
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._store, self._rows)


_ROW = (
    "PYTH",
    "crypto",
    date(2026, 7, 12),
    Decimal("0.0486"),
    Decimal("2.32"),
    Decimal("7.28"),
    Decimal("27.89"),
)


class MomentumCommandTests(unittest.TestCase):
    def _run(self, args: list[str], rows: list = (_ROW,)) -> tuple[str, dict]:
        store: dict = {}
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.momentum.db.connection",
                return_value=_FakeConn(store, list(rows)),
            ),
            redirect_stdout(out),
        ):
            main(["momentum", *args])
        return out.getvalue(), store

    def test_default_human_output(self) -> None:
        text, _ = self._run([])
        self.assertIn("PYTH", text)
        self.assertIn("+27.89%", text)
        self.assertIn("Price momentum", text)

    def test_window_selects_sort_column(self) -> None:
        _, store = self._run(["--window", "30"])
        self.assertIn("ret_30d DESC NULLS LAST", store["sql"])

    def test_default_window_is_7d(self) -> None:
        _, store = self._run([])
        self.assertIn("ret_7d DESC NULLS LAST", store["sql"])

    def test_asset_filter_uppercases(self) -> None:
        _, store = self._run(["--asset", "btc"])
        self.assertIn("BTC", store["params"])

    def test_asset_class_filter_passed(self) -> None:
        _, store = self._run(["--asset-class", "crypto"])
        self.assertIn("crypto", store["params"])

    def test_invalid_window_rejected(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["momentum", "--window", "14"])
        self.assertEqual(code, 2)

    def test_invalid_asset_class_rejected(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["momentum", "--asset-class", "bond"])
        self.assertEqual(code, 2)

    def test_json_shape(self) -> None:
        out = io.StringIO()
        store: dict = {}
        with (
            patch(
                "genkei.cli.momentum.db.connection",
                return_value=_FakeConn(store, [_ROW]),
            ),
            redirect_stdout(out),
        ):
            main(["momentum", "--json"])
        parsed = json_mod.loads(out.getvalue())
        self.assertEqual(parsed[0]["asset"], "PYTH")
        self.assertEqual(parsed[0]["ret_30d"], 27.89)
        self.assertEqual(parsed[0]["ts"], "2026-07-12")


class FormatHumanTests(unittest.TestCase):
    def test_empty_rows_message(self) -> None:
        out = _format_human([], sort_window=7)
        self.assertIn("No momentum rows", out)
        self.assertIn("refresh_price_momentum", out)

    def test_null_window_renders_na(self) -> None:
        rows = [
            {
                "asset": "NEWCOIN",
                "asset_class": "crypto",
                "ts": "2026-07-12",
                "close": 1.23,
                "ret_3d": 4.5,
                "ret_7d": None,
                "ret_30d": None,
            }
        ]
        out = _format_human(rows, sort_window=7)
        self.assertIn("n/a", out)
        self.assertIn("+4.50%", out)


if __name__ == "__main__":
    unittest.main()
