"""Unit tests for the CFTC Commitments of Traders collector (B-031)."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlparse

from genkei.common.watchlist import CotMarketEntry
from genkei.ingest.cftc import (
    DISAGGREGATED_DATASET_ID,
    SOCRATA_LIMIT,
    TFF_DATASET_ID,
    _categories_for,
    _coerce_int,
    _parse_report_date,
    build_market_url,
    parse_market_rows,
)


BTC_MARKET = CotMarketEntry(
    code="133741",
    symbol="BTC",
    name="BITCOIN - CHICAGO MERCANTILE EXCHANGE",
    report_type="tff",
    sleeve="crypto:core",
    rationale="institutional positioning",
)

WTI_MARKET = CotMarketEntry(
    code="067411",
    symbol="CL",
    name="WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE",
    report_type="disaggregated",
    sleeve="macro",
    rationale="energy / inflation context",
)

NOW = datetime(2026, 6, 2, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# URL construction
# ---------------------------------------------------------------------------


class BuildMarketUrlTests(unittest.TestCase):
    def test_tff_dataset_chosen_for_financial_market(self) -> None:
        url = build_market_url(BTC_MARKET, since=None)
        parsed = urlparse(url)
        self.assertIn(TFF_DATASET_ID, parsed.path)
        params = parse_qs(parsed.query)
        # No date filter: only the market-code filter
        self.assertIn("cftc_contract_market_code='133741'", params["$where"][0])
        self.assertNotIn("report_date_as_yyyy_mm_dd", params["$where"][0])
        # Bounded $limit to avoid Socrata's 1000-row default
        self.assertEqual(params["$limit"], [str(SOCRATA_LIMIT)])
        # Ascending order so upserts process oldest first
        self.assertIn("ASC", params["$order"][0])

    def test_disaggregated_dataset_chosen_for_commodity_market(self) -> None:
        url = build_market_url(WTI_MARKET, since=None)
        parsed = urlparse(url)
        self.assertIn(DISAGGREGATED_DATASET_ID, parsed.path)

    def test_since_clause_appended_when_provided(self) -> None:
        url = build_market_url(BTC_MARKET, since=date(2024, 1, 1))
        params = parse_qs(urlparse(url).query)
        where = params["$where"][0]
        self.assertIn("cftc_contract_market_code='133741'", where)
        self.assertIn("report_date_as_yyyy_mm_dd >= '2024-01-01T00:00:00'", where)


# ---------------------------------------------------------------------------
# Value coercion + date parsing
# ---------------------------------------------------------------------------


class CoerceIntTests(unittest.TestCase):
    def test_string_digits_parse(self) -> None:
        self.assertEqual(_coerce_int("12345"), 12345)
        self.assertEqual(_coerce_int("  100  "), 100)

    def test_float_strings_truncate(self) -> None:
        # Socrata occasionally returns "0.0" for a zero-position cell
        self.assertEqual(_coerce_int("0.0"), 0)
        self.assertEqual(_coerce_int("12.7"), 12)

    def test_none_and_blank_pass_through(self) -> None:
        self.assertIsNone(_coerce_int(None))
        self.assertIsNone(_coerce_int(""))
        self.assertIsNone(_coerce_int("  "))

    def test_garbage_returns_none(self) -> None:
        self.assertIsNone(_coerce_int("n/a"))
        self.assertIsNone(_coerce_int("not-a-number"))

    def test_native_int_passes_through(self) -> None:
        self.assertEqual(_coerce_int(42), 42)

    def test_bool_rejected(self) -> None:
        # True/False would silently coerce to 1/0 via int(); reject so a
        # truthy-but-non-numeric cell doesn't land as a 1 in the table.
        self.assertIsNone(_coerce_int(True))
        self.assertIsNone(_coerce_int(False))


class ParseReportDateTests(unittest.TestCase):
    def test_socrata_floating_timestamp(self) -> None:
        self.assertEqual(
            _parse_report_date("2024-01-09T00:00:00.000"),
            date(2024, 1, 9),
        )

    def test_bare_date_falls_back(self) -> None:
        self.assertEqual(_parse_report_date("2024-01-09"), date(2024, 1, 9))

    def test_garbage_returns_none(self) -> None:
        self.assertIsNone(_parse_report_date(""))
        self.assertIsNone(_parse_report_date("not-a-date"))
        self.assertIsNone(_parse_report_date(None))


# ---------------------------------------------------------------------------
# Per-report-type category routing
# ---------------------------------------------------------------------------


class CategoryRoutingTests(unittest.TestCase):
    def test_tff_includes_asset_manager_and_leveraged_funds(self) -> None:
        cats = {c.trader_category for c in _categories_for("tff")}
        self.assertIn("asset_manager", cats)
        self.assertIn("leveraged_funds", cats)
        self.assertIn("dealer_intermediary", cats)
        self.assertIn("non_reportable", cats)

    def test_disaggregated_includes_managed_money_and_producer_merchant(self) -> None:
        cats = {c.trader_category for c in _categories_for("disaggregated")}
        self.assertIn("managed_money", cats)
        self.assertIn("producer_merchant", cats)
        self.assertIn("swap_dealer", cats)

    def test_unsupported_report_type_raises(self) -> None:
        with self.assertRaises(ValueError):
            _categories_for("legacy")


# ---------------------------------------------------------------------------
# Row parsing (the core canary)
# ---------------------------------------------------------------------------


TFF_FIXTURE_ROW = {
    "report_date_as_yyyy_mm_dd": "2024-01-09T00:00:00.000",
    "cftc_contract_market_code": "133741",
    "market_and_exchange_names": "BITCOIN - CHICAGO MERCANTILE EXCHANGE",
    "dealer_positions_long_all": "100",
    "dealer_positions_short_all": "200",
    "dealer_positions_spread": "50",
    "asset_mgr_positions_long": "1500",
    "asset_mgr_positions_short": "100",
    "asset_mgr_positions_spread": "200",
    "lev_money_positions_long": "5000",
    "lev_money_positions_short": "8000",
    "lev_money_positions_spread": "1000",
    "other_rept_positions_long": "300",
    "other_rept_positions_short": "400",
    "other_rept_positions_spread": "50",
    "nonrept_positions_long_all": "600",
    "nonrept_positions_short_all": "500",
}

DISAGG_FIXTURE_ROW = {
    "report_date_as_yyyy_mm_dd": "2024-01-09T00:00:00.000",
    "cftc_contract_market_code": "067411",
    "market_and_exchange_names": "WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE",
    "prod_merc_positions_long": "100000",
    "prod_merc_positions_short": "150000",
    "swap_positions_long_all": "200000",
    "swap_positions_short_all": "180000",
    "swap__positions_spread_all": "30000",  # double underscore intentional
    "m_money_positions_long_all": "300000",
    "m_money_positions_short_all": "250000",
    "m_money_positions_spread": "40000",
    "other_rept_positions_long": "50000",
    "other_rept_positions_short": "60000",
    "other_rept_positions_spread": "10000",
    "nonrept_positions_long_all": "20000",
    "nonrept_positions_short_all": "30000",
}


class ParseMarketRowsTests(unittest.TestCase):
    def _parse(self, payload: list[dict], market: CotMarketEntry) -> list[dict]:
        return parse_market_rows(
            payload,
            market=market,
            ingest_run_id=42,
            source_endpoint="https://test.example/cftc",
            fetched_at=NOW,
        )

    def test_tff_row_fans_out_to_five_categories(self) -> None:
        rows = self._parse([TFF_FIXTURE_ROW], BTC_MARKET)
        self.assertEqual(len(rows), 5)
        categories = {r["trader_category"] for r in rows}
        self.assertEqual(
            categories,
            {
                "dealer_intermediary",
                "asset_manager",
                "leveraged_funds",
                "other_reportables",
                "non_reportable",
            },
        )

    def test_leveraged_funds_net_position_math(self) -> None:
        """The canary test the spec calls out — verify long/short
        decoding so downstream queries computing (long - short) get the
        right net position for the leveraged-funds row."""
        rows = self._parse([TFF_FIXTURE_ROW], BTC_MARKET)
        lev = next(r for r in rows if r["trader_category"] == "leveraged_funds")
        self.assertEqual(lev["long_positions"], 5000)
        self.assertEqual(lev["short_positions"], 8000)
        self.assertEqual(lev["spreading_positions"], 1000)
        # The CLI computes net live, but verify the source values support it.
        self.assertEqual(lev["long_positions"] - lev["short_positions"], -3000)

    def test_asset_manager_decoded_from_correct_columns(self) -> None:
        rows = self._parse([TFF_FIXTURE_ROW], BTC_MARKET)
        am = next(r for r in rows if r["trader_category"] == "asset_manager")
        self.assertEqual(am["long_positions"], 1500)
        self.assertEqual(am["short_positions"], 100)
        self.assertEqual(am["spreading_positions"], 200)
        self.assertEqual(am["long_positions"] - am["short_positions"], 1400)

    def test_non_reportable_has_null_spread(self) -> None:
        # CFTC convention: non-reportable traders by definition don't spread.
        rows = self._parse([TFF_FIXTURE_ROW], BTC_MARKET)
        nr = next(r for r in rows if r["trader_category"] == "non_reportable")
        self.assertEqual(nr["long_positions"], 600)
        self.assertEqual(nr["short_positions"], 500)
        self.assertIsNone(nr["spreading_positions"])

    def test_report_date_decoded(self) -> None:
        rows = self._parse([TFF_FIXTURE_ROW], BTC_MARKET)
        for r in rows:
            self.assertEqual(r["report_date"], date(2024, 1, 9))

    def test_stamps_market_metadata_on_every_row(self) -> None:
        rows = self._parse([TFF_FIXTURE_ROW], BTC_MARKET)
        for r in rows:
            self.assertEqual(r["market_code"], "133741")
            self.assertEqual(r["market_name"], "BITCOIN - CHICAGO MERCANTILE EXCHANGE")
            self.assertEqual(r["report_type"], "tff")
            self.assertEqual(r["ingest_run_id"], 42)
            self.assertEqual(r["fetched_at"], NOW)
            self.assertEqual(r["source_endpoint"], "https://test.example/cftc")

    def test_disaggregated_fans_out_with_managed_money(self) -> None:
        rows = self._parse([DISAGG_FIXTURE_ROW], WTI_MARKET)
        # Disaggregated also fans out to 5 categories
        self.assertEqual(len(rows), 5)
        mm = next(r for r in rows if r["trader_category"] == "managed_money")
        self.assertEqual(mm["long_positions"], 300000)
        self.assertEqual(mm["short_positions"], 250000)
        self.assertEqual(mm["spreading_positions"], 40000)

    def test_disaggregated_swap_dealer_uses_double_underscore_column(self) -> None:
        # The upstream CFTC schema actually publishes
        # "swap__positions_spread_all" (double underscore). If a future
        # CFTC rename collapses to a single underscore, this test will
        # surface the drift loudly.
        rows = self._parse([DISAGG_FIXTURE_ROW], WTI_MARKET)
        sd = next(r for r in rows if r["trader_category"] == "swap_dealer")
        self.assertEqual(sd["long_positions"], 200000)
        self.assertEqual(sd["short_positions"], 180000)
        self.assertEqual(sd["spreading_positions"], 30000)

    def test_skips_rows_missing_long_and_short(self) -> None:
        # A row where both long and short are missing for a given category
        # would land as net=NULL — meaningless. The parser should drop
        # those rather than write zero-valued junk.
        partial = dict(TFF_FIXTURE_ROW)
        partial["lev_money_positions_long"] = ""
        partial["lev_money_positions_short"] = ""
        rows = self._parse([partial], BTC_MARKET)
        cats = {r["trader_category"] for r in rows}
        # 4 categories instead of 5 — leveraged_funds dropped.
        self.assertEqual(len(rows), 4)
        self.assertNotIn("leveraged_funds", cats)

    def test_skips_rows_with_missing_report_date(self) -> None:
        bad = dict(TFF_FIXTURE_ROW)
        bad["report_date_as_yyyy_mm_dd"] = ""
        rows = self._parse([bad], BTC_MARKET)
        self.assertEqual(rows, [])

    def test_multiple_weeks_each_fan_out(self) -> None:
        week1 = dict(TFF_FIXTURE_ROW)
        week2 = dict(TFF_FIXTURE_ROW)
        week2["report_date_as_yyyy_mm_dd"] = "2024-01-16T00:00:00.000"
        rows = self._parse([week1, week2], BTC_MARKET)
        # 2 weeks × 5 categories = 10 rows
        self.assertEqual(len(rows), 10)
        dates = {r["report_date"] for r in rows}
        self.assertEqual(dates, {date(2024, 1, 9), date(2024, 1, 16)})

    def test_non_array_payload_raises(self) -> None:
        # Socrata always returns an array; an object means the upstream
        # contract changed. Raise loudly rather than silently writing 0 rows.
        with self.assertRaises(ValueError):
            parse_market_rows(
                {"error": "bad"},
                market=BTC_MARKET,
                ingest_run_id=1,
                source_endpoint="test",
                fetched_at=NOW,
            )

    def test_falls_back_to_watchlist_name_when_upstream_missing(self) -> None:
        without_name = dict(TFF_FIXTURE_ROW)
        without_name.pop("market_and_exchange_names")
        rows = self._parse([without_name], BTC_MARKET)
        self.assertEqual(rows[0]["market_name"], BTC_MARKET.name)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
