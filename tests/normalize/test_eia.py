"""Unit tests for the EIA Open Data v2 normalizer (B-032).

Pure-function tests for the value parser, per-frequency period parser,
facet matcher, per-series projection, and blob-name mapping.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from genkei.common.watchlist import EiaSeriesEntry
from genkei.normalize.eia import (
    _row_matches_facets,
    _series_blob_name,
    _series_by_blob_name,
    _validate_blob_coverage,
    _validate_source_run_row,
    normalize_series,
    parse_period,
    parse_value,
)

WATCHLIST_YAML = """\
eia:
  - series_id: WTI_SPOT
    name: Cushing OK WTI spot
    route: petroleum/pri/spt
    frequency: D
    facets:
      series: RWTC
  - series_id: CRUDE_INV_EXSPR
    name: Weekly US commercial crude ex-SPR
    route: petroleum/stoc/wstk
    frequency: W
    facets:
      series: WCESTUS1
  - series_id: ELEC_NET_GEN_US
    name: US net electricity generation
    route: electricity/electric-power-operational-data
    frequency: M
    data_field: generation
    facets:
      fueltype: ALL
      location: US
      sectorid: '99'
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
    def test_native_float_passes_through(self) -> None:
        self.assertEqual(parse_value(1.5), 1.5)
        self.assertEqual(parse_value(0), 0.0)
        self.assertEqual(parse_value(-2), -2.0)

    def test_canonical_numeric_string(self) -> None:
        self.assertEqual(parse_value("3.4"), 3.4)
        self.assertEqual(parse_value("-1.2"), -1.2)

    def test_scientific_notation(self) -> None:
        self.assertEqual(parse_value("1.234e6"), 1_234_000.0)

    def test_thousands_separator_stripped(self) -> None:
        self.assertEqual(parse_value("1,000,000"), 1_000_000.0)

    def test_null_sentinel_is_none(self) -> None:
        self.assertIsNone(parse_value("null"))
        self.assertIsNone(parse_value("NULL"))

    def test_other_missing_sentinels(self) -> None:
        self.assertIsNone(parse_value("N/A"))
        self.assertIsNone(parse_value("na"))
        self.assertIsNone(parse_value("-"))

    def test_empty_string_is_none(self) -> None:
        self.assertIsNone(parse_value(""))
        self.assertIsNone(parse_value("   "))

    def test_non_numeric_string_is_none(self) -> None:
        self.assertIsNone(parse_value("pending"))

    def test_none_is_none(self) -> None:
        self.assertIsNone(parse_value(None))

    def test_bool_is_none(self) -> None:
        # Booleans must not coerce to 0/1 — they're never legitimate
        # numeric values in EIA data and silently coercing them would
        # mask upstream contract drift.
        self.assertIsNone(parse_value(True))
        self.assertIsNone(parse_value(False))


# ---------------------------------------------------------------------------
# parse_period
# ---------------------------------------------------------------------------


class ParsePeriodTests(unittest.TestCase):
    def test_daily_period(self) -> None:
        ts = parse_period("2024-06-11", frequency="D")
        self.assertEqual(ts, datetime(2024, 6, 11, tzinfo=timezone.utc))

    def test_weekly_period(self) -> None:
        # EIA weekly periods are dated to a specific reference day
        # (Friday for inventories). We store the as-published date.
        ts = parse_period("2024-06-07", frequency="W")
        self.assertEqual(ts, datetime(2024, 6, 7, tzinfo=timezone.utc))

    def test_monthly_period_to_month_start(self) -> None:
        ts = parse_period("2024-06", frequency="M")
        self.assertEqual(ts, datetime(2024, 6, 1, tzinfo=timezone.utc))

    def test_quarterly_period_to_quarter_start(self) -> None:
        cases = {
            "2024-Q1": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "2024-Q2": datetime(2024, 4, 1, tzinfo=timezone.utc),
            "2024-Q3": datetime(2024, 7, 1, tzinfo=timezone.utc),
            "2024-Q4": datetime(2024, 10, 1, tzinfo=timezone.utc),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_period(raw, frequency="Q"), expected)

    def test_annual_period_to_year_start(self) -> None:
        self.assertEqual(
            parse_period("2024", frequency="A"),
            datetime(2024, 1, 1, tzinfo=timezone.utc),
        )

    def test_empty_string_is_none(self) -> None:
        self.assertIsNone(parse_period("", frequency="D"))

    def test_unparseable_input_is_none(self) -> None:
        self.assertIsNone(parse_period("not-a-date", frequency="D"))
        self.assertIsNone(parse_period("2024-Q9", frequency="Q"))
        self.assertIsNone(parse_period("2024-Q", frequency="Q"))

    def test_existing_datetime_passes_through(self) -> None:
        original = datetime(2024, 1, 2, 3, 4, tzinfo=timezone.utc)
        self.assertEqual(parse_period(original, frequency="D"), original)

    def test_naive_datetime_attaches_utc(self) -> None:
        naive = datetime(2024, 1, 2, 3, 4)
        ts = parse_period(naive, frequency="D")
        self.assertEqual(ts, datetime(2024, 1, 2, 3, 4, tzinfo=timezone.utc))


