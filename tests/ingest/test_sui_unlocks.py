"""Unit tests for the SUI unlocks collector (B-089)."""

from __future__ import annotations

import unittest
from copy import deepcopy
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from genkei.ingest.sui_unlocks import (
    COLLECT_ENDPOINT_LABEL,
    CRYPTORANK_SUI_VESTING_URL,
    KNOWN_FREE_ALLOCATIONS,
    SOURCE_NAME,
    _coerce_decimal,
    _parse_iso_date,
    collect,
    extract_next_data,
    parse_allocations,
)

# Minimal-but-realistic fragment of CryptoRank's __NEXT_DATA__ payload.
# Field shape matches the live page returned on 2026-06-07.  Includes
# Community Reserves (the only allocation publicly exposed) and a
# second "Series A" entry with batches=None to confirm the gating-
# detection skip path: free-tier callers see allocation name +
# percent-of-supply but the batches list is absent for paywalled
# categories.
SAMPLE_NEXT_DATA = {
    "props": {
        "pageProps": {
            "vestingInfo": {
                "allocations": [
                    {
                        # Trailing space deliberate — matches the live
                        # upstream value; the parser must strip it before
                        # matching against KNOWN_FREE_ALLOCATIONS.
                        "name": "Community Reserves ",
                        "tokens_percent": 10.648,
                        "tokens": 1064795909,
                        "unlock_type": "linear",
                        "unlock_frequency_type": "month",
                        "unlock_frequency_value": 1,
                        "vesting_duration_type": "year",
                        "vesting_duration_value": 7,
                        "round_date": "2023-05-03T00:00:00.000Z",
                        "batches": [
                            {
                                "date": "2023-05-03T00:00:00.000Z",
                                "is_tge": True,
                                "unlock_percent": 29.555,
                            },
                            {
                                "date": "2023-06-01T00:00:00.000Z",
                                "is_tge": False,
                                "unlock_percent": 0,
                            },
                            {
                                "date": "2023-07-01T00:00:00.000Z",
                                "is_tge": False,
                                "unlock_percent": 0.376,
                            },
                            # Defective batch — missing date — must be skipped.
                            {"is_tge": False, "unlock_percent": 0.5},
                            # Same-day special-event row — must aggregate with
                            # the monthly row instead of being dropped.
                            {
                                "date": "2023-07-01T00:00:00.000Z",
                                "is_tge": False,
                                "unlock_percent": 0.124,
                            },
                        ],
                    },
                    {
                        # Paywalled allocation — name + percent published,
                        # but batches not in the SSR payload (gated behind
                        # auth on cryptorank.io). Must be silently skipped
                        # by the parser so collection of the free allocation
                        # still succeeds.
                        "name": "Series A",
                        "tokens_percent": 7.142,
                        "tokens": 714200000,
                        "unlock_type": "linear",
                        "batches": None,
                    },
                ]
            }
        }
    }
}


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class ModuleConstantsTests(unittest.TestCase):
    """Pin the constants the workflow + health checks depend on."""

    def test_source_name(self) -> None:
        """Source name is what PRIMARY_TABLES + RECURRING_ENDPOINTS key on."""
        self.assertEqual(SOURCE_NAME, "sui_unlocks")

    def test_collect_endpoint_label_follows_convention(self) -> None:
        """'collect' matches the universal convention pinned by test_watchlist_cmd."""
        self.assertEqual(COLLECT_ENDPOINT_LABEL, "collect")

    def test_cryptorank_url_pinned(self) -> None:
        """Source URL is the public CryptoRank SUI vesting page."""
        self.assertEqual(
            CRYPTORANK_SUI_VESTING_URL,
            "https://cryptorank.io/price/sui/vesting",
        )

    def test_known_free_allocations_v1_scope(self) -> None:
        """v1 scope: only the Community Reserves allocation is free.

        Pinned explicitly so a contributor adding a new allocation here
        has to update tests + docs/sources/sui-unlocks.md + the resolved.md
        entry in lockstep, rather than silently changing what gets ingested.
        """
        self.assertEqual(KNOWN_FREE_ALLOCATIONS, ("Community Reserves",))


# ---------------------------------------------------------------------------
# Coercion + date parsing helpers
# ---------------------------------------------------------------------------


