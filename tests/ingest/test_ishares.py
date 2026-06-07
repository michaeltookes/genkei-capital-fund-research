"""Unit tests for the iShares spot crypto ETF collector (B-107)."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from genkei.common.watchlist import EtfTickerEntry
from genkei.ingest.ishares import (
    ISSUER_FILTER,
    PRODUCT_SCREENER_URL,
    SOURCE_NAME,
    _coerce_decimal,
    _coerce_string,
    _parse_as_of_date,
    parse_snapshots,
)

# A minimal-but-realistic fragment of the iShares product-screener response.
# Shape matches what the live feed returns (verified 2026-06-07 against the
# real endpoint). Includes one BTC ETF, two ETH ETFs, and one non-crypto
# equity ETF to confirm the watchlist filter rejects unrelated funds.
LIVE_FRAGMENT_PAYLOAD = {
    "333011": {
        "localExchangeTicker": "IBIT",
        "cusip": "46438F101",
        "isin": "US46438F1012",
        "fundName": "iShares Bitcoin Trust ETF",
        "aladdinAssetClass": "Digital Assets",
        "navAmount": {"d": "33.81", "r": 33.805916},
        "navAmountAsOf": {"d": "Jun 05, 2026", "r": 20260605},
        "totalNetAssets": {"d": "46,211,335,562", "r": 46211335562.0},
        "totalNetAssetsFundAsOf": {"d": "Jun 05, 2026", "r": 20260605},
    },
    "337614": {
        "localExchangeTicker": "ETHA",
        "cusip": "46438R105",
        "isin": "US46438R1059",
        "fundName": "iShares Ethereum Trust ETF",
        "aladdinAssetClass": "Digital Assets",
        "navAmount": {"d": "11.75", "r": 11.7452},
        "navAmountAsOf": {"d": "Jun 05, 2026", "r": 20260605},
        "totalNetAssets": {"d": "4,450,501,503", "r": 4450501503.21},
        "totalNetAssetsFundAsOf": {"d": "Jun 05, 2026", "r": 20260605},
    },
    "348532": {
        "localExchangeTicker": "ETHB",
        "cusip": "46438M106",
        "isin": "US46438M1062",
        "fundName": "iShares Staked Ethereum Trust ETF",
        "aladdinAssetClass": "Digital Assets",
        "navAmount": {"d": "20.03", "r": 20.0312},
        "navAmountAsOf": {"d": "Jun 05, 2026", "r": 20260605},
        "totalNetAssets": {"d": "458,312,812", "r": 458312811.66},
        "totalNetAssetsFundAsOf": {"d": "Jun 05, 2026", "r": 20260605},
    },
    # Non-crypto fund — must be filtered out by the watchlist join.
    "239726": {
        "localExchangeTicker": "IVV",
        "cusip": "464287200",
        "isin": "US4642872000",
        "fundName": "iShares Core S&P 500 ETF",
        "aladdinAssetClass": "Equity",
        "navAmount": {"d": "548.12", "r": 548.121234},
        "navAmountAsOf": {"d": "Jun 05, 2026", "r": 20260605},
        "totalNetAssets": {"d": "500,000,000,000", "r": 500000000000.0},
        "totalNetAssetsFundAsOf": {"d": "Jun 05, 2026", "r": 20260605},
    },
}

# Watchlist entries used to filter the feed. Tests pass these directly to
# parse_snapshots without going through load_watchlist.
IBIT_WATCHLIST = EtfTickerEntry(
    ticker="IBIT",
    name="iShares Bitcoin Trust ETF",
    asset="BTC",
    issuer="BlackRock",
    launch_date="2024-01-11",
)
ETHA_WATCHLIST = EtfTickerEntry(
    ticker="ETHA",
    name="iShares Ethereum Trust ETF",
    asset="ETH",
    issuer="BlackRock",
    launch_date="2024-07-23",
)
ETHB_WATCHLIST = EtfTickerEntry(
    ticker="ETHB",
    name="iShares Staked Ethereum Trust ETF",
    asset="ETH",
    issuer="BlackRock",
    launch_date="2026-03-12",
)
BLACKROCK_WATCHLIST_V1: list[EtfTickerEntry] = [
    IBIT_WATCHLIST,
    ETHA_WATCHLIST,
    ETHB_WATCHLIST,
]


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class ModuleConstantsTests(unittest.TestCase):
    """Pin the module-level constants the workflow + health check depend on."""

    def test_source_name(self) -> None:
        """Stable source name keyed in RECURRING_ENDPOINTS + PRIMARY_TABLES."""
        self.assertEqual(SOURCE_NAME, "ishares")

    def test_issuer_filter_is_blackrock(self) -> None:
        """v1 scope is BlackRock-issued ETFs only."""
        self.assertEqual(ISSUER_FILTER, "BlackRock")

    def test_product_screener_url_is_https_ishares(self) -> None:
        """URL must be the public iShares product-screener endpoint."""
        self.assertTrue(PRODUCT_SCREENER_URL.startswith("https://www.ishares.com/"))
        self.assertIn("product-screener-v3.1.jsn", PRODUCT_SCREENER_URL)


# ---------------------------------------------------------------------------
# Value coercion + date parsing
# ---------------------------------------------------------------------------


class CoerceDecimalTests(unittest.TestCase):
    """Exercise iShares numeric-field coercion edge cases."""

    def test_dict_prefers_r_over_d(self) -> None:
        """``r`` (unrounded) wins over ``d`` (display string) when both exist."""
        # If the feed disagrees between r and d (rare), trust the unrounded one.
        v = _coerce_decimal({"d": "33.81", "r": 33.805916})
        self.assertEqual(v, Decimal("33.805916"))

    def test_dict_falls_back_to_d_when_r_missing(self) -> None:
        """Use display ``d`` when ``r`` isn't present."""
        v = _coerce_decimal({"d": "46,211,335,562"})
        self.assertEqual(v, Decimal("46211335562"))

    def test_dict_d_with_commas_stripped(self) -> None:
        """Strip thousands separators from display strings."""
        v = _coerce_decimal({"d": "1,234.56", "r": None})
        self.assertEqual(v, Decimal("1234.56"))

    def test_dash_returns_none(self) -> None:
        """``"-"`` is iShares' sentinel for missing — return None."""
        self.assertIsNone(_coerce_decimal({"d": "-", "r": None}))
        self.assertIsNone(_coerce_decimal("-"))

    def test_none_and_empty(self) -> None:
        """Missing or blank values become None."""
        self.assertIsNone(_coerce_decimal(None))
        self.assertIsNone(_coerce_decimal({"d": "", "r": None}))
        self.assertIsNone(_coerce_decimal(""))

    def test_native_numerics(self) -> None:
        """Bare ints / floats are accepted (defensive — feed shouldn't return them)."""
        self.assertEqual(_coerce_decimal(33), Decimal("33"))
        self.assertEqual(_coerce_decimal(33.81), Decimal("33.81"))


