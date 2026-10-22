"""Unit tests for `genkei prices` human formatting.

Regression coverage for sub-cent price rendering: the formatter used a
fixed two-decimal format, so every sub-cent token on the watchlist
(PUMP, PENGU) printed as ``0.00`` and the human output could not be
used to check a price at all.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from genkei.cli.prices import _format_human, _format_price


class FormatPriceTests(unittest.TestCase):
    def test_normal_prices_stay_at_cents(self) -> None:
        self.assertEqual(_format_price(5.29), "5.29")
        self.assertEqual(_format_price(1.0), "1.00")
        self.assertEqual(_format_price(83959.4), "83,959.40")

    def test_sub_cent_price_keeps_four_significant_digits(self) -> None:
        # PUMP on 2026-10-22 — the case that motivated the fix.
        self.assertEqual(_format_price(0.00478965456470235), "0.004790")

    def test_precision_scales_with_magnitude(self) -> None:
        self.assertEqual(_format_price(0.4789), "0.4789")
        self.assertEqual(_format_price(0.04789), "0.04789")
        self.assertEqual(_format_price(0.004789), "0.004789")
        self.assertEqual(_format_price(0.0000012345), "0.000001234")

    def test_zero_and_negative_are_handled(self) -> None:
        self.assertEqual(_format_price(0.0), "0.00")
        self.assertEqual(_format_price(-0.004789), "-0.004789")

    def test_extremely_small_price_is_capped_not_unbounded(self) -> None:
        rendered = _format_price(1e-30)
        # Capped at _MAX_PRICE_DECIMALS so the column stays aligned.
        self.assertEqual(rendered, "0.000000000000")
        self.assertLessEqual(len(rendered), 14)


class FormatHumanTests(unittest.TestCase):
    def test_sub_cent_row_is_not_collapsed_to_zero(self) -> None:
        rows = [
            {
                "ts": datetime(2026, 10, 22, tzinfo=timezone.utc),
                "price_usd": 0.00478965456470235,
                "market_cap_usd": 2211319158.55901,
                "volume_usd": 214188490.860973,
            }
        ]
        out = _format_human("PUMP", "coingecko", rows)
        self.assertIn("0.004790", out)
        self.assertNotIn("  0.00  ", out)
        self.assertIn("2,211,319,159", out)

    def test_missing_price_renders_na(self) -> None:
        rows = [
            {
                "ts": datetime(2026, 10, 22, tzinfo=timezone.utc),
                "price_usd": None,
                "market_cap_usd": None,
                "volume_usd": None,
            }
        ]
        out = _format_human("PUMP", "coingecko", rows)
        self.assertIn("n/a", out)

    def test_empty_rows_hint_at_ingest(self) -> None:
        out = _format_human("XRP", "coingecko", [])
        self.assertIn("XRP", out)
        self.assertIn("ingest", out)


if __name__ == "__main__":
    unittest.main()