class CoerceDecimalTests(unittest.TestCase):
    """Numeric values come from JSON — could be int / float / numeric string."""

    def test_native_float(self) -> None:
        """JSON floats (the common case for percentages) round-trip via str."""
        self.assertEqual(_coerce_decimal(10.648), Decimal("10.648"))

    def test_native_int(self) -> None:
        """JSON ints become Decimal."""
        self.assertEqual(_coerce_decimal(1064795909), Decimal("1064795909"))

    def test_numeric_string(self) -> None:
        """Defensive: parse stringified numerics."""
        self.assertEqual(_coerce_decimal("0.376"), Decimal("0.376"))

    def test_none(self) -> None:
        """Missing values yield None, not a default zero."""
        self.assertIsNone(_coerce_decimal(None))

    def test_bool_rejected(self) -> None:
        """is_tge is bool — Decimal coercion of True/False must not silently equal 1/0."""
        self.assertIsNone(_coerce_decimal(True))
        self.assertIsNone(_coerce_decimal(False))


class ParseIsoDateTests(unittest.TestCase):
    """CryptoRank stamps every batch at midnight UTC; we truncate to date."""

    def test_iso_with_z_suffix(self) -> None:
        """The dominant format: '2023-05-03T00:00:00.000Z'."""
        self.assertEqual(
            _parse_iso_date("2023-05-03T00:00:00.000Z"), date(2023, 5, 3)
        )

    def test_bare_yyyy_mm_dd_fallback(self) -> None:
        """Defensive: a bare date string still parses."""
        self.assertEqual(_parse_iso_date("2026-06-07"), date(2026, 6, 7))

    def test_none_and_empty(self) -> None:
        """Missing / blank yield None rather than raising."""
        self.assertIsNone(_parse_iso_date(None))
        self.assertIsNone(_parse_iso_date(""))

    def test_garbage_returns_none(self) -> None:
        """Unparseable input yields None rather than raising."""
        self.assertIsNone(_parse_iso_date("tomorrow"))


# ---------------------------------------------------------------------------
# extract_next_data
# ---------------------------------------------------------------------------


class ExtractNextDataTests(unittest.TestCase):
    """Pull the __NEXT_DATA__ JSON blob out of a Next.js HTML page."""

    def test_extracts_embedded_json(self) -> None:
        """The id=__NEXT_DATA__ script tag's JSON content is returned parsed."""
        html = (
            '<html><body>...'
            '<script id="__NEXT_DATA__" type="application/json">'
            '{"props": {"pageProps": {"hello": "world"}}}'
            '</script></body></html>'
        )
        out = extract_next_data(html)
        self.assertEqual(out["props"]["pageProps"]["hello"], "world")

    def test_missing_script_raises(self) -> None:
        """A page without the __NEXT_DATA__ tag is a CryptoRank shape break."""
        with self.assertRaisesRegex(ValueError, "missing the __NEXT_DATA__"):
            extract_next_data("<html><body>no data here</body></html>")

    def test_unparseable_json_raises(self) -> None:
        """Malformed JSON inside the tag raises ValueError, not crashes silently."""
        html = (
            '<script id="__NEXT_DATA__" type="application/json">'
            "{not-valid-json"
            "</script>"
        )
        with self.assertRaisesRegex(ValueError, "failed to parse as JSON"):
            extract_next_data(html)


# ---------------------------------------------------------------------------
# parse_allocations — the load-bearing extractor
# ---------------------------------------------------------------------------


