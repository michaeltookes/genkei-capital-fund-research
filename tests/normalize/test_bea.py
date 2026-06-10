"""Unit tests for the BEA NIPA normalizer (B-029).

Pure-function tests for the TimePeriod parser, missing-value sentinel
handling, NoteRef splitting, and the per-table parse that drops rows
outside the watchlist filter set.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from genkei.normalize.bea import (
    _parse_note_refs,
    normalize_table,
    parse_bea_value,
    parse_time_period,
)


class ParseBeaValueTests(unittest.TestCase):
    def test_canonical_numeric_string(self) -> None:
        self.assertEqual(parse_bea_value("3.4"), 3.4)
        self.assertEqual(parse_bea_value("0"), 0.0)
        self.assertEqual(parse_bea_value("-1.2"), -1.2)

    def test_thousands_separator_stripped(self) -> None:
        # BEA's nominal-dollar series come with comma-formatted strings:
        # "23,128.3" → 23128.3.
        self.assertEqual(parse_bea_value("23,128.3"), 23128.3)
        self.assertEqual(parse_bea_value("1,000,000"), 1_000_000.0)

    def test_ellipsis_means_missing(self) -> None:
        # BEA's withheld-value sentinel.
        self.assertIsNone(parse_bea_value("..."))
        self.assertIsNone(parse_bea_value("."))

    def test_empty_string_is_none(self) -> None:
        self.assertIsNone(parse_bea_value(""))
        self.assertIsNone(parse_bea_value("   "))

    def test_non_numeric_string_is_none(self) -> None:
        # Defensive — don't crash on garbage data, just drop the value.
        self.assertIsNone(parse_bea_value("N/A"))
        self.assertIsNone(parse_bea_value("pending"))

    def test_native_numeric_passthrough(self) -> None:
        self.assertEqual(parse_bea_value(3.4), 3.4)
        self.assertEqual(parse_bea_value(7), 7.0)

    def test_none_passthrough(self) -> None:
        self.assertIsNone(parse_bea_value(None))


class ParseTimePeriodTests(unittest.TestCase):
    def _utc(self, year: int, month: int, day: int) -> datetime:
        return datetime(year, month, day, tzinfo=timezone.utc)

    def test_quarterly_q1(self) -> None:
        result = parse_time_period("2024Q1")
        self.assertIsNotNone(result)
        ts, freq = result
        self.assertEqual(ts, self._utc(2024, 1, 1))
        self.assertEqual(freq, "Q")

    def test_quarterly_q2_q3_q4(self) -> None:
        # Q2 starts April, Q3 starts July, Q4 starts October.
        cases = {
            "2024Q2": self._utc(2024, 4, 1),
            "2024Q3": self._utc(2024, 7, 1),
            "2024Q4": self._utc(2024, 10, 1),
        }
        for raw, expected in cases.items():
            result = parse_time_period(raw)
            self.assertIsNotNone(result)
            ts, _ = result
            self.assertEqual(ts, expected)

    def test_annual(self) -> None:
        result = parse_time_period("2024")
        self.assertIsNotNone(result)
        ts, freq = result
        self.assertEqual(ts, self._utc(2024, 1, 1))
        self.assertEqual(freq, "A")

    def test_monthly(self) -> None:
        result = parse_time_period("2024M03")
        self.assertIsNotNone(result)
        ts, freq = result
        self.assertEqual(ts, self._utc(2024, 3, 1))
        self.assertEqual(freq, "M")

    def test_quarter_5_invalid(self) -> None:
        self.assertIsNone(parse_time_period("2024Q5"))

    def test_month_13_invalid(self) -> None:
        self.assertIsNone(parse_time_period("2024M13"))

    def test_malformed_year_only(self) -> None:
        # 5-digit year is invalid (BEA never publishes those).
        self.assertIsNone(parse_time_period("12345"))
        # 3-digit year too.
        self.assertIsNone(parse_time_period("999"))

    def test_garbage_returns_none(self) -> None:
        self.assertIsNone(parse_time_period(""))
        self.assertIsNone(parse_time_period(None))
        self.assertIsNone(parse_time_period("nope"))
        self.assertIsNone(parse_time_period(2024))  # type: ignore[arg-type]


class ParseNoteRefsTests(unittest.TestCase):
    def test_empty_returns_empty_list(self) -> None:
        self.assertEqual(_parse_note_refs(""), [])
        self.assertEqual(_parse_note_refs(None), [])

    def test_canonical_comma_split(self) -> None:
        # BEA NoteRef is comma-separated note IDs that reference the
        # Notes array elsewhere in the payload.
        self.assertEqual(
            _parse_note_refs("T10101.1,T10101.2"),
            ["T10101.1", "T10101.2"],
        )

    def test_whitespace_padding_stripped(self) -> None:
        self.assertEqual(
            _parse_note_refs(" A , B , C "), ["A", "B", "C"]
        )


class NormalizeTableTests(unittest.TestCase):
    """Synthetic BEA payload → (series_rows, observation_rows)."""

    def _payload(self, data_rows: list[dict]) -> dict:
        return {
            "BEAAPI": {
                "Request": {},
                "Results": {
                    "Statistic": "Table 1.1.1",
                    "UTCProductionTime": "2024-04-25T08:30:00",
                    "Data": data_rows,
                },
            }
        }

    def _call(
        self,
        payload: dict,
        *,
        table_id: str = "T10101",
        frequency: str = "Q",
        watched_lines: set[int] | None = None,
    ) -> tuple[list[dict], list[dict]]:
        return normalize_table(
            payload,
            table_id=table_id,
            frequency=frequency,
            watched_lines=watched_lines if watched_lines is not None else {1},
            source_endpoint="https://apps.bea.gov/api/data/?UserID=***",
            ingest_run_id=42,
            fetched_at=datetime(2024, 4, 25, 12, tzinfo=timezone.utc),
        )

    def test_canonical_row_lands_series_and_observation(self) -> None:
        payload = self._payload(
            [
                {
                    "TableName": "T10101",
                    "SeriesCode": "DGDSRL",
                    "LineNumber": "1",
                    "LineDescription": "Gross domestic product",
                    "TimePeriod": "2024Q1",
                    "METRIC_NAME": "Percent change at annual rate",
                    "CL_UNIT": "Percent",
                    "UNIT_MULT": "0",
                    "DataValue": "3.4",
                    "NoteRef": "T10101.1",
                }
            ]
        )
        series, observations = self._call(payload)
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0]["series_id"], "T10101:1:Q")
        self.assertEqual(series[0]["table_id"], "T10101")
        self.assertEqual(series[0]["line_number"], 1)
        self.assertEqual(series[0]["line_description"], "Gross domestic product")
        self.assertEqual(series[0]["frequency"], "Q")
        self.assertEqual(series[0]["note_refs"], ["T10101.1"])

        self.assertEqual(len(observations), 1)
        obs = observations[0]
        self.assertEqual(obs["series_id"], "T10101:1:Q")
        self.assertEqual(obs["ts"], datetime(2024, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(obs["value"], 3.4)
        self.assertEqual(obs["ingest_run_id"], 42)

    def test_same_line_different_frequency_uses_distinct_series_id(self) -> None:
        quarterly_series, quarterly_observations = self._call(
            self._payload(
                [
                    {
                        "LineNumber": "1",
                        "TimePeriod": "2024Q1",
                        "DataValue": "3.4",
                    }
                ]
            ),
            table_id="T10101",
            frequency="Q",
            watched_lines={1},
        )
        annual_series, annual_observations = self._call(
            self._payload(
                [
                    {
                        "LineNumber": "1",
                        "TimePeriod": "2024",
                        "DataValue": "2.9",
                    }
                ]
            ),
            table_id="T10101",
            frequency="A",
            watched_lines={1},
        )

        self.assertEqual(quarterly_series[0]["series_id"], "T10101:1:Q")
        self.assertEqual(quarterly_observations[0]["series_id"], "T10101:1:Q")
        self.assertEqual(annual_series[0]["series_id"], "T10101:1:A")
        self.assertEqual(annual_observations[0]["series_id"], "T10101:1:A")

    def test_line_outside_watchlist_dropped(self) -> None:
        # T10101 has 50+ lines; the watchlist filter must drop them.
        payload = self._payload(
            [
                {
                    "LineNumber": "1",
                    "TimePeriod": "2024Q1",
                    "DataValue": "3.4",
                },
                {
                    "LineNumber": "2",  # not watched
                    "TimePeriod": "2024Q1",
                    "DataValue": "1.2",
                },
                {
                    "LineNumber": "37",  # not watched
                    "TimePeriod": "2024Q1",
                    "DataValue": "0.5",
                },
            ]
        )
        series, observations = self._call(payload, watched_lines={1})
        self.assertEqual(len(series), 1)
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["value"], 3.4)

    def test_missing_watched_line_raises(self) -> None:
        payload = self._payload(
            [
                {
                    "LineNumber": "2",
                    "TimePeriod": "2024Q1",
                    "DataValue": "1.2",
                },
                {
                    "LineNumber": "37",
                    "TimePeriod": "2024Q1",
                    "DataValue": "0.5",
                },
            ]
        )

        with self.assertRaisesRegex(
            ValueError,
            r"BEA response for T10101 Q missing watched line\(s\): 1",
        ):
            self._call(payload, watched_lines={1})

    def test_missing_value_lands_null(self) -> None:
        # Withheld values come through as None in the value column —
        # the row still exists so consumers know BEA *had* a row.
        payload = self._payload(
            [
                {
                    "LineNumber": "1",
                    "TimePeriod": "2024Q1",
                    "DataValue": "...",
                }
            ]
        )
        _, observations = self._call(payload)
        self.assertEqual(len(observations), 1)
        self.assertIsNone(observations[0]["value"])

    def test_dedup_within_blob_last_wins(self) -> None:
        # Defensive — if BEA accidentally repeats a row, keep last.
        payload = self._payload(
            [
                {
                    "LineNumber": "1",
                    "TimePeriod": "2024Q1",
                    "DataValue": "3.4",
                },
                {
                    "LineNumber": "1",
                    "TimePeriod": "2024Q1",
                    "DataValue": "3.5",  # revised in same blob
                },
            ]
        )
        _, observations = self._call(payload)
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["value"], 3.5)

    def test_frequency_mismatch_drops_row(self) -> None:
        # If we asked BEA for 'Q' and a row parses as 'A', it'd violate
        # the PK invariant — drop with a warning, then fail because the
        # configured watched line produced no usable row.
        payload = self._payload(
            [
                {
                    "LineNumber": "1",
                    "TimePeriod": "2024",  # annual
                    "DataValue": "3.4",
                }
            ]
        )
        with self.assertRaisesRegex(
            ValueError,
            r"BEA response for T10101 Q missing watched line\(s\): 1",
        ):
            self._call(payload, frequency="Q")

    def test_unparseable_watched_line_period_raises(self) -> None:
        payload = self._payload(
            [
                {
                    "LineNumber": "1",
                    "TimePeriod": "FY2024",
                    "DataValue": "3.4",
                }
            ]
        )

        with self.assertRaisesRegex(
            ValueError,
            r"BEA response for T10101 Q missing watched line\(s\): 1",
        ):
            self._call(payload, frequency="Q")

    def test_malformed_payload_returns_empty(self) -> None:
        # Defensive — error envelopes should return (), not crash.
        self.assertEqual(self._call({}), ([], []))
        self.assertEqual(self._call({"BEAAPI": {}}), ([], []))
        self.assertEqual(
            self._call({"BEAAPI": {"Results": {}}}), ([], [])
        )

    def test_thousands_separated_value_parses(self) -> None:
        # GDP nominal series come with commas.
        payload = self._payload(
            [
                {
                    "LineNumber": "1",
                    "TimePeriod": "2024Q1",
                    "DataValue": "23,128.3",
                }
            ]
        )
        _, observations = self._call(payload)
        self.assertEqual(observations[0]["value"], 23128.3)

    def test_annual_payload_parses(self) -> None:
        payload = self._payload(
            [
                {
                    "LineNumber": "5",
                    "TimePeriod": "2024",
                    "DataValue": "65000",
                }
            ]
        )
        _series, observations = self._call(
            payload,
            table_id="T70100",
            frequency="A",
            watched_lines={5},
        )
        self.assertEqual(observations[0]["frequency"], "A")
        self.assertEqual(observations[0]["ts"], datetime(2024, 1, 1, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
