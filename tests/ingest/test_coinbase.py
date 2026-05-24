"""Unit tests for the Coinbase collector helpers (offline)."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from genkei.ingest.coinbase import (
    BACKFILL_CHUNK_DAYS,
    DAILY_GRANULARITY_SECONDS,
    ProductTarget,
    _chunk_windows,
    build_candles_url,
    load_products,
)


class LoadProductsTests(unittest.TestCase):
    def test_reads_crypto_entries_with_coinbase_product(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(
                "crypto:\n"
                "  primary:\n"
                "    - symbol: BTC\n"
                "      name: Bitcoin\n"
                "      coingecko_id: bitcoin\n"
                "      coinbase_product: BTC-USD\n"
                "    - symbol: ETH\n"
                "      name: Ethereum\n"
                "      coingecko_id: ethereum\n"
                "      coinbase_product: ETH-USD\n",
                encoding="utf-8",
            )
            products = load_products(path)
        self.assertEqual(len(products), 2)
        self.assertEqual(products[0], ProductTarget("BTC", "BTC-USD"))
        self.assertEqual(products[1], ProductTarget("ETH", "ETH-USD"))

    def test_skips_entries_without_coinbase_product(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            # BTC has the mapping, FOO doesn't — only BTC should land.
            path.write_text(
                "crypto:\n"
                "  primary:\n"
                "    - symbol: BTC\n"
                "      name: Bitcoin\n"
                "      coingecko_id: bitcoin\n"
                "      coinbase_product: BTC-USD\n"
                "    - symbol: FOO\n"
                "      name: Foo Token\n"
                "      coingecko_id: foo\n",
                encoding="utf-8",
            )
            products = load_products(path)
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].symbol, "BTC")

    def test_raises_when_no_entries_have_mapping(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(
                "crypto:\n"
                "  primary:\n"
                "    - symbol: BTC\n"
                "      name: Bitcoin\n"
                "      coingecko_id: bitcoin\n",
                encoding="utf-8",
            )
            with self.assertRaises(SystemExit) as cm:
                load_products(path)
            self.assertIn("coinbase_product set", str(cm.exception).lower())

    def test_rejects_duplicate_products(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            # Two entries pointing at the same product is a watchlist
            # bug — silently double-fetching costs rate budget.
            path.write_text(
                "crypto:\n"
                "  primary:\n"
                "    - symbol: BTC\n"
                "      name: Bitcoin\n"
                "      coingecko_id: bitcoin\n"
                "      coinbase_product: BTC-USD\n"
                "    - symbol: BTC2\n"
                "      name: Bitcoin (dupe)\n"
                "      coingecko_id: bitcoin-2\n"
                "      coinbase_product: BTC-USD\n",
                encoding="utf-8",
            )
            with self.assertRaises(SystemExit) as cm:
                load_products(path)
            self.assertIn("Duplicate coinbase_product", str(cm.exception))


class BuildCandlesUrlTests(unittest.TestCase):
    def test_url_shape(self) -> None:
        start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 31, 23, 59, 59, tzinfo=timezone.utc)
        url = build_candles_url("BTC-USD", start=start, end=end)
        self.assertIn("/products/BTC-USD/candles", url)
        self.assertIn("granularity=86400", url)
        self.assertIn("start=2024-01-01T00%3A00%3A00Z", url)
        self.assertIn("end=2024-01-31T23%3A59%3A59Z", url)

    def test_default_granularity_is_daily(self) -> None:
        url = build_candles_url(
            "ETH-USD",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        self.assertIn(f"granularity={DAILY_GRANULARITY_SECONDS}", url)

    def test_url_is_deterministic_for_same_inputs(self) -> None:
        # Determinism matters for raw_blobs.url uniqueness (re-fetching
        # the same window produces the same URL and the same blob key).
        start = datetime(2024, 6, 1, tzinfo=timezone.utc)
        end = datetime(2024, 6, 30, tzinfo=timezone.utc)
        self.assertEqual(
            build_candles_url("SOL-USD", start=start, end=end),
            build_candles_url("SOL-USD", start=start, end=end),
        )


class ChunkWindowsTests(unittest.TestCase):
    def test_single_chunk_when_range_fits(self) -> None:
        windows = _chunk_windows(date(2024, 1, 1), date(2024, 3, 1), chunk_days=280)
        self.assertEqual(windows, [(date(2024, 1, 1), date(2024, 3, 1))])

    def test_splits_long_range(self) -> None:
        # 600 days at 280-day chunks → 3 chunks: [0-279], [280-559], [560-599].
        windows = _chunk_windows(date(2020, 1, 1), date(2021, 8, 23), chunk_days=280)
        self.assertEqual(len(windows), 3)
        self.assertEqual(windows[0][0], date(2020, 1, 1))
        # Chunks are contiguous (no gaps, no overlap).
        from datetime import timedelta as _td

        for i in range(len(windows) - 1):
            self.assertEqual(windows[i + 1][0], windows[i][1] + _td(days=1))

    def test_uses_default_chunk_size(self) -> None:
        # The 10y BTC backfill (2015-07 to today) should produce ~14
        # chunks at the default 280-day chunk size.
        windows = _chunk_windows(date(2015, 7, 19), date(2026, 5, 22), BACKFILL_CHUNK_DAYS)
        self.assertGreater(len(windows), 10)
        self.assertLess(len(windows), 20)

    def test_rejects_inverted_range(self) -> None:
        with self.assertRaises(ValueError):
            _chunk_windows(date(2024, 6, 1), date(2024, 1, 1), 280)

    def test_rejects_non_positive_chunk_size(self) -> None:
        with self.assertRaises(ValueError):
            _chunk_windows(date(2024, 6, 1), date(2024, 6, 30), 0)
        with self.assertRaises(ValueError):
            _chunk_windows(date(2024, 6, 1), date(2024, 6, 30), -10)


if __name__ == "__main__":
    unittest.main()