class ParseAllocationsTests(unittest.TestCase):
    """End-to-end allocation → batch row decode."""

    def test_extracts_community_reserves_batches(self) -> None:
        """Community Reserves batches with valid date+pct land as rows."""
        rows = parse_allocations(SAMPLE_NEXT_DATA)
        # Sample has 3 valid batches (2023-05-03, 2023-06-01, 2023-07-01),
        # 1 defective (missing date), 1 duplicate (same date as the third).
        # Defective + duplicate both drop → 3 rows total.
        self.assertEqual(len(rows), 3)
        dates = sorted(r.unlock_date for r in rows)
        self.assertEqual(
            dates, [date(2023, 5, 3), date(2023, 6, 1), date(2023, 7, 1)]
        )

    def test_strips_trailing_space_from_allocation_name(self) -> None:
        """CryptoRank's 'Community Reserves ' (trailing space) lands stripped.

        Without the strip, the PK on (allocation_name, unlock_date) would
        diverge between collector runs based on whether the upstream value
        had whitespace, breaking idempotency.
        """
        rows = parse_allocations(SAMPLE_NEXT_DATA)
        for r in rows:
            self.assertEqual(r.allocation_name, "Community Reserves")

    def test_skips_allocation_not_in_known_free_set(self) -> None:
        """Series A is in the payload but not in KNOWN_FREE_ALLOCATIONS — skipped."""
        rows = parse_allocations(SAMPLE_NEXT_DATA)
        names = {r.allocation_name for r in rows}
        self.assertNotIn("Series A", names)

    def test_skips_allocation_with_no_batches_list(self) -> None:
        """An allocation whose batches field is None (paywalled) is silently skipped."""
        # Even if we did want Series A — the gating means batches=None — it
        # would still skip; we'd just log a warning. Confirm by including
        # Series A in the allowed set and rerunning.
        rows = parse_allocations(
            SAMPLE_NEXT_DATA,
            allowed_allocations=("Community Reserves", "Series A"),
        )
        names = {r.allocation_name for r in rows}
        self.assertNotIn("Series A", names)
        # Community Reserves still extracted normally
        self.assertIn("Community Reserves", names)

    def test_unlock_tokens_derived_at_4_decimals(self) -> None:
        """unlock_tokens = allocation_total_tokens * unlock_percent / 100."""
        rows = parse_allocations(SAMPLE_NEXT_DATA)
        by_date = {r.unlock_date: r for r in rows}
        # 1,064,795,909 * 29.555 / 100 = 314,700,430.9050 (quantized 4 dp)
        tge = by_date[date(2023, 5, 3)]
        self.assertEqual(tge.unlock_tokens, Decimal("314700430.9050"))
        # Zero-pct batch lands as 0.0000
        self.assertEqual(by_date[date(2023, 6, 1)].unlock_tokens, Decimal("0.0000"))

    def test_is_tge_flag_carried_correctly(self) -> None:
        """is_tge=True on the TGE batch; False on subsequent batches."""
        rows = parse_allocations(SAMPLE_NEXT_DATA)
        by_date = {r.unlock_date: r for r in rows}
        self.assertTrue(by_date[date(2023, 5, 3)].is_tge)
        self.assertFalse(by_date[date(2023, 6, 1)].is_tge)
        self.assertFalse(by_date[date(2023, 7, 1)].is_tge)

    def test_vesting_type_lowercased(self) -> None:
        """unlock_type 'linear' / 'LINEAR' both normalize to lowercase."""
        rows = parse_allocations(SAMPLE_NEXT_DATA)
        self.assertEqual(rows[0].vesting_type, "linear")

    def test_duplicate_date_aggregates_same_day_non_zero_batches(self) -> None:
        """Same-day non-zero rows are aggregated into one output row.

        Upstream occasionally publishes overlapping monthly + special-event
        batches on the same date; aggregating the percentages lets the PK on
        (allocation_name, unlock_date) doesn't collide at insert time and
        the bulk_upsert doesn't see two rows with identical conflict keys
        in a single batch (which psycopg's COPY-based upsert hates).
        """
        rows = parse_allocations(SAMPLE_NEXT_DATA)
        by_date = {r.unlock_date: r for r in rows}
        # 2023-07-01 has a 0.376 monthly row plus a 0.124 special-event row.
        self.assertEqual(
            by_date[date(2023, 7, 1)].unlock_percent_of_allocation,
            Decimal("0.500"),
        )
        self.assertEqual(
            by_date[date(2023, 7, 1)].unlock_tokens,
            Decimal("5323979.5450"),
        )

    def test_duplicate_date_preserves_first_non_zero_occurrence(self) -> None:
        """A zero placeholder does not block a later same-date non-zero batch."""
        payload = deepcopy(SAMPLE_NEXT_DATA)
        batches = payload["props"]["pageProps"]["vestingInfo"]["allocations"][0]["batches"]
        batches.insert(
            3,
            {
                "date": "2023-08-01T00:00:00.000Z",
                "is_tge": False,
                "unlock_percent": 0,
            },
        )
        batches.insert(
            4,
            {
                "date": "2023-08-01T00:00:00.000Z",
                "is_tge": False,
                "unlock_percent": 0.5,
            },
        )

        rows = parse_allocations(payload)
        by_date = {r.unlock_date: r for r in rows}

        self.assertEqual(
            by_date[date(2023, 8, 1)].unlock_percent_of_allocation,
            Decimal("0.5"),
        )
        self.assertEqual(
            by_date[date(2023, 8, 1)].unlock_tokens,
            Decimal("5323979.5450"),
        )

    def test_allocation_totals_denormalized_onto_every_row(self) -> None:
        """allocation_total_tokens and percent ride on every batch row.

        Makes the headline 'SUI unlocking in next N days' query a trivial
        SUM(unlock_tokens) without joining a separate allocations-master
        table — the duplication cost is trivial (~85 rows total in v1).
        """
        rows = parse_allocations(SAMPLE_NEXT_DATA)
        for r in rows:
            self.assertEqual(r.allocation_total_tokens, Decimal("1064795909"))
            self.assertEqual(
                r.allocation_total_percent_of_supply, Decimal("10.648")
            )

    def test_missing_vesting_info_raises(self) -> None:
        """A payload without props.pageProps.vestingInfo.allocations raises loudly."""
        with self.assertRaisesRegex(ValueError, "vestingInfo.allocations"):
            parse_allocations({"props": {"pageProps": {}}})

    def test_allocations_not_a_list_raises(self) -> None:
        """A scalar allocations field is a CryptoRank shape break."""
        bad = {
            "props": {
                "pageProps": {
                    "vestingInfo": {"allocations": "not-a-list"},
                }
            }
        }
        with self.assertRaisesRegex(ValueError, "is not a list"):
            parse_allocations(bad)

    def test_empty_allocations_returns_empty(self) -> None:
        """No allocations means no rows — not an error."""
        empty = {"props": {"pageProps": {"vestingInfo": {"allocations": []}}}}
        self.assertEqual(parse_allocations(empty), [])


