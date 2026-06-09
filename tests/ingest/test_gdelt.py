"""Unit tests for the GDELT GKG collector (B-033).

Covers the pure-function parser surface (tone / themes / persons /
orgs / locations / published_at), the file-list builders (incremental
window + backfill date range), the watchlist matcher, the lastupdate
parser, and the zip decompression path. Network + DB integration
testing lives in ``test_gdelt_integration.py``.
"""

from __future__ import annotations

import io
import unittest
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from genkei.common.watchlist import (
    CryptoEntry,
    EquityEntry,
    FilerEntry,
    ProtocolEntry,
    Watchlist,
)
from genkei.ingest.gdelt import (
    GDELT_BASE_URL,
    GKG_MIN_COLUMNS,
    MAX_BACKFILL_DAYS,
    MIN_TERM_LENGTH,
    _decompress_csv,
    _fetch_and_parse,
    _MatchTerm,
    _parse_locations,
    _parse_persons_orgs,
    _parse_published_at,
    _parse_themes,
    _parse_tone,
    _raw_blob_endpoint_name,
    build_match_terms,
    collect_backfill,
    file_timestamps_for_date_range,
    file_timestamps_for_window,
    latest_gkg_timestamp,
    match_article,
    parse_csv_rows,
    url_for_timestamp,
)


def _empty_watchlist(**overrides) -> Watchlist:
    defaults = dict(
        crypto=[],
        equities=[],
        macro=[],
        protocols=[],
        filers=[],
    )
    defaults.update(overrides)
    return Watchlist(**defaults)


class ParseToneTests(unittest.TestCase):
    """V1.5 tone field: 7 comma-separated values."""

    def test_canonical_seven_value_row(self) -> None:
        result = _parse_tone("-2.5,3.1,5.6,8.7,12.0,4.2,250")
        self.assertEqual(result.tone, Decimal("-2.5"))
        self.assertEqual(result.positive, Decimal("3.1"))
        self.assertEqual(result.negative, Decimal("5.6"))
        self.assertEqual(result.polarity, Decimal("8.7"))
        self.assertEqual(result.activity_density, Decimal("12.0"))
        self.assertEqual(result.self_density, Decimal("4.2"))
        self.assertEqual(result.word_count, 250)

    def test_empty_string_yields_all_none(self) -> None:
        result = _parse_tone("")
        self.assertIsNone(result.tone)
        self.assertIsNone(result.word_count)

    def test_short_row_yields_all_none(self) -> None:
        # Fewer than 7 values: don't guess — bail.
        result = _parse_tone("-2.5,3.1,5.6")
        self.assertIsNone(result.tone)

    def test_non_numeric_entry_silently_none(self) -> None:
        result = _parse_tone("-2.5,bogus,5.6,8.7,12.0,4.2,250")
        self.assertEqual(result.tone, Decimal("-2.5"))
        self.assertIsNone(result.positive)
        self.assertEqual(result.negative, Decimal("5.6"))

    def test_word_count_coerces_float_to_int(self) -> None:
        result = _parse_tone("0,0,0,0,0,0,250.0")
        self.assertEqual(result.word_count, 250)


class ParseThemesAndPeopleTests(unittest.TestCase):
    """V1Themes / V1Persons / V1Organizations: ;-delimited lists."""

    def test_themes_canonical(self) -> None:
        self.assertEqual(
            _parse_themes("ECON_BITCOIN;CRISIS;EPU_ECONOMY"),
            ["ECON_BITCOIN", "CRISIS", "EPU_ECONOMY"],
        )

    def test_themes_skips_empty_segments(self) -> None:
        # Trailing semicolon is canonical in GDELT output.
        self.assertEqual(_parse_themes("ECON_BITCOIN;CRISIS;"), ["ECON_BITCOIN", "CRISIS"])

    def test_themes_empty_field(self) -> None:
        self.assertEqual(_parse_themes(""), [])

    def test_persons_canonical(self) -> None:
        self.assertEqual(
            _parse_persons_orgs("warren buffett;jamie dimon"),
            ["warren buffett", "jamie dimon"],
        )

    def test_persons_strips_whitespace(self) -> None:
        self.assertEqual(
            _parse_persons_orgs(" elon musk ; satoshi nakamoto "),
            ["elon musk", "satoshi nakamoto"],
        )


