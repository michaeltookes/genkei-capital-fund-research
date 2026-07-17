"""Tests for shared numeric coercion helpers."""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import patch

from genkei.common.numeric import safe_decimal


class SafeDecimalTests(unittest.TestCase):
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

        with patch("genkei.common.numeric.LOGGER.warning") as warning:
            self.assertIsNone(safe_decimal(BadStr(), field="nav"))

        warning.assert_called_once()
        args, kwargs = warning.call_args
        self.assertEqual(
            args[0],
            "safe_decimal: unexpected error coercing %s=%s to Decimal",
        )
        self.assertEqual(args[1], "nav")
        self.assertIn("BadStr", args[2])
        self.assertIs(kwargs["exc_info"], True)

    def test_unexpected_failures_with_bad_repr_are_logged(self) -> None:
        class BadStrAndRepr:
            def __str__(self) -> str:
                raise RuntimeError("boom")

            def __repr__(self) -> str:
                raise RuntimeError("repr boom")

        with patch("genkei.common.numeric.LOGGER.warning") as warning:
            self.assertIsNone(safe_decimal(BadStrAndRepr(), field="nav"))

        warning.assert_called_once()
        args, kwargs = warning.call_args
        self.assertEqual(args[1], "nav")
        self.assertEqual(args[2], "<unrepresentable BadStrAndRepr>")
        self.assertIs(kwargs["exc_info"], True)


if __name__ == "__main__":
    unittest.main()
