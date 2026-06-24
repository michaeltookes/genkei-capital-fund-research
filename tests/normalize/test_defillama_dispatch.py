"""Offline tests for the DeFiLlama raw-blob dispatch table (B-121).

These pin the prefix-routing that ``core.normalize`` / ``core.normalize_backfill``
rely on. The integration tests that exercise those orchestrators need Postgres
and are skipped in the offline suite, so the load-bearing, collision-prone bit
— that ``protocol_fees_`` / ``protocol_revenue_`` route correctly despite both
starting with the generic ``protocol_`` history prefix — is verified here
without a database.
"""

from __future__ import annotations

import unittest

from genkei.normalize.defillama.dispatch import BLOB_ROUTES, classify_blob


class ClassifyBlobTests(unittest.TestCase):
    def test_protocol_history_strips_slug(self) -> None:
        self.assertEqual(classify_blob("protocol_jupiter"), ("protocol_history", "jupiter"))

    def test_fees_take_precedence_over_generic_protocol_prefix(self) -> None:
        # "protocol_fees_jupiter" starts with both "protocol_fees_" and the
        # generic "protocol_" — the more specific fees route must win.
        self.assertEqual(
            classify_blob("protocol_fees_jupiter"), ("protocol_fees", "jupiter")
        )

    def test_revenue_take_precedence_over_generic_protocol_prefix(self) -> None:
        self.assertEqual(
            classify_blob("protocol_revenue_jupiter"),
            ("protocol_revenue", "jupiter"),
        )

    def test_price_historical(self) -> None:
        self.assertEqual(
            classify_blob("prices_historical_bitcoin"),
            ("price_historical", "bitcoin"),
        )

    def test_stablecoin_history_strips_asset_id(self) -> None:
        self.assertEqual(
            classify_blob("stablecoin_1"), ("stablecoin_history", "1")
        )

    def test_unknown_prefix_returns_none(self) -> None:
        self.assertIsNone(classify_blob("protocols"))
        self.assertIsNone(classify_blob("chain_tvl_history_ethereum"))
        self.assertIsNone(classify_blob("stablecoins"))

    def test_fees_route_ordered_before_history_route(self) -> None:
        # Guard the ordering invariant directly so a future reorder of
        # BLOB_ROUTES that breaks precedence fails loudly here.
        kinds = [r.kind for r in BLOB_ROUTES]
        self.assertLess(kinds.index("protocol_fees"), kinds.index("protocol_history"))
        self.assertLess(kinds.index("protocol_revenue"), kinds.index("protocol_history"))


if __name__ == "__main__":
    unittest.main()