class ParseLocationsTests(unittest.TestCase):
    def test_canonical_record(self) -> None:
        raw = "1#United States#US#USAK#34.31#-83.42#1496534"
        result = _parse_locations(raw)
        assert result is not None
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], 1)
        self.assertEqual(result[0]["name"], "United States")
        self.assertEqual(result[0]["country_code"], "US")
        self.assertEqual(result[0]["adm1"], "USAK")
        self.assertAlmostEqual(result[0]["lat"], 34.31)
        self.assertAlmostEqual(result[0]["lon"], -83.42)
        self.assertEqual(result[0]["feature_id"], "1496534")

    def test_multiple_records_separated_by_semicolons(self) -> None:
        raw = (
            "1#United States#US##37.09#-95.71#FID1;"
            "1#United Kingdom#UK##55.37#-3.43#FID2"
        )
        result = _parse_locations(raw)
        assert result is not None
        self.assertEqual(len(result), 2)
        self.assertEqual(result[1]["name"], "United Kingdom")

    def test_malformed_record_silently_skipped(self) -> None:
        # Fewer than 7 hash-fields: drop it.
        raw = "1#United States#US;1#Canada#CA##53.13#-105.5#FID-CA"
        result = _parse_locations(raw)
        assert result is not None
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Canada")

    def test_empty_raw_returns_none(self) -> None:
        self.assertIsNone(_parse_locations(""))

    def test_all_records_malformed_returns_none(self) -> None:
        # Empty result list collapses to None (don't store empty array).
        self.assertIsNone(_parse_locations(";"))

    def test_non_numeric_lat_lon_falls_through_to_none(self) -> None:
        raw = "1#Mars#XX##bogus#xx#FID-MARS"
        result = _parse_locations(raw)
        assert result is not None
        self.assertIsNone(result[0]["lat"])
        self.assertIsNone(result[0]["lon"])

    def test_malformed_lon_preserves_valid_lat(self) -> None:
        raw = "1#Mars#XX##34.31#xx#FID-MARS"
        result = _parse_locations(raw)
        assert result is not None
        self.assertEqual(result[0]["lat"], 34.31)
        self.assertIsNone(result[0]["lon"])


class PublishedAtTests(unittest.TestCase):
    def test_canonical_v2_date(self) -> None:
        dt = _parse_published_at("20260609001500")
        self.assertEqual(dt, datetime(2026, 6, 9, 0, 15, tzinfo=timezone.utc))

    def test_malformed_returns_none(self) -> None:
        self.assertIsNone(_parse_published_at("not-a-date"))

    def test_empty_returns_none(self) -> None:
        self.assertIsNone(_parse_published_at(""))