class CoerceStringTests(unittest.TestCase):
    """Exercise iShares string-field coercion edge cases."""

    def test_plain_string(self) -> None:
        """Plain strings pass through with whitespace stripped."""
        self.assertEqual(_coerce_string("IBIT"), "IBIT")
        self.assertEqual(_coerce_string("  IBIT  "), "IBIT")

    def test_dict_d_field(self) -> None:
        """Dict-wrapped strings use the ``d`` field."""
        self.assertEqual(_coerce_string({"d": "46438F101"}), "46438F101")

    def test_dash_treated_as_missing(self) -> None:
        """iShares uses ``"-"`` as a missing-value sentinel."""
        self.assertIsNone(_coerce_string("-"))
        self.assertIsNone(_coerce_string({"d": "-"}))

    def test_empty_and_none(self) -> None:
        """Empty strings and None both yield None."""
        self.assertIsNone(_coerce_string(""))
        self.assertIsNone(_coerce_string("  "))
        self.assertIsNone(_coerce_string(None))


class ParseAsOfDateTests(unittest.TestCase):
    """Pin the as-of-date parser for navAmountAsOf / totalNetAssetsFundAsOf."""

    def test_r_field_yyyymmdd_int(self) -> None:
        """Prefer the integer ``r`` field — locale-free YYYYMMDD."""
        d = _parse_as_of_date({"d": "Jun 05, 2026", "r": 20260605})
        self.assertEqual(d, date(2026, 6, 5))

    def test_d_field_human_string_fallback(self) -> None:
        """Fall back to parsing the human-readable display string when r is missing."""
        d = _parse_as_of_date({"d": "Jun 05, 2026", "r": None})
        self.assertEqual(d, date(2026, 6, 5))

    def test_invalid_r_falls_through_to_d(self) -> None:
        """Out-of-range ``r`` is rejected; parser falls through to ``d``."""
        d = _parse_as_of_date({"d": "Jan 15, 2025", "r": 12345678})
        self.assertEqual(d, date(2025, 1, 15))

    def test_garbage_returns_none(self) -> None:
        """Completely unparseable input yields None."""
        self.assertIsNone(_parse_as_of_date({"d": "tomorrow", "r": None}))
        self.assertIsNone(_parse_as_of_date(None))
        self.assertIsNone(_parse_as_of_date("Jun 05, 2026"))  # bare string, not dict


