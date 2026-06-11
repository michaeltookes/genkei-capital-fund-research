"""Unit tests for the Treasury Fiscal Data normalizer (B-030).

Pure-function tests for the date parser, missing-value sentinel
handling, row_filter matching, and the per-endpoint parse that
projects each watched series out of the shared endpoint payload.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from genkei.common.watchlist import TreasurySeriesEntry, load_watchlist
from genkei.normalize.treasury import (
    _endpoint_to_blob_name,
    _series_by_endpoint,
    _validate_blob_coverage,
    _validate_source_run_row,
    normalize_endpoint,
    parse_record_date,
    parse_value,
    row_matches_filter,
)

WATCHLIST_YAML = """\
treasury:
  - series_id: TOTAL_PUBLIC_DEBT
    name: Total Public Debt Outstanding
    endpoint: /v2/accounting/od/debt_to_penny
    value_field: tot_pub_debt_out_amt
    frequency: D
    units: USD
  - series_id: DEBT_HELD_PUBLIC
    name: Debt Held by the Public
    endpoint: /v2/accounting/od/debt_to_penny
    value_field: debt_held_public_amt
    frequency: D
    units: USD
  - series_id: TGA_CLOSING_BAL
    name: TGA closing balance
    endpoint: /v1/accounting/dts/operating_cash_balance
    value_field: close_today_bal
    frequency: D
    row_filter:
      account_type: Treasury General Account (TGA) Closing Balance
