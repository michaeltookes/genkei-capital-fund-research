"""Unit tests for the SUI unlocks collector (B-089; DeFiLlama port B-145)."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from genkei.ingest.sui_unlocks import (
    COLLECT_ENDPOINT_LABEL,
    LLAMA_SUI_EMISSIONS_URL,
    SOURCE_NAME,
    _coerce_decimal,
    _ts_to_utc_date,
    collect,
    parse_unlock_rows,
)

# Unix-seconds day stamps (00:00 UTC).
_D0 = 1683072000  # 2023-05-03 — SUI TGE
_D1 = 1683158400  # 2023-05-04
_D2 = 1683244800  # 2023-05-05
_D3 = 1683331200  # 2023-05-06

# Minimal-but-realistic fragment of the DeFiLlama emissions payload
# (defillama-datasets.llama.fi/emissions/sui, probed 2026-09-18). Each
# series is a cumulative unlocked curve sampled daily; floats appear
# upstream (e.g. 49720000.000999995) so the fixture keeps one.
SAMPLE_PAYLOAD = {
    "name": "Sui",
    "gecko_id": "sui",
    "documentedData": {
        "data": [
            {
                # One TGE cliff, then flat: exactly one unlock row.
                "label": "ICO $0.03",
                "data": [
                    {"timestamp": _D0, "unlocked": 0},
                    {"timestamp": _D1, "unlocked": 138000000},
                    {"timestamp": _D2, "unlocked": 138000000},
                    {"timestamp": _D3, "unlocked": 138000000},
                ],
            },
            {
                # TGE cliff + one later batch (with float noise upstream).
                "label": "Community Reserve",
                "data": [
                    {"timestamp": _D0, "unlocked": 0},
                    {"timestamp": _D1, "unlocked": 49720000.000999995},
                    {"timestamp": _D2, "unlocked": 49720000.000999995},
                    {"timestamp": _D3, "unlocked": 111000000.001},
                ],
            },
            {
                # Fully locked through the fixture window: zero rows, but the
                # final cumulative still counts toward the supply total.
                "label": "Series A",
                "data": [
                    {"timestamp": _D0, "unlocked": 0},
                    {"timestamp": _D3, "unlocked": 0},
                ],
            },
        ],
    },
}


class _FakeHttp:
    """Feeds one scripted JSON body to collect()."""

    def __init__(self, body: object) -> None:
        self._body = body
        self.calls: list[str] = []

    def get(self, url: str) -> object:
        self.calls.append(url)
        body = self._body

        class _Resp:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> object:
                return body

        return _Resp()


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class ModuleConstantsTests(unittest.TestCase):
    """Pin the module-level constants the workflow + health check depend on."""

    def test_source_name(self) -> None:
        """Stable source name keyed in RECURRING_ENDPOINTS + PRIMARY_TABLES."""
        self.assertEqual(SOURCE_NAME, "sui_unlocks")

    def test_collect_endpoint_label_follows_convention(self) -> None:
        """'collect' matches the universal convention pinned by test_watchlist_cmd."""
        self.assertEqual(COLLECT_ENDPOINT_LABEL, "collect")

    def test_url_is_open_datasets_bucket(self) -> None:
        """B-145 port: the api.llama.fi emission endpoints are paid (402);
        the datasets bucket is the free path the DeFiLlama frontend uses."""
        self.assertEqual(
            LLAMA_SUI_EMISSIONS_URL,
            "https://defillama-datasets.llama.fi/emissions/sui",
        )


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------


class CoerceDecimalTests(unittest.TestCase):
    """Upstream mixes ints and floats; storage is Decimal."""

    def test_float_via_str_round_trip(self) -> None:
        """Floats convert via str() to avoid binary-float noise."""
        self.assertEqual(
            _coerce_decimal(49720000.000999995), Decimal("49720000.000999995")
        )

    def test_int(self) -> None:
        """Ints pass through."""
        self.assertEqual(_coerce_decimal(138000000), Decimal(138000000))

    def test_none_and_garbage(self) -> None:
        """Missing / non-numeric become None."""
        self.assertIsNone(_coerce_decimal(None))
        self.assertIsNone(_coerce_decimal("not-a-number"))
        self.assertIsNone(_coerce_decimal(True))


class TsToUtcDateTests(unittest.TestCase):
    """Unix-seconds timestamps → UTC dates."""

    def test_known_stamp(self) -> None:
        """SUI TGE stamp decodes to 2023-05-03."""
        self.assertEqual(_ts_to_utc_date(_D0), date(2023, 5, 3))

    def test_string_stamp(self) -> None:
        """String-encoded stamps (defensive) also decode."""
        self.assertEqual(_ts_to_utc_date(str(_D0)), date(2023, 5, 3))

    def test_none_and_garbage(self) -> None:
        """Missing / unparseable yield None instead of raising."""
        self.assertIsNone(_ts_to_utc_date(None))
        self.assertIsNone(_ts_to_utc_date("garbage"))


# ---------------------------------------------------------------------------
# parse_unlock_rows — cumulative curves → batch rows
# ---------------------------------------------------------------------------


class ParseUnlockRowsTests(unittest.TestCase):
    """Delta extraction, dating, TGE flag, and derived totals."""

    def test_one_row_per_positive_delta(self) -> None:
        """ICO cliff → 1 row; Community Reserve cliff + batch → 2 rows;
        fully locked Series A → 0 rows."""
        rows = parse_unlock_rows(SAMPLE_PAYLOAD)
        by_alloc: dict[str, list] = {}
        for r in rows:
            by_alloc.setdefault(r.allocation_name, []).append(r)
        self.assertEqual(len(by_alloc.get("ICO $0.03", [])), 1)
        self.assertEqual(len(by_alloc.get("Community Reserve", [])), 2)
        self.assertNotIn("Series A", by_alloc)

    def test_delta_dated_at_earlier_sample(self) -> None:
        """Cumulative is 'unlocked as of start of day' — the increment
        between D0 and D1 belongs to D0 (validated live: TGE cliffs land
        exactly on 2023-05-03)."""
        rows = parse_unlock_rows(SAMPLE_PAYLOAD)
        ico = [r for r in rows if r.allocation_name == "ICO $0.03"]
        self.assertEqual(ico[0].unlock_date, date(2023, 5, 3))

    def test_tge_flag_only_on_schedule_start(self) -> None:
        """is_tge marks only rows dated at the global schedule start."""
        rows = parse_unlock_rows(SAMPLE_PAYLOAD)
        for r in rows:
            self.assertEqual(r.is_tge, r.unlock_date == date(2023, 5, 3))
        cr = {r.unlock_date: r for r in rows if r.allocation_name == "Community Reserve"}
        self.assertTrue(cr[date(2023, 5, 3)].is_tge)
        self.assertFalse(cr[date(2023, 5, 5)].is_tge)

    def test_unlock_tokens_are_the_delta(self) -> None:
        """unlock_tokens is the day-over-day cumulative increment."""
        rows = parse_unlock_rows(SAMPLE_PAYLOAD)
        cr = {r.unlock_date: r for r in rows if r.allocation_name == "Community Reserve"}
        self.assertEqual(cr[date(2023, 5, 3)].unlock_tokens, Decimal("49720000.0010"))
        self.assertEqual(cr[date(2023, 5, 5)].unlock_tokens, Decimal("61280000.0000"))

    def test_percent_of_supply_derived_from_series_totals(self) -> None:
        """Supply = sum of final cumulatives (138M + 111M + 0 = 249M here);
        no hardcoded 10B constant."""
        rows = parse_unlock_rows(SAMPLE_PAYLOAD)
        ico = next(r for r in rows if r.allocation_name == "ICO $0.03")
        expected = (Decimal(138000000) / Decimal("249000000.001") * 100).quantize(
            Decimal("0.0001")
        )
        self.assertEqual(ico.allocation_total_percent_of_supply, expected)

    def test_percent_of_allocation(self) -> None:
        """unlock_percent_of_allocation is delta / allocation final total."""
        rows = parse_unlock_rows(SAMPLE_PAYLOAD)
        ico = next(r for r in rows if r.allocation_name == "ICO $0.03")
        self.assertEqual(ico.unlock_percent_of_allocation, Decimal("100.0000"))

    def test_vesting_type_null_for_llama_rows(self) -> None:
        """The cumulative curves can't distinguish cliff vs linear — NULL."""
        rows = parse_unlock_rows(SAMPLE_PAYLOAD)
        for r in rows:
            self.assertIsNone(r.vesting_type)

    def test_malformed_point_dropped_not_fatal(self) -> None:
        """A bad point inside a series is skipped; the series survives."""
        payload = {
            "documentedData": {
                "data": [
                    {
                        "label": "Messy",
                        "data": [
                            {"timestamp": _D0, "unlocked": 0},
                            {"timestamp": "garbage", "unlocked": 10},
                            {"timestamp": _D2, "unlocked": 50},
                        ],
                    },
                ],
            },
        }
        rows = parse_unlock_rows(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].unlock_tokens, Decimal("50.0000"))

    def test_missing_documented_data_raises(self) -> None:
        """A payload without documentedData.data is unusable — raise loudly."""
        with self.assertRaises(ValueError):
            parse_unlock_rows({"name": "Sui"})

    def test_empty_series_list_raises(self) -> None:
        """An empty data list means upstream broke — raise, don't write 0 rows."""
        with self.assertRaises(ValueError):
            parse_unlock_rows({"documentedData": {"data": []}})

    def test_all_series_unusable_raises(self) -> None:
        """Series lacking labels/points must not silently produce nothing."""
        payload = {
            "documentedData": {
                "data": [
                    {"label": "", "data": []},
                    {"data": [{"timestamp": _D0, "unlocked": 1}]},
                ],
            },
        }
        with self.assertRaises(ValueError):
            parse_unlock_rows(payload)


