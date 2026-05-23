"""Unit tests for the Coinbase normalizer (offline)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from genkei.normalize.coinbase import (
    _parse_unix_seconds,
    _product_from_endpoint_name,
    normalize_candles,
)


class ParseUnixSecondsTests(unittest.TestCase):
    def test_integer_unix_seconds_to_utc_datetime(self) -> None:
        # 1700000000 = 2023-11-14 22:13:20 UTC
        dt = _parse_unix_seconds(1700000000)
        self.assertEqual(dt, datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc))

    def test_float_seconds_accepted(self) -> None:
        # Coinbase uses integer seconds in practice but the parser
        # should be tolerant of float input.
        dt = _parse_unix_seconds(1700000000.5)
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_none_returns_none(self) -> None:
        self.assertIsNone(_parse_unix_seconds(None))

    def test_non_numeric_returns_none(self) -> None:
        self.assertIsNone(_parse_unix_seconds("not a timestamp"))


class ProductFromEndpointNameTests(unittest.TestCase):
    def test_daily_blob_name(self) -> None:
        self.assertEqual(_product_from_endpoint_name("candles_BTC-USD"), "BTC-USD")

    def test_backfill_blob_name(self) -> None:
        self.assertEqual(
            _product_from_endpoint_name("candles_BTC-USD_2024-01-01_2024-10-08"),
            "BTC-USD",
        )

    def test_product_with_hyphen_in_quote(self) -> None:
        # Defensive: ETH-USD format with explicit hyphen lives in the
        # product portion, not the date portion.
        self.assertEqual(
            _product_from_endpoint_name("candles_ETH-USD_2020-05-17_2021-02-21"),
            "ETH-USD",
        )

    def test_unknown_prefix_returns_none(self) -> None:
        self.assertIsNone(_product_from_endpoint_name("market_chart_btc"))

    def test_empty_payload_after_prefix_returns_none(self) -> None:
        # Pathological edge: the loader should never produce this but
        # the parser shouldn't crash either.
        self.assertIsNone(_product_from_endpoint_name("candles_"))


class NormalizeCandlesTests(unittest.TestCase):
    def _row_kwargs(self) -> dict:
        return {
            "product": "BTC-USD",
            "source_endpoint": "https://api.exchange.coinbase.com/products/BTC-USD/candles?...",
            "ingest_run_id": 42,
            "fetched_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        }

    def test_well_formed_candle_yields_one_row(self) -> None:
        payload = [[1700000000, 100.0, 110.0, 105.0, 108.0, 1234.5]]
        rows = normalize_candles(payload, **self._row_kwargs())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["product"], "BTC-USD")
        self.assertEqual(row["ts"], datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc))
        # Column order is intentional — Coinbase candles arrive
        # [time, low, high, open, close, volume] so we unpack by
        # position. open=105, high=110, low=100, close=108.
        self.assertEqual(row["open"], 105.0)
        self.assertEqual(row["high"], 110.0)
        self.assertEqual(row["low"], 100.0)
        self.assertEqual(row["close"], 108.0)
        self.assertEqual(row["volume_base"], 1234.5)
        self.assertEqual(row["ingest_run_id"], 42)

    def test_empty_array_yields_zero_rows(self) -> None:
        # Pre-listing windows return [] — the normalizer should accept
        # silently so backfills don't fail on SUI-USD in 2020.
        rows = normalize_candles([], **self._row_kwargs())
        self.assertEqual(rows, [])

    def test_non_list_payload_yields_zero_rows(self) -> None:
        # If Coinbase returns an error dict, the ingester should have
        # already flagged it as a failure — but normalize shouldn't
        # crash if a bad blob slips through.
        rows = normalize_candles({"message": "bad"}, **self._row_kwargs())
        self.assertEqual(rows, [])

    def test_short_candle_is_skipped(self) -> None:
        # 5-element candle (missing volume): skip rather than crash.
        payload = [[1700000000, 100.0, 110.0, 105.0, 108.0]]
        rows = normalize_candles(payload, **self._row_kwargs())
        self.assertEqual(rows, [])

    def test_candle_with_null_field_is_skipped(self) -> None:
        # Any NULL in the 6-tuple breaks the NOT NULL columns — skip.
        payload = [[1700000000, 100.0, 110.0, None, 108.0, 1234.5]]
        rows = normalize_candles(payload, **self._row_kwargs())
        self.assertEqual(rows, [])

    def test_mixed_valid_and_invalid_rows(self) -> None:
        payload = [
            [1700000000, 100.0, 110.0, 105.0, 108.0, 1234.5],  # valid
            [1700086400, 108.0, 115.0, 109.0, 114.0, 2000.0],  # valid
            "not a candle",  # skipped
            [1700172800, None, None, None, None, None],  # skipped
        ]
        rows = normalize_candles(payload, **self._row_kwargs())
        self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()