# ---------------------------------------------------------------------------
# _row_matches_facets
# ---------------------------------------------------------------------------


class RowMatchesFacetsTests(unittest.TestCase):
    def test_empty_facets_matches_any_row(self) -> None:
        self.assertTrue(_row_matches_facets({"period": "2024-06-11"}, {}))

    def test_matches_when_every_facet_value_matches(self) -> None:
        row = {"series": "RWTC", "period": "2024-06-11", "value": 79.95}
        self.assertTrue(_row_matches_facets(row, {"series": "RWTC"}))

    def test_rejects_on_any_facet_mismatch(self) -> None:
        row = {"series": "RBRTE", "period": "2024-06-11"}
        self.assertFalse(_row_matches_facets(row, {"series": "RWTC"}))

    def test_multi_facet_match_requires_every_value(self) -> None:
        row = {"fueltype": "ALL", "location": "US", "sectorid": "99"}
        self.assertTrue(
            _row_matches_facets(
                row, {"fueltype": "ALL", "location": "US", "sectorid": "99"}
            )
        )
        self.assertFalse(
            _row_matches_facets(
                row, {"fueltype": "ALL", "location": "TX", "sectorid": "99"}
            )
        )

    def test_missing_facet_key_rejects(self) -> None:
        row = {"series": "RWTC"}
        self.assertFalse(_row_matches_facets(row, {"location": "US"}))

    def test_numeric_facet_value_is_coerced(self) -> None:
        # EIA returns sectorid as int sometimes, str other times.
        row = {"sectorid": 99}
        self.assertTrue(_row_matches_facets(row, {"sectorid": "99"}))


# ---------------------------------------------------------------------------
# normalize_series
# ---------------------------------------------------------------------------


def _entry(**overrides: Any) -> EiaSeriesEntry:
    base = {
        "series_id": "WTI_SPOT",
        "name": "WTI spot",
        "route": "petroleum/pri/spt",
        "frequency": "D",
        "data_field": "value",
        "date_field": "period",
        "facets": {"series": "RWTC"},
        "units": "USD_PER_BARREL",
        "rationale": None,
    }
    base.update(overrides)
    return EiaSeriesEntry(**base)  # type: ignore[arg-type]


def _payload(rows: list[dict[str, Any]], total: int | None = None) -> dict[str, Any]:
    return {
        "request": {"route": "petroleum/pri/spt"},
        "apiVersion": "2.1.8",
        "response": {
            "total": str(total) if total is not None else str(len(rows)),
            "data": rows,
            "warnings": [],
        },
    }