# ---------------------------------------------------------------------------
# collect — orchestration
# ---------------------------------------------------------------------------


class CollectTests(unittest.TestCase):
    """Collector orchestration paths not covered by parser-only tests."""

    def test_happy_path(self) -> None:
        """Rows land via bulk_upsert on the (allocation_name, unlock_date) PK."""

        class FakeRun:
            id = 61

            def add_rows(self, n: int) -> None:
                self._added = n

        fake_run = FakeRun()
        http = _FakeHttp(SAMPLE_PAYLOAD)
        with (
            patch("genkei.ingest.sui_unlocks.db.ingest_run") as ingest_run_cm,
            patch("genkei.ingest.sui_unlocks.db.record_partial_endpoints") as partial,
            patch("genkei.ingest.sui_unlocks.db.store_raw_blob") as store_blob,
            patch("genkei.ingest.sui_unlocks.db.connection"),
            patch(
                "genkei.ingest.sui_unlocks.db.bulk_upsert", return_value=3
            ) as bulk_upsert,
        ):
            ingest_run_cm.return_value.__enter__.return_value = fake_run
            ingest_run_cm.return_value.__exit__.return_value = False

            self.assertEqual(collect(http=http), 61)

        self.assertEqual(http.calls, [LLAMA_SUI_EMISSIONS_URL])
        partial.assert_not_called()
        store_blob.assert_called_once_with(
            61, COLLECT_ENDPOINT_LABEL, LLAMA_SUI_EMISSIONS_URL, SAMPLE_PAYLOAD
        )
        rows = bulk_upsert.call_args.args[2]
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            bulk_upsert.call_args.kwargs["conflict_keys"],
            ("allocation_name", "unlock_date"),
        )
        self.assertTrue(
            all(row["source_endpoint"] == LLAMA_SUI_EMISSIONS_URL for row in rows)
        )
        self.assertEqual(fake_run._added, 3)

    def test_parse_failure_recorded_before_reraising(self) -> None:
        """A structurally broken payload records a partial and raises."""

        class FakeRun:
            id = 62

            def add_rows(self, n: int) -> None:
                self._added = n

        fake_run = FakeRun()
        http = _FakeHttp({"name": "Sui"})
        with (
            patch("genkei.ingest.sui_unlocks.db.ingest_run") as ingest_run_cm,
            patch("genkei.ingest.sui_unlocks.db.record_partial_endpoints") as partial,
            patch("genkei.ingest.sui_unlocks.db.store_raw_blob") as store_blob,
            patch("genkei.ingest.sui_unlocks.db.connection") as connection_cm,
            patch("genkei.ingest.sui_unlocks.db.bulk_upsert") as bulk_upsert,
        ):
            ingest_run_cm.return_value.__enter__.return_value = fake_run
            ingest_run_cm.return_value.__exit__.return_value = False

            with self.assertRaisesRegex(
                RuntimeError, "DeFiLlama SUI emissions parse failed"
            ):
                collect(http=http)

        store_blob.assert_called_once()
        partial_args = partial.call_args.args
        self.assertEqual(partial_args[0], 62)
        self.assertEqual(partial_args[1][0]["name"], COLLECT_ENDPOINT_LABEL)
        self.assertIn("documentedData", partial_args[1][0]["error"])
        connection_cm.assert_not_called()
        bulk_upsert.assert_not_called()
        self.assertFalse(hasattr(fake_run, "_added"))


if __name__ == "__main__":
    unittest.main()
