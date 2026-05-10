"""Unit tests for the DeFiLlama normalizer (offline)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from genkei.normalize.defillama import (
    _rows_for,
    _stablecoin_supply,
    as_float,
    chain_history_stem,
    normalize_chain_tvl_history,
    normalize_prices,
    normalize_protocol_history,
    normalize_protocols,
    normalize_stablecoin_history,
    normalize_stablecoins,
    parse_history_timestamp,
)

NOW = datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc)


class HelperTests(unittest.TestCase):
    def test_as_float_handles_strings_and_rejects_bool(self) -> None:
        self.assertEqual(as_float("3.14"), 3.14)
        self.assertEqual(as_float(7), 7.0)
        self.assertIsNone(as_float(None))
        self.assertIsNone(as_float(True))
        self.assertIsNone(as_float("nope"))

    def test_parse_history_timestamp_accepts_epoch_and_iso(self) -> None:
        self.assertEqual(
            parse_history_timestamp(1_700_000_000),
            datetime.fromtimestamp(1_700_000_000, tz=timezone.utc),
        )
        self.assertEqual(
            parse_history_timestamp("2025-12-01T00:00:00Z"),
            datetime(2025, 12, 1, tzinfo=timezone.utc),
        )
        self.assertIsNone(parse_history_timestamp("garbage"))

    def test_chain_history_stem_matches_collector_naming(self) -> None:
        self.assertEqual(chain_history_stem("Rootstock RSK"), "rootstock_rsk")
        self.assertEqual(chain_history_stem("Ethereum"), "ethereum")

    def test_stablecoin_supply_walks_nested_keys(self) -> None:
        self.assertEqual(_stablecoin_supply({"current": {"peggedUSD": 1_234.56}}), 1234.56)
        self.assertEqual(_stablecoin_supply({"current": 42}), 42.0)
        self.assertEqual(_stablecoin_supply(99), 99.0)
        self.assertIsNone(_stablecoin_supply({"current": {"other": "x"}}))

    def test_rows_for_requires_blob(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Missing required raw blob.*protocols"):
            _rows_for({}, "protocols", normalize_protocols, 1, 99)


class NormalizeProtocolsTests(unittest.TestCase):
    def test_returns_one_row_per_named_protocol(self) -> None:
        payload = [
            {
                "id": 1,
                "slug": "aave-v3",
                "name": "Aave V3",
                "category": "Lending",
                "chains": ["Ethereum", "Arbitrum"],
                "url": "https://aave.com",
                "description": "lending",
                "parentProtocol": "aave",
                "twitter": "aave",
            },
            {"id": 2, "name": "missing-slug"},  # dropped
            "string item",  # dropped
        ]
        rows = normalize_protocols(
            payload, source_endpoint="https://api.llama.fi/protocols", ingest_run_id=42, now=NOW
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["slug"], "aave-v3")
        self.assertEqual(row["defillama_id"], "1")
        self.assertEqual(row["chains"], ["Ethereum", "Arbitrum"])
        self.assertEqual(row["last_updated_at"], NOW)
        self.assertEqual(row["fetched_at"], NOW)
        self.assertEqual(row["ingest_run_id"], 42)
        self.assertEqual(row["source_endpoint"], "https://api.llama.fi/protocols")
        self.assertNotIn("first_seen_at", row)  # DB default fires on insert

    def test_handles_non_list_chains(self) -> None:
        payload = [{"slug": "x", "name": "X", "chains": "not a list"}]
        rows = normalize_protocols(payload, source_endpoint="x", ingest_run_id=1, now=NOW)
        self.assertIsNone(rows[0]["chains"])


class NormalizeChainTvlHistoryTests(unittest.TestCase):
    def test_drops_invalid_and_dedupes_timestamps(self) -> None:
        payload = [
            {"date": 1_700_000_000, "tvl": 100.0},
            {"date": 1_700_086_400, "tvl": 110.5},
            {"date": 1_700_086_400, "tvl": 999.0},  # duplicate ts dropped
            {"date": "garbage", "tvl": 5.0},  # dropped
            {"date": 1_700_172_800, "tvl": None},  # dropped
        ]
        rows = normalize_chain_tvl_history(
            payload,
            chain_name="Ethereum",
            source_endpoint="https://api.llama.fi/v2/historicalChainTvl/Ethereum",
            ingest_run_id=1,
            now=NOW,
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["chain"], "Ethereum")
        self.assertEqual(rows[0]["tvl_usd"], 100.0)
        self.assertEqual(rows[1]["tvl_usd"], 110.5)
        for row in rows:
            self.assertEqual(
                row["source_endpoint"], "https://api.llama.fi/v2/historicalChainTvl/Ethereum"
            )


class NormalizeStablecoinsTests(unittest.TestCase):
    def test_emits_one_row_per_asset_chain_pair(self) -> None:
        payload = {
            "peggedAssets": [
                {
                    "id": "1",
                    "name": "Tether",
                    "symbol": "USDT",
                    "pegType": "peggedUSD",
                    "chainCirculating": {
                        "Ethereum": {"current": {"peggedUSD": 50_000_000_000}},
                        "Tron": {"current": {"peggedUSD": 30_000_000_000}},
                        "EmptyChain": {"current": {}},  # no usable supply -> dropped
                    },
                },
                {
                    "id": "2",
                    "name": "Legacy USD",
                    "symbol": "LUSD",
                    "pegType": "peggedUSD",
                    "chainBalances": {
                        "Ethereum": {"current": {"peggedUSD": 1_000_000}},
                        "LegacyChain": {"current": {"peggedUSD": 2_000_000}},
                    },
                },
                {
                    "id": "3",
                    "name": "Empty Canonical",
                    "symbol": "EMPTY",
                    "pegType": "peggedUSD",
                    "chainCirculating": {},
                    "chainBalances": {
                        "StaleChain": {"current": {"peggedUSD": 99_000_000}},
                    },
                },
                "garbage",
            ]
        }
        rows = normalize_stablecoins(
            payload,
            source_endpoint="https://stablecoins.llama.fi/stablecoins",
            ingest_run_id=7,
            now=NOW,
        )
        chain_names = sorted(row["chain"] for row in rows)
        self.assertEqual(chain_names, ["Ethereum", "Ethereum", "LegacyChain", "Tron"])
        self.assertEqual({row["symbol"] for row in rows}, {"LUSD", "USDT"})
        self.assertEqual({row["asset_id"] for row in rows}, {"1", "2"})
        for row in rows:
            self.assertEqual(row["ts"], NOW)
            self.assertEqual(row["ingest_run_id"], 7)
            self.assertEqual(row["source_endpoint"], "https://stablecoins.llama.fi/stablecoins")


class NormalizePricesTests(unittest.TestCase):
    def test_uses_record_timestamp_when_present(self) -> None:
        payload = {
            "coins": {
                "coingecko:bitcoin": {
                    "price": 64_000.0,
                    "timestamp": 1_700_000_000,
                    "symbol": "BTC",
                    "decimals": 8,
                    "confidence": 0.99,
                },
                "coingecko:nullprice": {"price": None},  # dropped
            }
        }
        rows = normalize_prices(
            payload,
            source_endpoint="https://coins.llama.fi/prices/current/x",
            ingest_run_id=3,
            now=NOW,
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["asset_key"], "coingecko:bitcoin")
        self.assertEqual(row["ts"], datetime.fromtimestamp(1_700_000_000, tz=timezone.utc))
        self.assertEqual(row["price_usd"], 64_000.0)
        self.assertEqual(row["symbol"], "BTC")
        self.assertEqual(row["decimals"], 8)
        self.assertAlmostEqual(row["confidence"], 0.99)
        self.assertEqual(row["source_endpoint"], "https://coins.llama.fi/prices/current/x")

    def test_falls_back_to_provided_default_without_timestamp(self) -> None:
        payload = {"coins": {"coingecko:foo": {"price": 1.0}}}
        rows = normalize_prices(payload, source_endpoint="x", ingest_run_id=1, now=NOW)
        self.assertEqual(rows[0]["ts"], NOW)
        self.assertEqual(rows[0]["source_endpoint"], "x")


class NormalizeProtocolHistoryTests(unittest.TestCase):
    def test_emits_one_row_per_chain_timestamp(self) -> None:
        payload = {
            "id": "111",
            "name": "Aave V3",
            "slug": "aave-v3",
            "chainTvls": {
                "Ethereum": {
                    "tvl": [
                        {"date": 1_700_000_000, "totalLiquidityUSD": 100.0},
                        {"date": 1_700_086_400, "totalLiquidityUSD": 110.0},
                    ]
                },
                "Arbitrum": {"tvl": [{"date": 1_700_000_000, "totalLiquidityUSD": 50.0}]},
                # Synthetic sub-buckets with `-` are filtered out.
                "Ethereum-borrowed": {"tvl": [{"date": 1_700_000_000, "totalLiquidityUSD": 999}]},
            },
        }
        rows = normalize_protocol_history(
            payload,
            slug="aave-v3",
            source_endpoint="https://api.llama.fi/protocol/aave-v3",
            ingest_run_id=11,
            fetched_at=NOW,
        )
        self.assertEqual(len(rows), 3)
        chains = sorted({r["chain"] for r in rows})
        self.assertEqual(chains, ["Arbitrum", "Ethereum"])
        for row in rows:
            self.assertEqual(row["slug"], "aave-v3")
            self.assertEqual(row["fetched_at"], NOW)
            self.assertEqual(row["ingest_run_id"], 11)


class NormalizeStablecoinHistoryTests(unittest.TestCase):
    def test_emits_one_row_per_chain_timestamp(self) -> None:
        payload = {
            "id": "1",
            "name": "Tether",
            "symbol": "USDT",
            "pegType": "peggedUSD",
            "chainBalances": {
                "Ethereum": {
                    "tokens": [
                        {"date": 1_700_000_000, "current": {"peggedUSD": 50_000_000_000}},
                        {"date": 1_700_086_400, "current": {"peggedUSD": 50_500_000_000}},
                    ]
                },
                "Tron": {
                    "tokens": [{"date": 1_700_000_000, "current": {"peggedUSD": 30_000_000_000}}]
                },
            },
        }
        rows = normalize_stablecoin_history(
            payload,
            asset_id="1",
            source_endpoint="https://stablecoins.llama.fi/stablecoin/1",
            ingest_run_id=22,
            fetched_at=NOW,
        )
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(row["asset_id"], "1")
            self.assertEqual(row["symbol"], "USDT")
            self.assertEqual(row["peg_type"], "peggedUSD")
            self.assertEqual(row["fetched_at"], NOW)
            self.assertEqual(row["ingest_run_id"], 22)


if __name__ == "__main__":
    unittest.main()
