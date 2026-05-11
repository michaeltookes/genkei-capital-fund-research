"""Unit tests for the CoinGecko normalizer (offline)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from genkei.normalize.coingecko import (
    normalize_coin,
    normalize_market_chart,
    parse_iso_date,
    parse_unix_ms,
)

NOW = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)


class HelperTests(unittest.TestCase):
    def test_parse_unix_ms_round_trips(self) -> None:
        ts = parse_unix_ms(1_700_000_000_000)
        assert ts is not None
        self.assertEqual(ts.tzinfo, timezone.utc)
        self.assertEqual(int(ts.timestamp()), 1_700_000_000)

    def test_parse_unix_ms_rejects_garbage(self) -> None:
        self.assertIsNone(parse_unix_ms("not a number"))
        self.assertIsNone(parse_unix_ms(None))
        self.assertIsNone(parse_unix_ms(True))

    def test_parse_iso_date(self) -> None:
        from datetime import date as date_type

        self.assertEqual(parse_iso_date("2009-01-03"), date_type(2009, 1, 3))
        self.assertIsNone(parse_iso_date("garbage"))


class NormalizeCoinTests(unittest.TestCase):
    def test_extracts_metadata(self) -> None:
        payload = {
            "id": "bitcoin",
            "symbol": "btc",
            "name": "Bitcoin",
            "market_cap_rank": 1,
            "genesis_date": "2009-01-03",
            "description": {"en": "Bitcoin is the first cryptocurrency..."},
            "links": {"homepage": ["https://bitcoin.org", ""]},
            "categories": ["Cryptocurrency", "Layer 1 (L1)"],
        }
        row = normalize_coin(
            payload,
            coingecko_id="bitcoin",
            source_endpoint="x",
            ingest_run_id=42,
            fetched_at=NOW,
        )
        assert row is not None
        self.assertEqual(row["coingecko_id"], "bitcoin")
        self.assertEqual(row["symbol"], "BTC")
        self.assertEqual(row["name"], "Bitcoin")
        self.assertEqual(row["market_cap_rank"], 1)
        from datetime import date as date_type

        self.assertEqual(row["genesis_date"], date_type(2009, 1, 3))
        self.assertEqual(row["homepage"], "https://bitcoin.org")
        self.assertEqual(row["categories"], ["Cryptocurrency", "Layer 1 (L1)"])
        self.assertTrue(row["description"].startswith("Bitcoin is the first"))

    def test_returns_none_without_symbol(self) -> None:
        self.assertIsNone(
            normalize_coin(
                {"name": "anonymous"},
                coingecko_id="x",
                source_endpoint="x",
                ingest_run_id=1,
                fetched_at=NOW,
            )
        )


class NormalizeMarketChartTests(unittest.TestCase):
    def test_zips_three_parallel_arrays_by_timestamp(self) -> None:
        # G-024: arrays are keyed by timestamp, not index. We only emit rows
        # for timestamps present in all three.
        payload = {
            "prices": [
                [1_700_000_000_000, 35_000.5],
                [1_700_086_400_000, 35_500.0],
                [1_700_172_800_000, 36_100.0],
            ],
            "market_caps": [
                [1_700_000_000_000, 700_000_000_000],
                [1_700_086_400_000, 710_000_000_000],
                [1_700_172_800_000, 720_000_000_000],
            ],
            "total_volumes": [
                [1_700_000_000_000, 20_000_000_000],
                [1_700_086_400_000, 21_000_000_000],
                [1_700_172_800_000, 22_000_000_000],
            ],
        }
        rows = normalize_market_chart(
            payload,
            coingecko_id="bitcoin",
            source_endpoint="x",
            ingest_run_id=11,
            fetched_at=NOW,
        )
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            [r["ts"] for r in rows],
            sorted(r["ts"] for r in rows),
        )
        # The middle row sanity-checks the zip.
        mid = rows[1]
        self.assertEqual(mid["coingecko_id"], "bitcoin")
        self.assertEqual(mid["price_usd"], 35_500.0)
        self.assertEqual(mid["market_cap_usd"], 710_000_000_000)
        self.assertEqual(mid["volume_usd"], 21_000_000_000)

    def test_drops_timestamps_missing_from_any_series(self) -> None:
        # First timestamp present in prices + market_caps but missing from
        # volumes → that row is dropped. Last is in volumes only → dropped too.
        payload = {
            "prices": [
                [1_700_000_000_000, 1.0],
                [1_700_086_400_000, 2.0],
            ],
            "market_caps": [
                [1_700_000_000_000, 10.0],
                [1_700_086_400_000, 20.0],
            ],
            "total_volumes": [
                [1_700_086_400_000, 200.0],
                [1_700_172_800_000, 300.0],  # this ts not in prices
            ],
        }
        rows = normalize_market_chart(
            payload,
            coingecko_id="bitcoin",
            source_endpoint="x",
            ingest_run_id=1,
            fetched_at=NOW,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["price_usd"], 2.0)

    def test_returns_empty_for_malformed_payload(self) -> None:
        self.assertEqual(
            normalize_market_chart(
                "not a dict",
                coingecko_id="x",
                source_endpoint="x",
                ingest_run_id=1,
                fetched_at=NOW,
            ),
            [],
        )
        self.assertEqual(
            normalize_market_chart(
                {"prices": "not a list"},
                coingecko_id="x",
                source_endpoint="x",
                ingest_run_id=1,
                fetched_at=NOW,
            ),
            [],
        )

    def test_drops_pairs_with_non_numeric_values(self) -> None:
        payload = {
            "prices": [
                [1_700_000_000_000, "garbage"],
                [1_700_086_400_000, 2.0],
            ],
            "market_caps": [
                [1_700_000_000_000, 10.0],
                [1_700_086_400_000, 20.0],
            ],
            "total_volumes": [
                [1_700_000_000_000, 100.0],
                [1_700_086_400_000, 200.0],
            ],
        }
        rows = normalize_market_chart(
            payload,
            coingecko_id="bitcoin",
            source_endpoint="x",
            ingest_run_id=1,
            fetched_at=NOW,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["price_usd"], 2.0)


if __name__ == "__main__":
    unittest.main()
