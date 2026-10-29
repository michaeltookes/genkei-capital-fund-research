"""Unit tests for the `genkei momentum` subcommand (B-067)."""

from __future__ import annotations

import io
import json as json_mod
import sqlite3
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from genkei.cli import main
from genkei.cli.momentum import _format_human
from genkei.common.watchlist import CryptoEntry, EquityEntry, Watchlist


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


def _watchlist() -> Watchlist:
    return Watchlist(
        crypto=[
            CryptoEntry(
                symbol="PYTH",
                name="Pyth Network",
                coingecko_id="pyth-network",
                tier="primary",
                sleeve="tactical",
                coinbase_product="PYTH-USD",
            ),
            CryptoEntry(
                symbol="JUP",
                name="Jupiter",
                coingecko_id="jupiter-exchange-solana",
                tier="primary",
                sleeve="core",
                coinbase_product="JUPITER-USD",
            ),
        ],
        equities=[
            EquityEntry(
                symbol="AAPL",
                name="Apple Inc.",
                cik="0000320193",
                tier="primary",
            )
        ],
        macro=[],
        protocols=[],
        filers=[],
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
            patch("genkei.cli.momentum.load_watchlist", return_value=_watchlist()),
            redirect_stdout(out),
        ):
            main(["momentum", *args])
        return out.getvalue(), store

    def test_default_human_output(self) -> None:
        text, _ = self._run([])
        self.assertIn("PYTH", text)
        self.assertIn("crypto:tactical:primary", text)
        self.assertIn("horizon", text)
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
        text, _ = self._run(["--json"])
        parsed = json_mod.loads(text)
        self.assertEqual(parsed[0]["asset"], "PYTH")
        self.assertEqual(parsed[0]["horizon_tag"], "crypto:tactical:primary")
        self.assertEqual(parsed[0]["ret_30d"], 27.89)
        self.assertEqual(parsed[0]["ts"], "2026-07-12")


class CanonicalMomentumTests(unittest.TestCase):
    """Execute the query against fixture rows, including a legacy JUP product."""

    def _run(self, args: list[str]) -> list[dict]:
        with sqlite3.connect(":memory:") as conn:
            conn.execute("ATTACH DATABASE ':memory:' AS analytics")
            conn.execute(
                "CREATE TABLE analytics.price_momentum "
                "(asset TEXT, asset_class TEXT, ts DATE, close NUMERIC, "
                "ret_3d NUMERIC, ret_7d NUMERIC, ret_30d NUMERIC)"
            )
            conn.executemany(
                "INSERT INTO analytics.price_momentum VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    ("JUPITER", "crypto", "2026-10-08", 1, 2, 15, 30),
                    ("JUP", "crypto", "2023-09-01", 999, 999, 999, 999),
                    ("JUPITER", "equity", "2026-10-08", 10, 1, 1, 1),
                    ("JUP", "equity", "2026-10-08", 20, 2, 2, 2),
                ],
            )
            cursor = conn.cursor()
            connection = MagicMock()
            mocked_cursor = connection.__enter__.return_value.cursor.return_value
            mocked_cursor = mocked_cursor.__enter__.return_value
            # SQLite exercises the same CTE/join/filter/order/limit semantics;
            # only the DB-API parameter placeholder differs from psycopg.
            mocked_cursor.execute.side_effect = lambda sql, params: cursor.execute(
                sql.replace("%s", "?"), params
            )
            mocked_cursor.fetchall.side_effect = cursor.fetchall
            out = io.StringIO()
            with (
                patch("genkei.cli.momentum.db.connection", return_value=connection),
                patch("genkei.cli.momentum.load_watchlist", return_value=_watchlist()),
                redirect_stdout(out),
            ):
                main(["momentum", "--json", *args])
            return json_mod.loads(out.getvalue())

    def test_canonical_asset_filter_uses_live_product(self) -> None:
        rows = self._run(["--asset", "jup", "--asset-class", "crypto"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["asset"], "JUP")
        self.assertEqual(rows[0]["close"], 1)
        self.assertEqual(rows[0]["horizon_tag"], "crypto:core:primary")

    def test_unfiltered_output_keeps_equities_and_suppresses_legacy_crypto(self) -> None:
        rows = self._run([])
        self.assertEqual(
            [(r["asset"], r["asset_class"]) for r in rows],
            [("JUP", "crypto"), ("JUP", "equity"), ("JUPITER", "equity")],
        )

    def test_alias_and_legacy_removal_happen_before_limit(self) -> None:
        rows = self._run(["--asset-class", "crypto", "--limit", "1"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["asset"], "JUP")
        self.assertEqual(rows[0]["ret_7d"], 15)


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
                "horizon_tag": "unknown",
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