class NormalizeSeriesTests(unittest.TestCase):
    def test_projects_observations_with_canonical_ts(self) -> None:
        payload = _payload(
            [
                {"period": "2024-06-10", "value": 80.5, "series": "RWTC"},
                {"period": "2024-06-11", "value": 79.9, "series": "RWTC"},
            ]
        )
        series_row, observations = normalize_series(
            payload,
            entry=_entry(),
            source_endpoint="https://api.eia.gov/...",
            ingest_run_id=42,
            fetched_at=datetime(2024, 6, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(series_row["series_id"], "WTI_SPOT")
        self.assertEqual(series_row["ingest_run_id"], 42)
        self.assertEqual(len(observations), 2)
        # Order isn't guaranteed; check by ts.
        by_ts = {obs["ts"]: obs for obs in observations}
        self.assertEqual(
            by_ts[datetime(2024, 6, 10, tzinfo=timezone.utc)]["value"], 80.5
        )
        self.assertEqual(
            by_ts[datetime(2024, 6, 11, tzinfo=timezone.utc)]["value"], 79.9
        )

    def test_filters_rows_by_facets(self) -> None:
        # Mixing RWTC + RBRTE in the same payload — only RWTC should be
        # kept under the watchlist entry's facet.
        payload = _payload(
            [
                {"period": "2024-06-11", "value": 79.9, "series": "RWTC"},
                {"period": "2024-06-11", "value": 82.4, "series": "RBRTE"},
            ]
        )
        _, observations = normalize_series(
            payload,
            entry=_entry(),
            source_endpoint="...",
            ingest_run_id=1,
            fetched_at=datetime.now(timezone.utc),
        )
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["value"], 79.9)

    def test_last_value_per_period_wins(self) -> None:
        # Defensive: if EIA returns two rows for the same period, the
        # last one wins (matches "revise-in-place" semantics).
        payload = _payload(
            [
                {"period": "2024-06-11", "value": 79.0, "series": "RWTC"},
                {"period": "2024-06-11", "value": 79.9, "series": "RWTC"},
            ]
        )
        _, observations = normalize_series(
            payload,
            entry=_entry(),
            source_endpoint="...",
            ingest_run_id=1,
            fetched_at=datetime.now(timezone.utc),
        )
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["value"], 79.9)

    def test_uses_per_series_data_field(self) -> None:
        # Electricity operational data uses `generation`, not `value`.
        payload = _payload(
            [
                {
                    "period": "2024-05",
                    "generation": 367_412.0,
                    "fueltype": "ALL",
                    "location": "US",
                    "sectorid": "99",
                },
            ]
        )
        _, observations = normalize_series(
            payload,
            entry=_entry(
                series_id="ELEC_NET_GEN_US",
                route="electricity/electric-power-operational-data",
                frequency="M",
                data_field="generation",
                facets={"fueltype": "ALL", "location": "US", "sectorid": "99"},
            ),
            source_endpoint="...",
            ingest_run_id=1,
            fetched_at=datetime.now(timezone.utc),
        )
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["value"], 367_412.0)
        self.assertEqual(
            observations[0]["ts"], datetime(2024, 5, 1, tzinfo=timezone.utc)
        )

    def test_no_matching_rows_raises(self) -> None:
        # An empty data array — or a payload where every row fails the
        # facet filter — is a contract-drift signal we want loud.
        payload = _payload(
            [{"period": "2024-06-11", "value": 79.9, "series": "RBRTE"}]
        )
        with self.assertRaisesRegex(ValueError, "matched no rows"):
            normalize_series(
                payload,
                entry=_entry(),
                source_endpoint="...",
                ingest_run_id=1,
                fetched_at=datetime.now(timezone.utc),
            )

    def test_missing_data_field_raises(self) -> None:
        # If EIA renames the data column we want to know immediately.
        payload = _payload([{"period": "2024-06-11", "series": "RWTC"}])
        with self.assertRaisesRegex(ValueError, "missing data field"):
            normalize_series(
                payload,
                entry=_entry(),
                source_endpoint="...",
                ingest_run_id=1,
                fetched_at=datetime.now(timezone.utc),
            )

    def test_invalid_period_for_matching_row_raises(self) -> None:
        payload = _payload([{"period": "not-a-date", "value": 79.9, "series": "RWTC"}])
        with self.assertRaisesRegex(ValueError, "invalid 'period' value 'not-a-date'"):
            normalize_series(
                payload,
                entry=_entry(),
                source_endpoint="...",
                ingest_run_id=1,
                fetched_at=datetime.now(timezone.utc),
            )

    def test_null_value_passes_through_as_none(self) -> None:
        # An explicit null value isn't a contract drift — the row still
        # matched, just no observation available. Persist with value=None.
        payload = _payload(
            [{"period": "2024-06-11", "value": None, "series": "RWTC"}]
        )
        _, observations = normalize_series(
            payload,
            entry=_entry(),
            source_endpoint="...",
            ingest_run_id=1,
            fetched_at=datetime.now(timezone.utc),
        )
        self.assertEqual(len(observations), 1)
        self.assertIsNone(observations[0]["value"])

    def test_payload_must_be_dict(self) -> None:
        with self.assertRaisesRegex(ValueError, "not a JSON object"):
            normalize_series(
                "not a dict",
                entry=_entry(),
                source_endpoint="...",
                ingest_run_id=1,
                fetched_at=datetime.now(timezone.utc),
            )

    def test_missing_response_block_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing a `response` object"):
            normalize_series(
                {"apiVersion": "2.1.8"},
                entry=_entry(),
                source_endpoint="...",
                ingest_run_id=1,
                fetched_at=datetime.now(timezone.utc),
            )

    def test_missing_data_array_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing"):
            normalize_series(
                {"response": {"total": "0"}},
                entry=_entry(),
                source_endpoint="...",
                ingest_run_id=1,
                fetched_at=datetime.now(timezone.utc),
            )


