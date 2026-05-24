"""Unit tests for the Yahoo Finance normalizer (offline)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from genkei.normalize.yahoo import (
    _parse_unix_seconds,
    _ticker_from_endpoint_name,
    normalize_chart,
)


class ParseUnixSecondsTests(unittest.TestCase):
    def test_integer_seconds(self) -> None:
        dt = _parse_unix_seconds(1700000000)
        self.assertEqual(dt, datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc))

    def test_none_returns_none(self) -> None:
        self.assertIsNone(_parse_unix_seconds(None))

    def test_non_numeric_returns_none(self) -> None:
        self.assertIsNone(_parse_unix_seconds("nope"))


class TickerFromEndpointNameTests(unittest.TestCase):
    def test_daily_blob_name(self) -> None:
        self.assertEqual(_ticker_from_endpoint_name("chart_AAPL"), "AAPL")

    def test_backfill_blob_name(self) -> None:
        self.assertEqual(
            _ticker_from_endpoint_name("chart_AAPL_1970-01-01_2026-05-24"),
            "AAPL",
        )

    def test_multi_letter_ticker(self) -> None:
        # SMCI, GOOGL, MSTR etc. — 4+ chars before the date.
        self.assertEqual(
            _ticker_from_endpoint_name("chart_GOOGL_1980-01-01_2026-05-24"),
            "GOOGL",
        )

    def test_unknown_prefix_returns_none(self) -> None:
        # Coinbase blobs use `candles_` — shouldn't claim them.
        self.assertIsNone(_ticker_from_endpoint_name("candles_BTC-USD"))

    def test_empty_payload_after_prefix_returns_none(self) -> None:
        self.assertIsNone(_ticker_from_endpoint_name("chart_"))


class NormalizeChartTests(unittest.TestCase):
    def _kwargs(self) -> dict:
        return {
            "ticker": "AAPL",
            "source_endpoint": "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?...",
            "ingest_run_id": 42,
            "fetched_at": datetime(2026, 5, 24, tzinfo=timezone.utc),
        }

    def _payload(self, **overrides) -> dict:
        """Minimal Yahoo-shaped payload with one candle."""
        base = {
            "chart": {
                "result": [
                    {
                        "meta": {"symbol": "AAPL", "currency": "USD"},
                        "timestamp": [1700000000],
                        "indicators": {
                            "quote": [
                                {
                                    "open": [105.0],
                                    "high": [110.0],
                                    "low": [100.0],
                                    "close": [108.0],
                                    "volume": [1_234_567],
                                }
                            ],
                            "adjclose": [{"adjclose": [107.5]}],
                        },
                    }
                ],
                "error": None,
            }
        }
        base.update(overrides)
        return base

    def test_well_formed_candle_yields_one_row(self) -> None:
        rows = normalize_chart(self._payload(), **self._kwargs())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["ticker"], "AAPL")
        self.assertEqual(row["ts"], datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc))
        self.assertEqual(row["open"], 105.0)
        self.assertEqual(row["high"], 110.0)
        self.assertEqual(row["low"], 100.0)
        self.assertEqual(row["close"], 108.0)
        self.assertEqual(row["adj_close"], 107.5)
        self.assertEqual(row["volume"], 1_234_567)

    def test_missing_adjclose_yields_null_adj_close(self) -> None:
        payload = self._payload()
        # Strip the adjclose indicators block.
        payload["chart"]["result"][0]["indicators"].pop("adjclose")
        rows = normalize_chart(payload, **self._kwargs())
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["adj_close"])
        # close still populated (NOT NULL invariant preserved).
        self.assertEqual(rows[0]["close"], 108.0)

    def test_null_volume_skips_the_row(self) -> None:
        payload = self._payload()
        payload["chart"]["result"][0]["indicators"]["quote"][0]["volume"] = [None]
        rows = normalize_chart(payload, **self._kwargs())
        self.assertEqual(rows, [])

    def test_empty_chart_result_yields_zero_rows(self) -> None:
        payload = {"chart": {"result": [], "error": None}}
        self.assertEqual(normalize_chart(payload, **self._kwargs()), [])

    def test_dict_payload_without_chart_key_yields_zero_rows(self) -> None:
        # Defensive — should never happen given the ingester's error
        # detection, but the normalizer mustn't crash on it.
        self.assertEqual(normalize_chart({"something": "else"}, **self._kwargs()), [])

    def test_non_dict_payload_yields_zero_rows(self) -> None:
        self.assertEqual(normalize_chart("not a dict", **self._kwargs()), [])

    def test_multiple_aligned_candles(self) -> None:
        payload = self._payload()
        r = payload["chart"]["result"][0]
        r["timestamp"] = [1700000000, 1700086400, 1700172800]
        q = r["indicators"]["quote"][0]
        q["open"] = [105.0, 108.0, 109.0]
        q["high"] = [110.0, 115.0, 116.0]
        q["low"] = [100.0, 107.0, 108.0]
        q["close"] = [108.0, 114.0, 115.5]
        q["volume"] = [1, 2, 3]
        r["indicators"]["adjclose"][0]["adjclose"] = [107.5, 113.5, 115.0]
        rows = normalize_chart(payload, **self._kwargs())
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["close"], 108.0)
        self.assertEqual(rows[1]["close"], 114.0)
        self.assertEqual(rows[2]["close"], 115.5)

    def test_offset_arrays_skip_missing_indices(self) -> None:
        # Defensive shape check — if Yahoo ever returns a shorter
        # quote array than timestamps, we shouldn't crash. The shorter
        # rows get None at the missing positions and are skipped.
        payload = self._payload()
        r = payload["chart"]["result"][0]
        r["timestamp"] = [1700000000, 1700086400]
        q = r["indicators"]["quote"][0]
        # close is length-1 while timestamps is length-2.
        q["close"] = [108.0]
        rows = normalize_chart(payload, **self._kwargs())
        # First row has all fields; second row's close is missing.
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
