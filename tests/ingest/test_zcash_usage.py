"""Unit tests for the Zcash shielded-pool usage collector."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from genkei.ingest.zcash_usage import (
    BLOCKCHAIN_INFO_URL,
    COLLECT_ENDPOINT_LABEL,
    SHIELDED_POOLS,
    SOURCE_NAME,
    _coerce_decimal,
    collect,
    parse_value_pools,
)
from tests.helpers import FakeIngestRun

# A realistic fragment of the zcashexplorer.app blockchain-info response
# (shape + values verified live 2026-07-07). Includes the transparent pool,
# the three shielded pools (sprout/sapling/orchard), and the dev-fund lockbox.
LIVE_PAYLOAD = {
    "blocks": 3404192,
    "chain": "main",
    "chainSupply": {"chainValue": 16808682.98, "monitored": True},
    "valuePools": [
        {"id": "transparent", "chainValue": 12347955.04, "monitored": True},
        {"id": "sprout", "chainValue": 25409.42, "monitored": True},
        {"id": "sapling", "chainValue": 620043.04, "monitored": True},
        {"id": "orchard", "chainValue": 3766939.29, "monitored": True},
        {"id": "lockbox", "chainValue": 48336.19, "monitored": True},
    ],
}


class ModuleConstantsTests(unittest.TestCase):
    def test_source_name(self) -> None:
        self.assertEqual(SOURCE_NAME, "zcash_usage")

    def test_shielded_pools_are_the_privacy_pools(self) -> None:
        """sprout/sapling/orchard are private; transparent + lockbox are not."""
        self.assertEqual(SHIELDED_POOLS, frozenset({"sprout", "sapling", "orchard"}))
        self.assertNotIn("transparent", SHIELDED_POOLS)
        self.assertNotIn("lockbox", SHIELDED_POOLS)

    def test_url_is_zcashexplorer_blockchain_info(self) -> None:
        self.assertTrue(BLOCKCHAIN_INFO_URL.startswith("https://mainnet.zcashexplorer.app/"))
        self.assertIn("blockchain-info", BLOCKCHAIN_INFO_URL)


class CoerceDecimalTests(unittest.TestCase):
    def test_float_and_int(self) -> None:
        self.assertEqual(_coerce_decimal(3766939.29), Decimal("3766939.29"))
        self.assertEqual(_coerce_decimal(0), Decimal("0"))

    def test_string(self) -> None:
        self.assertEqual(_coerce_decimal("620043.04"), Decimal("620043.04"))

    def test_negative_rejected(self) -> None:
        """A negative pool value is nonsensical → None (skipped upstream)."""
        self.assertIsNone(_coerce_decimal(-1.0))

    def test_none_and_bool_and_garbage(self) -> None:
        self.assertIsNone(_coerce_decimal(None))
        self.assertIsNone(_coerce_decimal(True))  # bool is not a numeric here
        self.assertIsNone(_coerce_decimal("not-a-number"))
        self.assertIsNone(_coerce_decimal(""))


class ParseValuePoolsTests(unittest.TestCase):
    _SNAP = date(2026, 7, 7)

    def test_extracts_all_five_pools(self) -> None:
        snaps = parse_value_pools(LIVE_PAYLOAD, snapshot_date=self._SNAP)
        self.assertEqual(
            sorted(s.pool for s in snaps),
            ["lockbox", "orchard", "sapling", "sprout", "transparent"],
        )

    def test_shielded_classification(self) -> None:
        snaps = {s.pool: s for s in parse_value_pools(LIVE_PAYLOAD, snapshot_date=self._SNAP)}
        self.assertTrue(snaps["orchard"].shielded)
        self.assertTrue(snaps["sapling"].shielded)
        self.assertTrue(snaps["sprout"].shielded)
        self.assertFalse(snaps["transparent"].shielded)
        self.assertFalse(snaps["lockbox"].shielded)

    def test_shielded_share_matches_expected(self) -> None:
        """(sprout+sapling+orchard) / total ≈ 26.3% for the live fragment."""
        snaps = parse_value_pools(LIVE_PAYLOAD, snapshot_date=self._SNAP)
        shielded = sum(s.chain_value_zec for s in snaps if s.shielded)
        total = sum(s.chain_value_zec for s in snaps)
        share = shielded / total * 100
        self.assertAlmostEqual(float(share), 26.3, places=1)

    def test_carries_block_height_and_snapshot_date(self) -> None:
        snaps = parse_value_pools(LIVE_PAYLOAD, snapshot_date=self._SNAP)
        self.assertTrue(all(s.block_height == 3404192 for s in snaps))
        self.assertTrue(all(s.snapshot_date == self._SNAP for s in snaps))

    def test_value_quantized_to_8dp(self) -> None:
        snaps = {s.pool: s for s in parse_value_pools(LIVE_PAYLOAD, snapshot_date=self._SNAP)}
        self.assertEqual(snaps["orchard"].chain_value_zec, Decimal("3766939.29000000"))

    def test_skips_pool_with_negative_value(self) -> None:
        payload = {
            "blocks": 1,
            "valuePools": [
                {"id": "transparent", "chainValue": 100.0},
                {"id": "orchard", "chainValue": -5.0},  # bad → skipped
            ],
        }
        snaps = parse_value_pools(payload, snapshot_date=self._SNAP)
        self.assertEqual([s.pool for s in snaps], ["transparent"])

    def test_missing_block_height_is_none(self) -> None:
        payload = {"valuePools": [{"id": "orchard", "chainValue": 10.0}]}
        snaps = parse_value_pools(payload, snapshot_date=self._SNAP)
        self.assertIsNone(snaps[0].block_height)

    def test_non_dict_payload_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_value_pools([], snapshot_date=self._SNAP)  # type: ignore[arg-type]

    def test_missing_value_pools_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_value_pools({"blocks": 1}, snapshot_date=self._SNAP)


class CollectTests(unittest.TestCase):
    _SNAP = date(2026, 7, 7)

    def test_zero_usable_value_pools_fail_ingest_run(self) -> None:
        payload = {
            "blocks": 3404192,
            "valuePools": [
                {"id": "orchard", "chainValue": "not-a-number"},
                {"id": "", "chainValue": 10},
                "not-a-pool-object",
            ],
        }

        class FakeHttp:
            def get_json(self, url: str) -> object:
                self.url = url
                return payload

        fake_run = FakeIngestRun(42)
        http = FakeHttp()
        with (
            patch("genkei.ingest.zcash_usage.db.ingest_run", return_value=fake_run),
            patch("genkei.ingest.zcash_usage.db.store_raw_blob") as store_blob,
            patch("genkei.ingest.zcash_usage.db.record_partial_endpoints") as partial,
            patch("genkei.ingest.zcash_usage.db.connection") as connection_cm,
            patch("genkei.ingest.zcash_usage.db.bulk_upsert") as bulk_upsert,
            self.assertRaisesRegex(RuntimeError, "no usable valuePools"),
        ):
            collect(http=http, snapshot_date=self._SNAP)

        self.assertEqual(http.url, BLOCKCHAIN_INFO_URL)
        store_blob.assert_called_once_with(
            42, COLLECT_ENDPOINT_LABEL, BLOCKCHAIN_INFO_URL, payload
        )
        partial.assert_called_once_with(
            42,
            [
                {
                    "name": COLLECT_ENDPOINT_LABEL,
                    "url": BLOCKCHAIN_INFO_URL,
                    "error": "blockchain-info payload produced no usable valuePools",
                }
            ],
        )
        connection_cm.assert_not_called()
        bulk_upsert.assert_not_called()
        self.assertEqual(fake_run.rows_added, 0)


if __name__ == "__main__":
    unittest.main()
