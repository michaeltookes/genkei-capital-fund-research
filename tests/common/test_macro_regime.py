"""Unit tests for shared macro-regime readers."""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import date
from typing import Any
from unittest.mock import patch

from genkei.common.macro_regime import load_macro_regime_labels


class _FakeCursor:
    def __init__(self, rows: list[tuple[date, str]]) -> None:
        self.rows = rows
        self.sql: str | None = None
        self.params: list[Any] | None = None

    def execute(self, sql: str, params: list[Any]) -> None:
        self.sql = sql
        self.params = params

    def fetchall(self) -> list[tuple[date, str]]:
        return self.rows


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    @contextmanager
    def cursor(self) -> Any:
        yield self._cursor


class MacroRegimeReaderTests(unittest.TestCase):
    def test_loads_bounded_calendar_in_requested_order(self) -> None:
        cursor = _FakeCursor([(date(2026, 7, 1), "risk_on")])

        @contextmanager
        def fake_connection() -> Any:
            yield _FakeConnection(cursor)

        with patch("genkei.common.macro_regime.db.connection", fake_connection):
            rows = load_macro_regime_labels(
                since=date(2026, 6, 1),
                until=date(2026, 7, 1),
                ascending=True,
            )

        self.assertEqual(rows, [(date(2026, 7, 1), "risk_on")])
        self.assertIn("analytics.macro_regime_per_date", cursor.sql or "")
        self.assertIn("ts >= %s", cursor.sql or "")
        self.assertIn("ts <= %s", cursor.sql or "")
        self.assertTrue((cursor.sql or "").endswith("ORDER BY ts ASC"))
        self.assertEqual(cursor.params, [date(2026, 6, 1), date(2026, 7, 1)])

    def test_loads_exact_dates_with_dedupe(self) -> None:
        cursor = _FakeCursor([(date(2026, 7, 1), "risk_on")])

        @contextmanager
        def fake_connection() -> Any:
            yield _FakeConnection(cursor)

        with patch("genkei.common.macro_regime.db.connection", fake_connection):
            load_macro_regime_labels(
                dates=[date(2026, 7, 1), date(2026, 7, 1)],
                ascending=False,
            )

        self.assertIn("ts = ANY(%s)", cursor.sql or "")
        self.assertTrue((cursor.sql or "").endswith("ORDER BY ts DESC"))
        self.assertEqual(cursor.params, [[date(2026, 7, 1)]])

    def test_empty_exact_dates_short_circuits_without_db(self) -> None:
        with patch("genkei.common.macro_regime.db.connection") as connection:
            self.assertEqual(load_macro_regime_labels(dates=[]), [])
        connection.assert_not_called()


if __name__ == "__main__":
    unittest.main()
