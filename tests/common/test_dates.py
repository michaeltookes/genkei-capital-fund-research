"""Tests for the shared date-windowing helper (B-121)."""

from __future__ import annotations

import unittest
from datetime import date

from genkei.common.dates import iter_date_windows


class IterDateWindowsTest(unittest.TestCase):
    def test_single_window_when_span_fits(self) -> None:
        windows = iter_date_windows(date(2024, 1, 1), date(2024, 1, 5), chunk_days=10)
        self.assertEqual(windows, [(date(2024, 1, 1), date(2024, 1, 5))])

    def test_inclusive_windows_split_evenly(self) -> None:
        windows = iter_date_windows(date(2020, 1, 1), date(2020, 1, 5), chunk_days=2)
        self.assertEqual(
            windows,
            [
                (date(2020, 1, 1), date(2020, 1, 2)),
                (date(2020, 1, 3), date(2020, 1, 4)),
                (date(2020, 1, 5), date(2020, 1, 5)),
            ],
        )

    def test_last_window_is_shorter_when_uneven(self) -> None:
        windows = iter_date_windows(date(2024, 1, 1), date(2024, 3, 1), chunk_days=28)
        # Each window spans exactly chunk_days except possibly the last, and the
        # windows tile the span back-to-back with no gaps or overlaps.
        self.assertEqual(windows[0][0], date(2024, 1, 1))
        self.assertEqual(windows[-1][1], date(2024, 3, 1))
        for i in range(1, len(windows)):
            prev_end = windows[i - 1][1]
            next_start = windows[i][0]
            self.assertEqual((next_start - prev_end).days, 1)

    def test_single_day_span(self) -> None:
        windows = iter_date_windows(date(2024, 6, 1), date(2024, 6, 1), chunk_days=280)
        self.assertEqual(windows, [(date(2024, 6, 1), date(2024, 6, 1))])

    def test_raises_on_backwards_span(self) -> None:
        with self.assertRaises(ValueError):
            iter_date_windows(date(2024, 6, 1), date(2024, 1, 1), chunk_days=280)

    def test_raises_on_nonpositive_chunk_days(self) -> None:
        with self.assertRaises(ValueError):
            iter_date_windows(date(2024, 6, 1), date(2024, 6, 30), chunk_days=0)
        with self.assertRaises(ValueError):
            iter_date_windows(date(2024, 6, 1), date(2024, 6, 30), chunk_days=-10)


if __name__ == "__main__":
    unittest.main()
