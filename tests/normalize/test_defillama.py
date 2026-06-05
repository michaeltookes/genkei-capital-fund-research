"""Unit tests for the DeFiLlama normalizer (offline)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from genkei.normalize.defillama import (
    _rows_for,
    _stablecoin_supply,
    _upsert_protocol_fee_rows,
    as_float,
    chain_history_stem,
    day_align_utc,
    merge_fee_revenue_rows,
    normalize_chain_tvl_history,
    normalize_prices,
    normalize_protocol_fee_series,
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


class DayAlignUtcTests(unittest.TestCase):
    """Pin the load-bearing day-align helper that prevents the B-109 bug."""

    def test_truncates_intra_day_utc_to_midnight(self) -> None:
        ts = datetime(2026, 6, 4, 12, 34, 56, 789, tzinfo=timezone.utc)
        self.assertEqual(
            day_align_utc(ts),
            datetime(2026, 6, 4, 0, 0, 0, 0, tzinfo=timezone.utc),
        )

    def test_already_aligned_passes_through(self) -> None:
        ts = datetime(2026, 6, 4, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(day_align_utc(ts), ts)

    def test_non_utc_input_converts_to_utc_first(self) -> None:
        # 2026-06-04 19:00:00-05:00 == 2026-06-05 00:00:00 UTC,
        # which day-aligns to 2026-06-05 00:00 UTC (NOT 2026-06-04).
        # This is exactly the case that caused the bug: defillama's
        # backfill rows arrived as "2026-05-10 19:00:00-05:00" and were
        # being rendered as 2026-05-10 in session-local time but
        # represented 2026-05-11 00:00 UTC.
        from datetime import timedelta

        est = timezone(timedelta(hours=-5))
        ts = datetime(2026, 6, 4, 19, 0, tzinfo=est)
        self.assertEqual(
            day_align_utc(ts),
            datetime(2026, 6, 5, 0, 0, tzinfo=timezone.utc),
        )

    def test_naive_input_assumed_utc(self) -> None:
        # parse_history_timestamp always returns tz-aware, but defensive
        # pin: naive input is interpreted as UTC.
        ts = datetime(2026, 6, 4, 14, 30)  # naive
        self.assertEqual(
            day_align_utc(ts),
            datetime(2026, 6, 4, 0, 0, tzinfo=timezone.utc),
        )


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
        # ts is day-aligned to UTC midnight (B-109): the daily collector
        # used to write the call-time `now` directly, but that left the
        # natural-key PK on (asset_id, chain, ts) failing to dedupe across
        # multiple runs of the same logical day. Aligning to UTC midnight
        # collapses those duplicates.
        expected_ts = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
        for row in rows:
            self.assertEqual(row["ts"], expected_ts)
            # fetched_at retains the original call time (provenance), only
            # ts is day-aligned so the PK can enforce per-day uniqueness.
            self.assertEqual(row["fetched_at"], NOW)
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
            "chainCirculating": {
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

    def test_accepts_legacy_chain_balances_shape(self) -> None:
        payload = {
            "id": "1",
            "name": "Tether",
            "symbol": "USDT",
            "pegType": "peggedUSD",
            "chainBalances": {
                "Ethereum": {
                    "tokens": [
                        {"date": 1_700_000_000, "current": {"peggedUSD": 50_000_000_000}},
                    ]
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

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["chain"], "Ethereum")


# ---------------------------------------------------------------------------
# B-083 — fees + revenue series normalization
# ---------------------------------------------------------------------------


class NormalizeProtocolFeeSeriesTests(unittest.TestCase):
    """Parse `/summary/fees/{slug}` and `/summary/revenue/{slug}` payloads."""

    def test_parses_totaldatachart_into_one_row_per_ts(self) -> None:
        # Real DefiLlama shape: totalDataChart is a list of
        # [epoch_seconds, value_usd] pairs. ts is parsed as UTC datetime.
        payload = {
            "totalDataChart": [
                [1691366400, 3844.0],     # 2023-08-07
                [1691452800, 5120.5],     # 2023-08-08
                [1691539200, None],       # missing value → skipped
                [1691625600, 9999.0],     # 2023-08-10
            ],
        }
        rows = normalize_protocol_fee_series(
            payload,
            slug="chainlink-requests",
            value_field="fees_usd",
            source_endpoint="https://example.com/summary/fees/chainlink-requests",
            ingest_run_id=42,
            fetched_at=NOW,
        )
        self.assertEqual(len(rows), 3)  # one None value dropped
        first = rows[0]
        self.assertEqual(first["slug"], "chainlink-requests")
        self.assertEqual(first["fees_usd"], 3844.0)
        self.assertEqual(first["ts"], datetime(2023, 8, 7, tzinfo=timezone.utc))
        self.assertEqual(first["ingest_run_id"], 42)
        self.assertIn("source_endpoint", first)
        # value_field controls which column the value lands in
        self.assertNotIn("revenue_usd", first)

    def test_value_field_routes_revenue_to_revenue_column(self) -> None:
        payload = {"totalDataChart": [[1691366400, 100.0]]}
        rows = normalize_protocol_fee_series(
            payload,
            slug="aave-v3",
            value_field="revenue_usd",
            source_endpoint="x",
            ingest_run_id=1,
            fetched_at=NOW,
        )
        self.assertEqual(rows[0]["revenue_usd"], 100.0)
        self.assertNotIn("fees_usd", rows[0])

    def test_dedupes_within_one_payload(self) -> None:
        # Defensive — if a payload ever returns the same ts twice, keep
        # the first and drop the dup so the bulk_upsert doesn't fight.
        payload = {
            "totalDataChart": [
                [1691366400, 100.0],
                [1691366400, 999.0],  # dup ts — should be skipped
                [1691452800, 200.0],
            ],
        }
        rows = normalize_protocol_fee_series(
            payload,
            slug="aave-v3",
            value_field="fees_usd",
            source_endpoint="x",
            ingest_run_id=1,
            fetched_at=NOW,
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["fees_usd"], 100.0)  # first wins

    def test_missing_totaldatachart_returns_empty(self) -> None:
        # 400-error payloads or unexpected shapes don't crash the
        # normalizer — they just produce 0 rows.
        self.assertEqual(
            normalize_protocol_fee_series(
                {"error": "Not found"},
                slug="x",
                value_field="fees_usd",
                source_endpoint="x",
                ingest_run_id=1,
                fetched_at=NOW,
            ),
            [],
        )

    def test_non_dict_payload_returns_empty(self) -> None:
        # Robustness against unexpected JSON shapes
        self.assertEqual(
            normalize_protocol_fee_series(
                None,  # type: ignore[arg-type]
                slug="x",
                value_field="fees_usd",
                source_endpoint="x",
                ingest_run_id=1,
                fetched_at=NOW,
            ),
            [],
        )


class MergeFeeRevenueRowsTests(unittest.TestCase):
    """Verify fees + revenue merge into one row per (slug, ts)."""

    def _row(
        self,
        slug: str,
        ts: datetime,
        *,
        fees_usd: float | None = None,
        revenue_usd: float | None = None,
    ) -> dict:
        row = {
            "slug": slug,
            "ts": ts,
            "source_endpoint": "x",
            "fetched_at": NOW,
            "ingest_run_id": 1,
        }
        if fees_usd is not None:
            row["fees_usd"] = fees_usd
        if revenue_usd is not None:
            row["revenue_usd"] = revenue_usd
        return row

    def test_fees_only_merges_with_null_revenue(self) -> None:
        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        merged = merge_fee_revenue_rows(
            fees_rows=[self._row("aave-v3", ts, fees_usd=100.0)],
            revenue_rows=[],
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["fees_usd"], 100.0)
        self.assertIsNone(merged[0]["revenue_usd"])

    def test_revenue_only_merges_with_null_fees(self) -> None:
        # Shouldn't happen in practice (revenue without fees), but the
        # merge function shouldn't lose the row.
        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        merged = merge_fee_revenue_rows(
            fees_rows=[],
            revenue_rows=[self._row("aave-v3", ts, revenue_usd=50.0)],
        )
        self.assertEqual(len(merged), 1)
        self.assertIsNone(merged[0]["fees_usd"])
        self.assertEqual(merged[0]["revenue_usd"], 50.0)

    def test_both_present_merge_into_single_row(self) -> None:
        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        merged = merge_fee_revenue_rows(
            fees_rows=[self._row("aave-v3", ts, fees_usd=100.0)],
            revenue_rows=[self._row("aave-v3", ts, revenue_usd=30.0)],
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["fees_usd"], 100.0)
        self.assertEqual(merged[0]["revenue_usd"], 30.0)

    def test_different_ts_stay_as_separate_rows(self) -> None:
        ts1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
        ts2 = datetime(2024, 1, 2, tzinfo=timezone.utc)
        merged = merge_fee_revenue_rows(
            fees_rows=[
                self._row("aave-v3", ts1, fees_usd=100.0),
                self._row("aave-v3", ts2, fees_usd=200.0),
            ],
            revenue_rows=[self._row("aave-v3", ts2, revenue_usd=50.0)],
        )
        self.assertEqual(len(merged), 2)
        by_ts = {row["ts"]: row for row in merged}
        self.assertEqual(by_ts[ts1]["fees_usd"], 100.0)
        self.assertIsNone(by_ts[ts1]["revenue_usd"])
        self.assertEqual(by_ts[ts2]["fees_usd"], 200.0)
        self.assertEqual(by_ts[ts2]["revenue_usd"], 50.0)

    def test_different_slugs_at_same_ts_stay_separate(self) -> None:
        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        merged = merge_fee_revenue_rows(
            fees_rows=[
                self._row("aave-v3", ts, fees_usd=100.0),
                self._row("uniswap-v3", ts, fees_usd=999.0),
            ],
            revenue_rows=[],
        )
        self.assertEqual(len(merged), 2)
        self.assertEqual({row["slug"] for row in merged}, {"aave-v3", "uniswap-v3"})


class UpsertProtocolFeeRowsTests(unittest.TestCase):
    def test_fees_only_rows_do_not_update_revenue_usd_on_conflict(self) -> None:
        row = {
            "slug": "chainlink-requests",
            "ts": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "fees_usd": 100.0,
            "revenue_usd": None,
            "source_endpoint": "fees-url",
            "fetched_at": NOW,
            "ingest_run_id": 1,
        }

        with patch("genkei.normalize.defillama.db.bulk_upsert", return_value=1) as upsert:
            self.assertEqual(_upsert_protocol_fee_rows(object(), [row]), 1)

        kwargs = upsert.call_args.kwargs
        self.assertEqual(kwargs["conflict_keys"], ["slug", "ts"])
        self.assertNotIn("revenue_usd", kwargs["update_cols"])

    def test_rows_with_revenue_update_revenue_usd_on_conflict(self) -> None:
        row = {
            "slug": "chainlink-requests",
            "ts": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "fees_usd": 100.0,
            "revenue_usd": 25.0,
            "source_endpoint": "fees-url",
            "fetched_at": NOW,
            "ingest_run_id": 1,
        }

        with patch("genkei.normalize.defillama.db.bulk_upsert", return_value=1) as upsert:
            self.assertEqual(_upsert_protocol_fee_rows(object(), [row]), 1)

        update_cols = upsert.call_args.kwargs["update_cols"]
        self.assertIn("fees_usd", update_cols)
        self.assertIn("revenue_usd", update_cols)

    def test_revenue_only_rows_do_not_update_fees_usd_on_conflict(self) -> None:
        row = {
            "slug": "chainlink-requests",
            "ts": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "fees_usd": None,
            "revenue_usd": 25.0,
            "source_endpoint": "revenue-url",
            "fetched_at": NOW,
            "ingest_run_id": 1,
        }

        with patch("genkei.normalize.defillama.db.bulk_upsert", return_value=1) as upsert:
            self.assertEqual(_upsert_protocol_fee_rows(object(), [row]), 1)

        update_cols = upsert.call_args.kwargs["update_cols"]
        self.assertIn("revenue_usd", update_cols)
        self.assertNotIn("fees_usd", update_cols)


if __name__ == "__main__":
    unittest.main()