class BuildMatchTermsTests(unittest.TestCase):
    """Watchlist → list of substring search terms."""

    def test_equity_name_promoted_to_term_labeled_by_ticker(self) -> None:
        wl = _empty_watchlist(
            equities=[
                EquityEntry(
                    symbol="aapl", name="Apple Inc.", cik="0000320193", tier="primary"
                )
            ]
        )
        terms = build_match_terms(wl)
        self.assertEqual(len(terms), 1)
        self.assertEqual(terms[0].term_lower, "apple inc.")
        # Label is upper-cased ticker.
        self.assertEqual(terms[0].label, "AAPL")

    def test_crypto_name_promoted_to_term_labeled_by_symbol(self) -> None:
        wl = _empty_watchlist(
            crypto=[
                CryptoEntry(
                    symbol="btc",
                    name="Bitcoin",
                    coingecko_id="bitcoin",
                    tier="primary",
                )
            ]
        )
        terms = build_match_terms(wl)
        self.assertEqual(terms[0].label, "BTC")

    def test_short_crypto_name_falls_back_to_symbol(self) -> None:
        wl = _empty_watchlist(
            crypto=[
                CryptoEntry(
                    symbol="SUI",
                    name="Sui",
                    coingecko_id="sui",
                    tier="primary",
                )
            ]
        )
        terms = build_match_terms(wl)
        self.assertEqual(terms, [_MatchTerm(term_lower="sui", label="SUI")])

    def test_protocol_label_is_slug(self) -> None:
        wl = _empty_watchlist(
            protocols=[
                ProtocolEntry(
                    slug="aave-v3",
                    name="Aave V3",
                    category="Lending",
                    tier="primary",
                )
            ]
        )
        terms = build_match_terms(wl)
        self.assertEqual(terms[0].label, "aave-v3")

    def test_filer_label_is_cik(self) -> None:
        wl = _empty_watchlist(
            filers=[
                FilerEntry(
                    filer_cik="0001067983",
                    name="Berkshire Hathaway Inc.",
                    tier="primary",
                )
            ]
        )
        terms = build_match_terms(wl)
        self.assertEqual(terms[0].label, "0001067983")

    def test_dedupes_when_same_name_appears_twice(self) -> None:
        # Two equity entries with identical name → one term.
        wl = _empty_watchlist(
            equities=[
                EquityEntry(symbol="GOOG", name="Alphabet Inc.", cik="0001652044", tier="primary"),
                EquityEntry(symbol="GOOGL", name="Alphabet Inc.", cik="0001652044", tier="primary"),
            ]
        )
        terms = build_match_terms(wl)
        self.assertEqual(len(terms), 1)
        # First-write-wins on the label so multi-class listings collapse
        # to one term — the alternative would be storing both labels per
        # hit, which would explode the matched_assets array for no signal.
        self.assertEqual(terms[0].label, "GOOG")

    def test_below_min_term_length_dropped(self) -> None:
        # 3-char name ("Big") is below the 4-char floor.
        self.assertGreaterEqual(MIN_TERM_LENGTH, 4)
        wl = _empty_watchlist(
            equities=[
                EquityEntry(symbol="BIG", name="Big", cik="0000123", tier="primary"),
            ]
        )
        self.assertEqual(build_match_terms(wl), [])


class MatchArticleTests(unittest.TestCase):
    """Substring matcher against themes / persons / orgs / doc URL."""

    def _terms(self) -> list[_MatchTerm]:
        return [
            _MatchTerm(term_lower="apple inc.", label="AAPL"),
            _MatchTerm(term_lower="bitcoin", label="BTC"),
            _MatchTerm(term_lower="berkshire hathaway", label="0001067983"),
        ]

    def test_matches_inside_organizations(self) -> None:
        hits = match_article(
            themes=[],
            persons=[],
            organizations=["Apple Inc."],
            document_identifier="https://example.com/a",
            terms=self._terms(),
        )
        self.assertEqual(hits, ["AAPL"])

    def test_matches_inside_themes_case_insensitively(self) -> None:
        # GDELT themes are upper-case canonical strings.
        hits = match_article(
            themes=["ECON_BITCOIN_INSTITUTIONAL"],
            persons=[],
            organizations=[],
            document_identifier="",
            terms=self._terms(),
        )
        self.assertEqual(hits, ["BTC"])

    def test_matches_inside_document_url(self) -> None:
        hits = match_article(
            themes=[],
            persons=[],
            organizations=[],
            document_identifier="https://blog.example/why-bitcoin-rallied",
            terms=self._terms(),
        )
        self.assertEqual(hits, ["BTC"])

    def test_multiple_matches_returns_sorted_unique(self) -> None:
        hits = match_article(
            themes=["ECON_BITCOIN"],
            persons=[],
            organizations=["Apple Inc.", "Berkshire Hathaway Inc."],
            document_identifier="",
            terms=self._terms(),
        )
        # Sorted ascending so the matched_assets column stays stable
        # across re-upserts of the same row.
        self.assertEqual(hits, ["0001067983", "AAPL", "BTC"])

    def test_no_matches_returns_empty(self) -> None:
        hits = match_article(
            themes=["UNRELATED_TOPIC"],
            persons=["someone else"],
            organizations=["Different Co"],
            document_identifier="https://example.com",
            terms=self._terms(),
        )
        self.assertEqual(hits, [])

    def test_empty_haystack_returns_empty(self) -> None:
        # Nothing to match against → don't match against ourselves.
        hits = match_article(
            themes=[],
            persons=[],
            organizations=[],
            document_identifier="",
            terms=self._terms(),
        )
        self.assertEqual(hits, [])