class CollectTests(unittest.TestCase):
    """Collector orchestration paths that are not covered by parser-only tests."""

    def test_zero_parsed_rows_record_partial_and_fail_run(self) -> None:
        """A valid page with no rows should not count as a successful ingest."""
        expected_payload = {"props": {"pageProps": {"vestingInfo": {"allocations": []}}}}

        class FakeResponse:
            text = (
                '<script id="__NEXT_DATA__" type="application/json">'
                '{"props": {"pageProps": {"vestingInfo": {"allocations": []}}}}'
                "</script>"
            )

            def raise_for_status(self) -> None:
                return None

        class FakeHttp:
            def get(self, url: str) -> FakeResponse:
                self.url = url
                return FakeResponse()

        class FakeRun:
            id = 99

            def add_rows(self, n: int) -> None:
                self._added = n

        fake_run = FakeRun()
        with (
            patch("genkei.ingest.sui_unlocks.db.ingest_run") as ingest_run_cm,
            patch("genkei.ingest.sui_unlocks.db.record_partial_endpoints") as partial,
            patch("genkei.ingest.sui_unlocks.db.store_raw_blob") as store_blob,
            patch("genkei.ingest.sui_unlocks.db.connection") as connection_cm,
            patch("genkei.ingest.sui_unlocks.db.bulk_upsert") as bulk_upsert,
        ):
            ingest_run_cm.return_value.__enter__.return_value = fake_run
            ingest_run_cm.return_value.__exit__.return_value = False

            with self.assertRaisesRegex(RuntimeError, "parsed 0 rows"):
                collect(http=FakeHttp())

        store_blob.assert_called_once_with(
            99,
            COLLECT_ENDPOINT_LABEL,
            CRYPTORANK_SUI_VESTING_URL,
            expected_payload,
        )
        partial_args = partial.call_args.args
        self.assertEqual(partial_args[0], 99)
        self.assertEqual(partial_args[1][0]["name"], COLLECT_ENDPOINT_LABEL)
        self.assertEqual(partial_args[1][0]["url"], CRYPTORANK_SUI_VESTING_URL)
        self.assertIn("parsed 0 rows", partial_args[1][0]["error"])
        connection_cm.assert_not_called()
        bulk_upsert.assert_not_called()
        self.assertFalse(hasattr(fake_run, "_added"))


if __name__ == "__main__":
    unittest.main()
