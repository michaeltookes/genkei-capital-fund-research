"""Tests for shared numeric coercion helpers."""

from __future__ import annotations

import logging
import unittest
from decimal import Decimal

from genkei.common.numeric import safe_decimal


class SafeDecimalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._logging_disable_level = logging.root.manager.disable
        logging.disable(logging.NOTSET)

    def tearDown(self) -> None:
        logging.disable(self._logging_disable_level)

    def test_accepts_native_numeric_and_string_values(self) -> None:
        self.assertEqual(safe_decimal(33), Decimal("33"))
        self.assertEqual(safe_decimal(33.81), Decimal("33.81"))
        self.assertEqual(safe_decimal("1234.56"), Decimal("1234.56"))

    def test_parse_failures_return_none(self) -> None:
        self.assertIsNone(safe_decimal(""))
        self.assertIsNone(safe_decimal("-"))
        self.assertIsNone(safe_decimal(None))

    def test_unexpected_failures_are_logged(self) -> None:
        class BadStr:
            def __str__(self) -> str:
                raise RuntimeError("boom")

        with self.assertLogs("genkei.common.numeric", level="WARNING") as logs:
            self.assertIsNone(safe_decimal(BadStr(), field="nav"))

        self.assertIn("safe_decimal: unexpected error coercing nav=", logs.output[0])

    def test_unexpected_failures_with_bad_repr_are_logged(self) -> None:
        class BadStrAndRepr:
            def __str__(self) -> str:
                raise RuntimeError("boom")

            def __repr__(self) -> str:
                raise RuntimeError("repr boom")

        with self.assertLogs("genkei.common.numeric", level="WARNING") as logs:
            self.assertIsNone(safe_decimal(BadStrAndRepr(), field="nav"))

        self.assertIn(
            "safe_decimal: unexpected error coercing nav=<unrepresentable BadStrAndRepr>",
            logs.output[0],
        )


if __name__ == "__main__":
    unittest.main()