class FileTimestampsForWindowTests(unittest.TestCase):
    def test_window_rounds_down_and_excludes_start_slot(self) -> None:
        end = datetime(2026, 6, 9, 12, 7, 30, tzinfo=timezone.utc)
        stamps = file_timestamps_for_window(end, hours=1)
        # End rounds down to 12:00; 1h back is 11:00 (exclusive).
        # Expected slots: 11:15, 11:30, 11:45, 12:00 → 4 files.
        self.assertEqual(len(stamps), 4)
        self.assertEqual(stamps[0], datetime(2026, 6, 9, 11, 15, tzinfo=timezone.utc))
        self.assertEqual(stamps[-1], datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc))

    def test_window_24h_yields_96_slots(self) -> None:
        end = datetime(2026, 6, 9, 0, 0, tzinfo=timezone.utc)
        stamps = file_timestamps_for_window(end, hours=24)
        self.assertEqual(len(stamps), 96)

    def test_zero_or_negative_hours_raises(self) -> None:
        end = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            file_timestamps_for_window(end, hours=0)
        with self.assertRaises(ValueError):
            file_timestamps_for_window(end, hours=-1)


class FileTimestampsForDateRangeTests(unittest.TestCase):
    def test_single_day_yields_96_stamps(self) -> None:
        stamps = file_timestamps_for_date_range(
            since=date(2026, 6, 1), until=date(2026, 6, 1)
        )
        self.assertEqual(len(stamps), 96)
        # Whole-day backfills cover the requested UTC day only.
        self.assertEqual(stamps[0], datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(stamps[-1], datetime(2026, 6, 1, 23, 45, tzinfo=timezone.utc))

    def test_multi_day_range_concatenates(self) -> None:
        stamps = file_timestamps_for_date_range(
            since=date(2026, 6, 1), until=date(2026, 6, 3)
        )
        self.assertEqual(len(stamps), 96 * 3)

    def test_inverted_range_raises(self) -> None:
        with self.assertRaises(ValueError):
            file_timestamps_for_date_range(
                since=date(2026, 6, 3), until=date(2026, 6, 1)
            )


class UrlForTimestampTests(unittest.TestCase):
    def test_url_format_matches_gdelt_spec(self) -> None:
        ts = datetime(2026, 6, 9, 0, 15, tzinfo=timezone.utc)
        self.assertEqual(
            url_for_timestamp(ts),
            f"{GDELT_BASE_URL}/20260609001500.gkg.csv.zip",
        )


class LatestGkgTimestampTests(unittest.TestCase):
    """``lastupdate.txt`` parser: pull the GKG-suffixed URL from 3 candidate lines."""

    class _FakeResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

    class _FakeClient:
        def __init__(self, response_text: str) -> None:
            self._response_text = response_text

        def get(self, url: str):  # noqa: ANN001 — fake client
            return LatestGkgTimestampTests._FakeResponse(self._response_text)

    def test_picks_gkg_url_among_three_lines(self) -> None:
        text = (
            "100000\tmd5export\t"
            "https://data.gdeltproject.org/gdeltv2/20260609001500.export.CSV.zip\n"
            "200000\tmd5mentions\t"
            "https://data.gdeltproject.org/gdeltv2/20260609001500.mentions.CSV.zip\n"
            "300000\tmd5gkg\t"
            "https://data.gdeltproject.org/gdeltv2/20260609001500.gkg.csv.zip\n"
        )
        client = self._FakeClient(text)
        ts = latest_gkg_timestamp(client)  # type: ignore[arg-type]
        self.assertEqual(ts, datetime(2026, 6, 9, 0, 15, tzinfo=timezone.utc))

    def test_missing_gkg_line_raises(self) -> None:
        client = self._FakeClient("not-a-gkg-url\n")
        with self.assertRaises(RuntimeError):
            latest_gkg_timestamp(client)  # type: ignore[arg-type]


class DecompressCsvTests(unittest.TestCase):
    def test_canonical_zip_with_one_csv(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("20260609001500.gkg.csv", "col1\tcol2\nval1\tval2\n")
        result = _decompress_csv(buf.getvalue())
        self.assertEqual(result, "col1\tcol2\nval1\tval2\n")

    def test_zip_without_csv_member_raises(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("notes.txt", "not a csv")
        with self.assertRaises(RuntimeError):
            _decompress_csv(buf.getvalue())


class FetchAndParseTests(unittest.TestCase):
    """Raw-blob persistence/replay around one fetched GKG file."""

    class _FakeResponse:
        def __init__(self, content: bytes, status_code: int = 200) -> None:
            self.content = content
            self.status_code = status_code

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

    class _FakeClient:
        def __init__(self, response: FetchAndParseTests._FakeResponse) -> None:
            self.response = response
            self.urls: list[str] = []

        def get(self, url: str):  # noqa: ANN001 — fake client
            self.urls.append(url)
            return self.response

    def _zip(self, text: str) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("20260609000000.gkg.csv", text)
        return buf.getvalue()

    def _matched_csv(self) -> str:
        cells = [
            "20260609000000-0",
            "20260609000000",
            "1",
            "example.com",
            "https://example.com/apple",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "Apple Inc.",
            "",
            "0,0,0,0,0,0,1",
        ]
        return "\t".join(cells) + "\n"

    def test_fetched_csv_lands_in_raw_blobs_before_parse(self) -> None:
        csv_text = self._matched_csv()
        ts = datetime(2026, 6, 9, 0, 0, tzinfo=timezone.utc)
        client = self._FakeClient(self._FakeResponse(self._zip(csv_text)))

        with (
            patch("genkei.ingest.gdelt._cached_raw_blob", return_value=None),
            patch("genkei.ingest.gdelt.db.store_raw_blob") as store,
        ):
            rows = _fetch_and_parse(
                client,
                ts,
                [_MatchTerm(term_lower="apple inc.", label="AAPL")],
                ingest_run_id=42,
            )

        self.assertEqual(len(rows), 1)
        store.assert_called_once_with(
            42,
            _raw_blob_endpoint_name(ts),
            f"{GDELT_BASE_URL}/20260609000000.gkg.csv.zip",
            {"csv": csv_text},
        )

    def test_cached_raw_blob_is_copied_and_reparsed_without_http(self) -> None:
        csv_text = self._matched_csv()
        ts = datetime(2026, 6, 9, 0, 0, tzinfo=timezone.utc)
        fetched_at = datetime(2026, 6, 9, 0, 1, tzinfo=timezone.utc)
        payload = {"csv": csv_text}
        client = self._FakeClient(self._FakeResponse(b"unused"))

        with (
            patch(
                "genkei.ingest.gdelt._cached_raw_blob",
                return_value=(
                    csv_text,
                    "https://cached.example/file.zip",
                    payload,
                    fetched_at,
                ),
            ),
            patch("genkei.ingest.gdelt.db.copy_raw_blob_for_run") as copy_blob,
            patch("genkei.ingest.gdelt.db.store_raw_blob") as store_blob,
        ):
            rows = _fetch_and_parse(
                client,
                ts,
                [_MatchTerm(term_lower="apple inc.", label="AAPL")],
                ingest_run_id=43,
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(client.urls, [])
        copy_blob.assert_called_once_with(
            43,
            _raw_blob_endpoint_name(ts),
            "https://cached.example/file.zip",
            payload,
            fetched_at,
        )
        store_blob.assert_not_called()


class ParseCsvRowsTests(unittest.TestCase):
    """End-to-end parser slice: synthetic CSV → matched _ParsedRow objects."""

    def _terms(self) -> list[_MatchTerm]:
        return [_MatchTerm(term_lower="apple inc.", label="AAPL")]

    def _canonical_row_pads(self, *cells: str) -> str:
        """Pad a row out to GKG_MIN_COLUMNS columns so the parser accepts it."""
        cells_list = list(cells)
        while len(cells_list) < GKG_MIN_COLUMNS:
            cells_list.append("")
        return "\t".join(cells_list) + "\n"

    def test_matched_row_yields_parsed_row(self) -> None:
        # GKGRECORDID, DATE, sourceCollectionId, sourceCommonName, docId,
        # 2 spacers (V1Counts, V2.1Counts), V1Themes, spacer (V2Themes),
        # V1Locations, spacer (V2Locations), V1Persons, spacer, V1Orgs,
        # spacer, Tone.
        csv_text = self._canonical_row_pads(
            "20260609001500-0",  # 0  gkg_record_id
            "20260609001500",    # 1  date
            "1",                  # 2  source collection
            "example.com",       # 3  source common name
            "https://example.com/article",  # 4  document identifier
            "",                   # 5
            "",                   # 6
            "ECON_STOCKMARKET",  # 7  V1 themes
            "",                   # 8
            "1#United States#US##37.0#-95.7#FID1",  # 9  V1 locations
            "",                   # 10
            "tim cook",          # 11 V1 persons
            "",                   # 12
            "Apple Inc.",        # 13 V1 orgs — triggers match
            "",                   # 14
            "-2.0,3.1,5.6,8.7,12.0,4.2,250",  # 15 tone
        )
        rows = list(parse_csv_rows(csv_text, self._terms()))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.gkg_record_id, "20260609001500-0")
        self.assertEqual(
            row.published_at, datetime(2026, 6, 9, 0, 15, tzinfo=timezone.utc)
        )
        self.assertEqual(row.matched_assets, ["AAPL"])
        self.assertEqual(row.organizations, ["Apple Inc."])
        self.assertEqual(row.themes, ["ECON_STOCKMARKET"])
        self.assertEqual(row.tone.tone, Decimal("-2.0"))
        self.assertEqual(row.tone.word_count, 250)
        assert row.locations is not None
        self.assertEqual(row.locations[0]["country_code"], "US")

    def test_unmatched_row_dropped(self) -> None:
        # No watchlist match → not yielded.
        csv_text = self._canonical_row_pads(
            "20260609001500-1",
            "20260609001500",
            "1",
            "example.com",
            "https://example.com/other",
            "",
            "",
            "UNRELATED",
            "",
            "",
            "",
            "",
            "",
            "Different Co",
            "",
            "0,0,0,0,0,0,100",
        )
        rows = list(parse_csv_rows(csv_text, self._terms()))
        self.assertEqual(rows, [])

    def test_missing_date_drops_row(self) -> None:
        csv_text = self._canonical_row_pads(
            "20260609001500-2",
            "",  # missing date
            "1",
            "example.com",
            "https://example.com/no-date",
            "", "", "", "", "", "", "", "", "Apple Inc.", "", "0,0,0,0,0,0,1",
        )
        rows = list(parse_csv_rows(csv_text, self._terms()))
        self.assertEqual(rows, [])

    def test_short_row_skipped_cleanly(self) -> None:
        # Fewer than GKG_MIN_COLUMNS columns: drop silently.
        csv_text = "20260609001500-3\t20260609001500\t1\texample.com\n"
        rows = list(parse_csv_rows(csv_text, self._terms()))
        self.assertEqual(rows, [])


class RetentionConstantTests(unittest.TestCase):
    """Pin the retention floor so future changes are explicit."""

    def test_max_backfill_days_matches_migration(self) -> None:
        # The migration encodes a 365-day retention policy. If the
        # collector caps backfill at a different number, retention will
        # silently prune historical rows the user thought they'd ingested.
        self.assertEqual(MAX_BACKFILL_DAYS, 365)


class CollectBackfillValidationTests(unittest.TestCase):
    def test_future_until_raises_before_fetching(self) -> None:
        tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
        with self.assertRaisesRegex(ValueError, "cannot be in the future"):
            collect_backfill(since=tomorrow, until=tomorrow)


if __name__ == "__main__":
    unittest.main()