# ---------------------------------------------------------------------------
# parse_snapshots — full extractor
# ---------------------------------------------------------------------------


class ParseSnapshotsTests(unittest.TestCase):
    """End-to-end extractor: filters by watchlist + derives shares-outstanding."""

    def test_extracts_three_crypto_etfs_from_live_fragment(self) -> None:
        """All three BlackRock crypto ETFs land as snapshots."""
        snaps = parse_snapshots(
            LIVE_FRAGMENT_PAYLOAD,
            watchlist_etfs=BLACKROCK_WATCHLIST_V1,
        )
        tickers = sorted(s.ticker for s in snaps)
        self.assertEqual(tickers, ["ETHA", "ETHB", "IBIT"])

    def test_filters_non_watchlist_etfs(self) -> None:
        """Non-crypto iShares ETFs (e.g. IVV) must NOT be extracted."""
        snaps = parse_snapshots(
            LIVE_FRAGMENT_PAYLOAD,
            watchlist_etfs=BLACKROCK_WATCHLIST_V1,
        )
        tickers = {s.ticker for s in snaps}
        # IVV is in the payload but not in the watchlist — must be skipped.
        self.assertNotIn("IVV", tickers)

    def test_shares_outstanding_derivation(self) -> None:
        """shares_outstanding = total_net_assets / nav, quantized to 4 decimals."""
        snaps = parse_snapshots(
            LIVE_FRAGMENT_PAYLOAD,
            watchlist_etfs=BLACKROCK_WATCHLIST_V1,
        )
        by_ticker = {s.ticker: s for s in snaps}

        ibit = by_ticker["IBIT"]
        # 46_211_335_562 / 33.805916 = 1366960018.5364... — verified against
        # the live homelab DB row written by the collector on 2026-06-07.
        self.assertEqual(ibit.shares_outstanding, Decimal("1366960018.5364"))

        ethb = by_ticker["ETHB"]
        # 458_312_811.66 / 20.0312 = 22879947.8643...
        # (live feed publishes more NAV precision than 4 decimals so the
        # live DB shows a slightly different shares figure; this fixture
        # uses the display-precision value so the math is reproducible.)
        self.assertEqual(ethb.shares_outstanding, Decimal("22879947.8643"))

    def test_carries_watchlist_metadata(self) -> None:
        """issuer + asset come from the watchlist entry, not the feed."""
        snaps = parse_snapshots(
            LIVE_FRAGMENT_PAYLOAD,
            watchlist_etfs=BLACKROCK_WATCHLIST_V1,
        )
        by_ticker = {s.ticker: s for s in snaps}
        # Asset routing is a watchlist concept, not an iShares feed concept.
        # (The feed's aladdinAssetClass would let us derive it but the
        # watchlist is the authoritative source for our asset taxonomy.)
        self.assertEqual(by_ticker["IBIT"].asset, "BTC")
        self.assertEqual(by_ticker["ETHA"].asset, "ETH")
        self.assertEqual(by_ticker["ETHB"].asset, "ETH")
        self.assertEqual(by_ticker["IBIT"].issuer, "BlackRock")

    def test_skips_mismatched_nav_and_tna_as_of_dates(self) -> None:
        """NAV and TNA must be stamped with the same as-of date."""
        payload = {
            "333011": {
                "localExchangeTicker": "IBIT",
                "cusip": "46438F101",
                "isin": "US46438F1012",
                "fundName": "iShares Bitcoin Trust ETF",
                "navAmount": {"d": "33.81", "r": 33.805916},
                "navAmountAsOf": {"d": "Jun 04, 2026", "r": 20260604},
                "totalNetAssets": {"d": "46,211,335,562", "r": 46211335562.0},
                "totalNetAssetsFundAsOf": {"d": "Jun 05, 2026", "r": 20260605},
            },
        }
        snaps = parse_snapshots(payload, watchlist_etfs=[IBIT_WATCHLIST])
        self.assertEqual(snaps, [])

    def test_skips_entries_with_missing_required_fields(self) -> None:
        """An entry missing NAV / TNA / either as-of date is dropped silently."""
        payload = {
            "333011": {
                "localExchangeTicker": "IBIT",
                "navAmount": None,  # missing — drop
                "navAmountAsOf": {"d": "Jun 05, 2026", "r": 20260605},
                "totalNetAssets": {"d": "46,211,335,562", "r": 46211335562.0},
                "totalNetAssetsFundAsOf": {"d": "Jun 05, 2026", "r": 20260605},
            },
            "337614": {
                "localExchangeTicker": "ETHA",
                "navAmount": {"d": "11.75", "r": 11.7452},
                "navAmountAsOf": {"d": "Jun 05, 2026", "r": 20260605},
                "totalNetAssets": {"d": "4,450,501,503", "r": 4450501503.21},
                "totalNetAssetsFundAsOf": {"d": "Jun 05, 2026", "r": 20260605},
            },
        }
        snaps = parse_snapshots(
            payload,
            watchlist_etfs=[IBIT_WATCHLIST, ETHA_WATCHLIST],
        )
        # IBIT is dropped (missing nav); ETHA survives.
        self.assertEqual([s.ticker for s in snaps], ["ETHA"])

    def test_skips_zero_or_negative_nav(self) -> None:
        """NAV <= 0 is a data-quality failure — skip rather than divide-by-zero."""
        payload = {
            "333011": {
                "localExchangeTicker": "IBIT",
                "navAmount": {"d": "0.00", "r": 0.0},
                "navAmountAsOf": {"d": "Jun 05, 2026", "r": 20260605},
                "totalNetAssets": {"d": "46,211,335,562", "r": 46211335562.0},
                "totalNetAssetsFundAsOf": {"d": "Jun 05, 2026", "r": 20260605},
            },
        }
        snaps = parse_snapshots(payload, watchlist_etfs=[IBIT_WATCHLIST])
        self.assertEqual(snaps, [])

    def test_skips_zero_or_negative_tna(self) -> None:
        """TNA <= 0 is skipped before deriving shares-outstanding."""
        for tna_value in (0.0, -1.0):
            with self.subTest(tna_value=tna_value):
                payload = {
                    "333011": {
                        "localExchangeTicker": "IBIT",
                        "navAmount": {"d": "33.81", "r": 33.805916},
                        "navAmountAsOf": {"d": "Jun 05, 2026", "r": 20260605},
                        "totalNetAssets": {"d": str(tna_value), "r": tna_value},
                        "totalNetAssetsFundAsOf": {
                            "d": "Jun 05, 2026",
                            "r": 20260605,
                        },
                    },
                }
                snaps = parse_snapshots(payload, watchlist_etfs=[IBIT_WATCHLIST])
                self.assertEqual(snaps, [])

    def test_empty_payload_returns_empty(self) -> None:
        """An empty product-screener payload yields no snapshots."""
        self.assertEqual(parse_snapshots({}, watchlist_etfs=BLACKROCK_WATCHLIST_V1), [])

    def test_non_dict_payload_raises(self) -> None:
        """A list payload (defensive) raises ValueError — the feed must be a JSON object."""
        with self.assertRaises(ValueError):
            parse_snapshots([], watchlist_etfs=BLACKROCK_WATCHLIST_V1)  # type: ignore[arg-type]

    def test_ticker_case_insensitive_match(self) -> None:
        """A lowercased ticker in the feed still matches its watchlist entry."""
        payload = {
            "333011": {
                "localExchangeTicker": "ibit",  # lowercase
                "navAmount": {"d": "33.81", "r": 33.805916},
                "navAmountAsOf": {"d": "Jun 05, 2026", "r": 20260605},
                "totalNetAssets": {"d": "46,211,335,562", "r": 46211335562.0},
                "totalNetAssetsFundAsOf": {"d": "Jun 05, 2026", "r": 20260605},
            },
        }
        snaps = parse_snapshots(payload, watchlist_etfs=[IBIT_WATCHLIST])
        self.assertEqual(len(snaps), 1)
        self.assertEqual(snaps[0].ticker, "IBIT")


if __name__ == "__main__":
    unittest.main()