"""


def _watchlist_path(case: unittest.TestCase) -> Path:
    ctx = TemporaryDirectory()
    case.addCleanup(ctx.cleanup)
    tmp = Path(ctx.name)
    path = tmp / "watchlists.yml"
    path.write_text(WATCHLIST_YAML, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# parse_value
# ---------------------------------------------------------------------------


class ParseValueTests(unittest.TestCase):
    def test_canonical_numeric_string(self) -> None:
        self.assertEqual(parse_value("3.4"), 3.4)
        self.assertEqual(parse_value("0"), 0.0)
        self.assertEqual(parse_value("-1.2"), -1.2)

    def test_thousands_separator_stripped(self) -> None:
        # Treasury publishes nominal-dollar series as strings:
        # "36,176,659,847,936.05" → 3.617...e13
        self.assertAlmostEqual(
            parse_value("36,176,659,847,936.05"),
            36_176_659_847_936.05,
        )
        self.assertEqual(parse_value("1,000,000"), 1_000_000.0)

    def test_null_sentinel_is_none(self) -> None:
        # Treasury occasionally returns the literal string "null" for
        # withheld values; the field comparison is case-insensitive.
        self.assertIsNone(parse_value("null"))
        self.assertIsNone(parse_value("NULL"))
        self.assertIsNone(parse_value("Null"))

    def test_other_missing_sentinels(self) -> None:
        self.assertIsNone(parse_value("N/A"))
        self.assertIsNone(parse_value("n/a"))
        self.assertIsNone(parse_value("-"))

    def test_empty_string_is_none(self) -> None:
        self.assertIsNone(parse_value(""))
        self.assertIsNone(parse_value("   "))

    def test_non_numeric_string_is_none(self) -> None:
        # Don't crash on garbage; drop the value silently.
        self.assertIsNone(parse_value("pending"))
        self.assertIsNone(parse_value("error"))

    def test_native_numeric_passthrough(self) -> None:
        self.assertEqual(parse_value(3.4), 3.4)
        self.assertEqual(parse_value(7), 7.0)

    def test_none_passthrough(self) -> None:
        self.assertIsNone(parse_value(None))

    def test_bool_is_none(self) -> None:
        # Defensive — Python's int(True) == 1 but a boolean in a
        # Treasury value field signals a contract change worth dropping.
        self.assertIsNone(parse_value(True))
        self.assertIsNone(parse_value(False))


# ---------------------------------------------------------------------------
# parse_record_date
# ---------------------------------------------------------------------------


class ParseRecordDateTests(unittest.TestCase):
    def test_iso_date_string(self) -> None:
        result = parse_record_date("2024-03-15")
        self.assertEqual(
            result, datetime(2024, 3, 15, tzinfo=timezone.utc)
        )

    def test_year_boundary(self) -> None:
        result = parse_record_date("1993-04-01")
        self.assertEqual(
            result, datetime(1993, 4, 1, tzinfo=timezone.utc)
        )

    def test_native_date_object(self) -> None:
        result = parse_record_date(date(2024, 6, 11))
        self.assertEqual(
            result, datetime(2024, 6, 11, tzinfo=timezone.utc)
        )

    def test_naive_datetime_gets_utc_zone(self) -> None:
        result = parse_record_date(datetime(2024, 6, 11, 12, 30))
        self.assertEqual(
            result, datetime(2024, 6, 11, 12, 30, tzinfo=timezone.utc)
        )

    def test_tz_aware_datetime_converted_to_utc(self) -> None:
        from datetime import timedelta

        eastern = timezone(timedelta(hours=-5))
        result = parse_record_date(datetime(2024, 6, 11, 8, 0, tzinfo=eastern))
        self.assertEqual(
            result, datetime(2024, 6, 11, 13, 0, tzinfo=timezone.utc)
        )

    def test_invalid_string_is_none(self) -> None:
        self.assertIsNone(parse_record_date("2024-13-40"))
        self.assertIsNone(parse_record_date("garbage"))
        self.assertIsNone(parse_record_date(""))
        self.assertIsNone(parse_record_date("   "))

    def test_non_string_non_date_is_none(self) -> None:
        self.assertIsNone(parse_record_date(None))
        self.assertIsNone(parse_record_date(12345))
        self.assertIsNone(parse_record_date([2024, 1, 1]))


# ---------------------------------------------------------------------------
# row_matches_filter
# ---------------------------------------------------------------------------


class RowMatchesFilterTests(unittest.TestCase):
    def test_empty_filter_matches_anything(self) -> None:
        self.assertTrue(row_matches_filter({"account_type": "TGA"}, {}))
        self.assertTrue(row_matches_filter({}, {}))

    def test_single_field_match(self) -> None:
        row = {"account_type": "Federal Reserve Account"}
        self.assertTrue(
            row_matches_filter(
                row, {"account_type": "Federal Reserve Account"}
            )
        )

    def test_single_field_mismatch(self) -> None:
        row = {"account_type": "Foo"}
        self.assertFalse(
            row_matches_filter(
                row, {"account_type": "Federal Reserve Account"}
            )
        )

    def test_multi_field_all_must_match(self) -> None:
        row = {"security_type_desc": "Marketable", "security_desc": "Treasury Bills"}
        self.assertTrue(
            row_matches_filter(
                row,
                {
                    "security_type_desc": "Marketable",
                    "security_desc": "Treasury Bills",
                },
            )
        )
        self.assertFalse(
            row_matches_filter(
                row,
                {
                    "security_type_desc": "Marketable",
                    "security_desc": "Treasury Bonds",
                },
            )
        )

    def test_missing_field_is_mismatch(self) -> None:
        row = {"account_type": "Foo"}
        self.assertFalse(
            row_matches_filter(row, {"missing_field": "anything"})
        )

    def test_null_field_in_row_is_mismatch(self) -> None:
        row = {"account_type": None}
        self.assertFalse(
            row_matches_filter(row, {"account_type": "Federal Reserve Account"})
        )

    def test_numeric_field_string_coerced(self) -> None:
        # Treasury occasionally types numeric strings as ints; we
        # compare as strings so the watchlist YAML doesn't have to
        # care.
        row = {"src_line_nbr": 4}
        self.assertTrue(row_matches_filter(row, {"src_line_nbr": "4"}))


# ---------------------------------------------------------------------------
# normalize_endpoint
# ---------------------------------------------------------------------------


def _series_entries() -> list[TreasurySeriesEntry]:
    return [
        TreasurySeriesEntry(
            series_id="TOTAL_PUBLIC_DEBT",
            name="Total Public Debt Outstanding",
            endpoint="/v2/accounting/od/debt_to_penny",
            value_field="tot_pub_debt_out_amt",
            frequency="D",
            units="USD",
        ),
        TreasurySeriesEntry(
            series_id="DEBT_HELD_PUBLIC",
            name="Debt Held by the Public",
            endpoint="/v2/accounting/od/debt_to_penny",
            value_field="debt_held_public_amt",
            frequency="D",
            units="USD",
        ),
    ]


def _tga_entry() -> TreasurySeriesEntry:
    return TreasurySeriesEntry(
        series_id="TGA_CLOSING_BAL",
        name="TGA closing balance",
        endpoint="/v1/accounting/dts/operating_cash_balance",
        value_field="close_today_bal",
        frequency="D",
        row_filter={
            "account_type": "Treasury General Account (TGA) Closing Balance"
        },
    )


class NormalizeEndpointTests(unittest.TestCase):
    def _call(
        self,
        payload: Any,
        *,
        series: list[TreasurySeriesEntry] | None = None,
        source_endpoint: str = "https://example/test",
        ingest_run_id: int = 42,
        fetched_at: datetime | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return normalize_endpoint(
            payload,
            series=series if series is not None else _series_entries(),
            source_endpoint=source_endpoint,
            ingest_run_id=ingest_run_id,
            fetched_at=fetched_at
            or datetime(2026, 6, 11, tzinfo=timezone.utc),
        )

    @staticmethod
    def _payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {"data": rows, "meta": {"count": len(rows)}}

    def test_single_row_emits_two_series_two_observations(self) -> None:
        payload = self._payload(
            [
                {
                    "record_date": "2024-06-10",
                    "tot_pub_debt_out_amt": "34,000,000,000,000",
                    "debt_held_public_amt": "27,000,000,000,000",
                    "intragov_hold_amt": "7,000,000,000,000",
                }
            ]
        )
        series_rows, observations = self._call(payload)
        self.assertEqual(len(series_rows), 2)
        self.assertEqual(len(observations), 2)
        by_id = {row["series_id"]: row for row in observations}
        self.assertAlmostEqual(
            by_id["TOTAL_PUBLIC_DEBT"]["value"], 34_000_000_000_000.0
        )
        self.assertAlmostEqual(
            by_id["DEBT_HELD_PUBLIC"]["value"], 27_000_000_000_000.0
        )
        # ts is the start-of-day UTC datetime.
        self.assertEqual(
            by_id["TOTAL_PUBLIC_DEBT"]["ts"],
            datetime(2024, 6, 10, tzinfo=timezone.utc),
        )

    def test_series_metadata_carries_endpoint_and_value_field(self) -> None:
        payload = self._payload(
            [
                {
                    "record_date": "2024-06-10",
                    "tot_pub_debt_out_amt": "1",
                    "debt_held_public_amt": "1",
                }
            ]
        )
        series_rows, _ = self._call(payload)
        by_id = {row["series_id"]: row for row in series_rows}
        self.assertEqual(
            by_id["TOTAL_PUBLIC_DEBT"]["endpoint"],
            "/v2/accounting/od/debt_to_penny",
        )
        self.assertEqual(
            by_id["TOTAL_PUBLIC_DEBT"]["value_field"], "tot_pub_debt_out_amt"
        )
        self.assertEqual(by_id["TOTAL_PUBLIC_DEBT"]["units"], "USD")
        self.assertEqual(by_id["TOTAL_PUBLIC_DEBT"]["frequency"], "D")
        self.assertEqual(by_id["TOTAL_PUBLIC_DEBT"]["ingest_run_id"], 42)
        # No row_filter on debt_to_penny → JSONB column should land None.
        self.assertIsNone(by_id["TOTAL_PUBLIC_DEBT"]["row_filter"])

    def test_row_filter_dropping_unmatched_rows(self) -> None:
        # operating_cash_balance returns ~25 rows per record_date;
        # only the TGA row should produce an observation.
        payload = self._payload(
            [
                {
                    "record_date": "2024-06-10",
                    "account_type": "Federal Reserve Account",
                    "close_today_bal": "999",
                },
                {
                    "record_date": "2024-06-10",
                    "account_type": "Treasury General Account (TGA) Closing Balance",
                    "close_today_bal": "5,000",
                },
                {
                    "record_date": "2024-06-10",
                    "account_type": "Some other TGA descriptor",
                    "close_today_bal": "12345",
                },
            ]
        )
        series_rows, observations = self._call(
            payload, series=[_tga_entry()]
        )
        self.assertEqual(len(series_rows), 1)
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["series_id"], "TGA_CLOSING_BAL")
        self.assertEqual(observations[0]["value"], 5000.0)
        # JSONB row_filter persisted on series row.
        self.assertEqual(
            series_rows[0]["row_filter"],
            {
                "account_type": "Treasury General Account (TGA) Closing Balance"
            },
        )

    def test_missing_value_lands_null(self) -> None:
        payload = self._payload(
            [
                {
                    "record_date": "2024-06-10",
                    "tot_pub_debt_out_amt": "null",
                    "debt_held_public_amt": "null",
                }
            ]
        )
        _, observations = self._call(payload)
        self.assertEqual(len(observations), 2)
        for obs in observations:
            self.assertIsNone(obs["value"])

    def test_unparseable_date_drops_row(self) -> None:
        payload = self._payload(
            [
                {
                    "record_date": "garbage",
                    "tot_pub_debt_out_amt": "1",
                    "debt_held_public_amt": "1",
                }
            ]
        )
        # No valid row → both watched series missing → raise.
        with self.assertRaisesRegex(
            ValueError, r"matched no rows for series"
        ):
            self._call(payload)

    def test_missing_series_raises(self) -> None:
        # A real Treasury contract change (field renamed, all rows
        # filtered out) must surface loudly.
        payload = self._payload(
            [
                {
                    "record_date": "2024-06-10",
                    "account_type": "Wrong descriptor",
                    "close_today_bal": "1",
                }
            ]
        )
        with self.assertRaisesRegex(
            ValueError, r"matched no rows for series.*TGA_CLOSING_BAL"
        ):
            self._call(payload, series=[_tga_entry()])

    def test_dedup_within_blob_last_wins(self) -> None:
        # Defensive — duplicate (series_id, ts) keeps the last value.
        payload = self._payload(
            [
                {
                    "record_date": "2024-06-10",
                    "tot_pub_debt_out_amt": "1",
                    "debt_held_public_amt": "1",
                },
                {
                    "record_date": "2024-06-10",
                    "tot_pub_debt_out_amt": "2",
                    "debt_held_public_amt": "2",
                },
            ]
        )
        _, observations = self._call(payload)
        self.assertEqual(len(observations), 2)
        for obs in observations:
            self.assertEqual(obs["value"], 2.0)

    def test_malformed_payload_returns_empty(self) -> None:
        # Defensive — non-dict payloads / missing data block don't
        # crash; the empty result triggers the missing-series raise
        # via the orchestrator. Calling normalize_endpoint directly,
        # without series, the empty branch returns ([], []).
        self.assertEqual(normalize_endpoint("not a dict", series=[], source_endpoint="x", ingest_run_id=1, fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc)), ([], []))
        self.assertEqual(normalize_endpoint({}, series=[], source_endpoint="x", ingest_run_id=1, fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc)), ([], []))
        self.assertEqual(normalize_endpoint({"data": "not a list"}, series=[], source_endpoint="x", ingest_run_id=1, fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc)), ([], []))


# ---------------------------------------------------------------------------
# Orchestration helpers
# ---------------------------------------------------------------------------


class EndpointToBlobNameTests(unittest.TestCase):
    def test_round_trips_with_collector_slug(self) -> None:
        # The normalizer slug must match the collector's blob_endpoint
        # so the dispatch loop finds the blob it expects.
        self.assertEqual(
            _endpoint_to_blob_name("/v2/accounting/od/debt_to_penny"),
            "treasury_v2_accounting_od_debt_to_penny",
        )
        self.assertEqual(
            _endpoint_to_blob_name("/v1/accounting/dts/operating_cash_balance"),
            "treasury_v1_accounting_dts_operating_cash_balance",
        )


class SeriesByEndpointTests(unittest.TestCase):
    def test_groups_shared_endpoint(self) -> None:
        path = _watchlist_path(self)
        grouped = _series_by_endpoint(path)
        self.assertEqual(set(grouped.keys()), {
            "/v2/accounting/od/debt_to_penny",
            "/v1/accounting/dts/operating_cash_balance",
        })
        self.assertEqual(
            sorted(e.series_id for e in grouped["/v2/accounting/od/debt_to_penny"]),
            ["DEBT_HELD_PUBLIC", "TOTAL_PUBLIC_DEBT"],
        )


class ValidateBlobCoverageTests(unittest.TestCase):
    def test_missing_blob_raises_with_endpoint_name(self) -> None:
        with self.assertRaisesRegex(
            SystemExit, r"missing raw blob endpoint\(s\): treasury_foo"
        ):
            _validate_blob_coverage(
                source_run_id=123,
                blobs={},
                expected_endpoints={"treasury_foo"},
            )

    def test_all_present_does_not_raise(self) -> None:
        _validate_blob_coverage(
            source_run_id=123,
            blobs={"treasury_foo": ("u", {}, datetime(2026, 1, 1, tzinfo=timezone.utc))},
            expected_endpoints={"treasury_foo"},
        )


class ValidateSourceRunRowTests(unittest.TestCase):
    def test_missing_row_raises(self) -> None:
        with self.assertRaisesRegex(SystemExit, r"No Treasury collector run"):
            _validate_source_run_row(7, None)

    def test_wrong_source_raises(self) -> None:
        with self.assertRaisesRegex(SystemExit, r"not a Treasury collect run"):
            _validate_source_run_row(7, ("bea", "collect", "success", None))

    def test_wrong_endpoint_raises(self) -> None:
        with self.assertRaisesRegex(SystemExit, r"not a Treasury collect run"):
            _validate_source_run_row(
                7, ("treasury", "normalize", "success", None)
            )

    def test_failed_status_raises(self) -> None:
        with self.assertRaisesRegex(SystemExit, r"is not successful"):
            _validate_source_run_row(
                7, ("treasury", "collect", "failed", None)
            )

    def test_partial_endpoint_failures_raise(self) -> None:
        with self.assertRaisesRegex(
            SystemExit, r"partial endpoint failure\(s\): treasury_foo"
        ):
            _validate_source_run_row(
                7,
                (
                    "treasury",
                    "collect",
                    "success",
                    {"partial_endpoints": [{"name": "treasury_foo"}]},
                ),
            )

    def test_clean_success_passes(self) -> None:
        _validate_source_run_row(
            7, ("treasury", "collect", "success", {"watchlist_path": "x"})
        )


if __name__ == "__main__":
    unittest.main()