# ---------------------------------------------------------------------------
# blob-name + watchlist mapping
# ---------------------------------------------------------------------------


class SeriesBlobNameTests(unittest.TestCase):
    def test_uppercase_series_id_lowercased(self) -> None:
        self.assertEqual(_series_blob_name("WTI_SPOT"), "eia_wti_spot")

    def test_matches_collector_blob_endpoint(self) -> None:
        # Co-located with the collector's blob_endpoint property — the
        # two must stay in lockstep or normalizer dispatch breaks.
        from genkei.ingest.eia import SeriesTarget

        target = SeriesTarget(
            series_id="HH_SPOT",
            route="natural-gas/pri/fut",
            frequency="D",
            data_field="value",
            facets={"series": "RNGWHHD"},
        )
        self.assertEqual(_series_blob_name(target.series_id), target.blob_endpoint)


class SeriesByBlobNameTests(unittest.TestCase):
    def test_indexes_every_watchlist_entry(self) -> None:
        path = _watchlist_path(self)
        mapping = _series_by_blob_name(path)
        self.assertEqual(
            set(mapping),
            {"eia_wti_spot", "eia_crude_inv_exspr", "eia_elec_net_gen_us"},
        )
        self.assertEqual(mapping["eia_wti_spot"].series_id, "WTI_SPOT")


class ValidateBlobCoverageTests(unittest.TestCase):
    def test_passes_when_every_expected_blob_present(self) -> None:
        # Should not raise.
        _validate_blob_coverage(
            source_run_id=1,
            blobs={
                "eia_wti_spot": ("u", {}, datetime.now(timezone.utc)),
                "eia_brent_spot": ("u", {}, datetime.now(timezone.utc)),
            },
            expected={"eia_wti_spot", "eia_brent_spot"},
        )

    def test_raises_when_blob_missing(self) -> None:
        with self.assertRaises(SystemExit):
            _validate_blob_coverage(
                source_run_id=1,
                blobs={"eia_wti_spot": ("u", {}, datetime.now(timezone.utc))},
                expected={"eia_wti_spot", "eia_brent_spot"},
            )


class ValidateSourceRunRowTests(unittest.TestCase):
    def test_no_row_raises(self) -> None:
        with self.assertRaises(SystemExit):
            _validate_source_run_row(1, None)

    def test_wrong_source_raises(self) -> None:
        row = ("fred", "collect", "success", {})
        with self.assertRaisesRegex(SystemExit, "not an EIA collect run"):
            _validate_source_run_row(1, row)

    def test_wrong_endpoint_raises(self) -> None:
        row = ("eia", "normalize", "success", {})
        with self.assertRaisesRegex(SystemExit, "not an EIA collect run"):
            _validate_source_run_row(1, row)

    def test_non_success_status_raises(self) -> None:
        row = ("eia", "collect", "running", {})
        with self.assertRaisesRegex(SystemExit, "not successful"):
            _validate_source_run_row(1, row)

    def test_partial_endpoints_raises(self) -> None:
        row = (
            "eia",
            "collect",
            "success",
            {"partial_endpoints": [{"name": "eia_wti_spot", "error": "404"}]},
        )
        with self.assertRaisesRegex(SystemExit, "partial endpoint"):
            _validate_source_run_row(1, row)

    def test_success_with_clean_metadata_passes(self) -> None:
        # Should not raise.
        _validate_source_run_row(1, ("eia", "collect", "success", {}))


if __name__ == "__main__":
    unittest.main()
