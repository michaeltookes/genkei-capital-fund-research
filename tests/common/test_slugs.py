"""Tests for shared slug helpers that define persisted blob endpoint names."""

from __future__ import annotations

import unittest

from genkei.common.slugs import blob_slug_part


class BlobSlugPartTests(unittest.TestCase):
    def test_normalizes_blob_endpoint_parts(self) -> None:
        cases = {
            "/v1/accounting/dts/operating_cash_balance": (
                "v1_accounting_dts_operating_cash_balance"
            ),
            "/v2/accounting/od/interest_expense": "v2_accounting_od_interest_expense",
            "record_date": "record_date",
            "WTI_SPOT": "wti_spot",
            "CRUDE INV": "crude_inv",
            "/natural-gas/stor/wkly/": "natural-gas_stor_wkly",
            "///foo//bar///": "foo__bar",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(blob_slug_part(raw), expected)


if __name__ == "__main__":
    unittest.main()
