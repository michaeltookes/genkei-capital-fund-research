"""Unit tests for the Bitwise spot crypto ETF collector (B-113)."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from genkei.common.watchlist import EtfTickerEntry
from genkei.ingest.bitwise import (
    ISSUER_FILTER,
    PRODUCT_URLS,
    RECONCILE_TOLERANCE,
    SOURCE_NAME,
    _find_labeled_value,
    _find_nav,
    _find_nav_as_of,
    _parse_money,
    parse_snapshot,
)

# A trimmed-but-realistic fragment of the live bitbetf.com HTML (verified
# 2026-06-30). Deliberately keeps the build-generated ``class="c-..."``
# attributes and the Next.js ``<!-- -->`` comment nodes so the tests prove
# the label-anchored parser survives both (the c-* classes churn on every
# site rebuild; the comments sit between "NAV:" and its value span).
LIVE_FRAGMENT_HTML = """
<div class="c-bszhDB"><h4 class="c-AfGhm">Ticker</h4>
  <p class="c-cjWCAs">BITB</p></div>
<div class="c-bszhDB"><h4 class="c-AfGhm">Fund Type</h4>
  <p class="c-cjWCAs">ETP</p></div>
<div class="c-bszhDB"><h4 class="c-AfGhm">Shares Outstanding</h4>
  <p class="c-cjWCAs">66,690,000</p></div>
<div class="c-bszhDB"><h4 class="c-AfGhm">CUSIP</h4>
  <p class="c-cjWCAs">09174C104</p></div>
<div class="c-bszhDB"><h4 class="c-AfGhm">ISIN</h4>
  <p class="c-cjWCAs">US09174C1045</p></div>
<div class="c-bszhDB"><h4 class="c-AfGhm">Net Assets (AUM)</h4>
  <p class="c-cjWCAs">$2,181,609,770</p></div>
<div class="c-bszhDB"><h4 class="c-AfGhm">Daily Volume (Shares)*</h4>
  <p class="c-cjWCAs">1,742,158</p></div>
<div class="c-columns-2"><div><div><div>
  <h4 class="c-hakyQ">Net Asset Value (NAV) and Market Price</h4>
  <p class="c-iAccHW"><!-- -->Data as of <!-- -->06/28/2026</p></div>
  <div><div><div>
    <div>NAV: <span>$32.71</span></div>
    <div>Market Price:<!-- --><span>$32.74</span></div></div>
  <div><div>NAV Change:<!-- --><span>$0.24<!-- --> /<!-- --><!-- -->0.75%</span></div></div></div>
<!-- a marketing date elsewhere on the page that must NOT be mistaken for the NAV strike -->
<p>By market capitalization as of December 31, 2024.</p>
<p>Portfolio characteristics as of 03/30/2026</p>
"""

BITB_WATCHLIST = EtfTickerEntry(
    ticker="BITB",
    name="Bitwise Bitcoin ETF",
    asset="BTC",
    issuer="Bitwise",
    launch_date="2024-01-11",
)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class ModuleConstantsTests(unittest.TestCase):
    """Pin the constants the workflow + health check depend on."""

    def test_source_name(self) -> None:
        """Stable source name keyed in RECURRING_ENDPOINTS + PRIMARY_TABLES."""
        self.assertEqual(SOURCE_NAME, "bitwise")

    def test_issuer_filter_is_bitwise(self) -> None:
        """v1 scope is Bitwise-issued ETFs only."""
        self.assertEqual(ISSUER_FILTER, "Bitwise")

    def test_bitb_product_url_pinned(self) -> None:
        """The BITB product page URL must be pinned and on the bitbetf domain."""
        self.assertIn("BITB", PRODUCT_URLS)
        self.assertTrue(PRODUCT_URLS["BITB"].startswith("https://bitbetf.com"))


# ---------------------------------------------------------------------------
# Field-level parsers
# ---------------------------------------------------------------------------


class ParseMoneyTests(unittest.TestCase):
    """Exercise the money/integer string coercion."""

    def test_strips_dollar_and_commas(self) -> None:
        """``$2,181,609,770`` → Decimal(2181609770)."""
        self.assertEqual(_parse_money("$2,181,609,770", field="x"), Decimal("2181609770"))

    def test_plain_integer_with_commas(self) -> None:
        """``66,690,000`` (no dollar sign) parses fine."""
        self.assertEqual(_parse_money("66,690,000", field="x"), Decimal("66690000"))

    def test_decimal_value(self) -> None:
        """``$32.71`` keeps its fractional part."""
        self.assertEqual(_parse_money("$32.71", field="x"), Decimal("32.71"))

    def test_dash_and_empty_and_none(self) -> None:
        """Missing sentinels become None."""
        self.assertIsNone(_parse_money("-", field="x"))
        self.assertIsNone(_parse_money("  ", field="x"))
        self.assertIsNone(_parse_money(None, field="x"))


class FindLabeledValueTests(unittest.TestCase):
    """The key-facts grid extractor anchors on label text, not CSS class."""

    def test_extracts_value_despite_churning_classes(self) -> None:
        """The c-* classes are ignored; the label text is the anchor."""
        self.assertEqual(
            _find_labeled_value(LIVE_FRAGMENT_HTML, "Shares Outstanding"), "66,690,000"
        )
        self.assertEqual(_find_labeled_value(LIVE_FRAGMENT_HTML, "CUSIP"), "09174C104")
        self.assertEqual(
            _find_labeled_value(LIVE_FRAGMENT_HTML, "Net Assets (AUM)"), "$2,181,609,770"
        )

    def test_missing_label_returns_none(self) -> None:
        """A label not present on the page yields None, not a crash."""
        self.assertIsNone(_find_labeled_value(LIVE_FRAGMENT_HTML, "Nonexistent Field"))


class FindNavTests(unittest.TestCase):
    """The NAV extractor must pick NAV, not the sibling Market Price."""

    def test_extracts_nav_not_market_price(self) -> None:
        """``NAV:`` anchors the right span; ``Market Price:`` is left alone."""
        from genkei.ingest.bitwise import _strip_html_comments

        nav = _find_nav(_strip_html_comments(LIVE_FRAGMENT_HTML))
        self.assertEqual(nav, Decimal("32.71"))


class FindNavAsOfTests(unittest.TestCase):
    """The as-of parser anchors on the NAV section's 'Data as of' stamp."""

    def test_extracts_nav_strike_date(self) -> None:
        """MM/DD/YYYY in the NAV section → the snapshot date."""
        from genkei.ingest.bitwise import _strip_html_comments

        d = _find_nav_as_of(_strip_html_comments(LIVE_FRAGMENT_HTML))
        self.assertEqual(d, date(2026, 6, 28))

    def test_ignores_unrelated_marketing_dates(self) -> None:
        """The 'as of December 31, 2024' / portfolio dates must not win."""
        from genkei.ingest.bitwise import _strip_html_comments

        d = _find_nav_as_of(_strip_html_comments(LIVE_FRAGMENT_HTML))
        # The portfolio-characteristics date is 03/30/2026; the NAV strike is
        # 06/28/2026. Anchoring on the NAV header keeps them from being
        # confused.
        self.assertEqual(d, date(2026, 6, 28))


# ---------------------------------------------------------------------------
# parse_snapshot — full extractor
# ---------------------------------------------------------------------------


class ParseSnapshotTests(unittest.TestCase):
    """End-to-end extractor over the live HTML fragment."""

    def test_extracts_full_snapshot(self) -> None:
        """All published financials land, with watchlist-sourced asset/issuer."""
        snap = parse_snapshot(
            LIVE_FRAGMENT_HTML, ticker="BITB", watchlist_entry=BITB_WATCHLIST
        )
        assert snap is not None
        self.assertEqual(snap.ticker, "BITB")
        self.assertEqual(snap.snapshot_date, date(2026, 6, 28))
        self.assertEqual(snap.asset, "BTC")
        self.assertEqual(snap.issuer, "Bitwise")
        self.assertEqual(snap.cusip, "09174C104")
        self.assertEqual(snap.isin, "US09174C1045")
        self.assertEqual(snap.nav_per_share_usd, Decimal("32.71"))
        self.assertEqual(snap.total_net_assets_usd, Decimal("2181609770"))
        self.assertEqual(snap.shares_outstanding, Decimal("66690000.0000"))

    def test_asset_and_issuer_come_from_watchlist(self) -> None:
        """Asset/issuer are a watchlist concept, not scraped from the page."""
        eth_entry = EtfTickerEntry(
            ticker="BITB", name="x", asset="ETH", issuer="Bitwise Capital"
        )
        snap = parse_snapshot(
            LIVE_FRAGMENT_HTML, ticker="BITB", watchlist_entry=eth_entry
        )
        assert snap is not None
        self.assertEqual(snap.asset, "ETH")
        self.assertEqual(snap.issuer, "Bitwise Capital")

    def test_missing_shares_drops_row(self) -> None:
        """A page missing Shares Outstanding yields None (skip, don't store)."""
        html = LIVE_FRAGMENT_HTML.replace("Shares Outstanding", "Shares Outstandingg")
        self.assertIsNone(
            parse_snapshot(html, ticker="BITB", watchlist_entry=BITB_WATCHLIST)
        )

    def test_missing_nav_drops_row(self) -> None:
        """A page missing the NAV span yields None."""
        html = LIVE_FRAGMENT_HTML.replace("NAV: <span>$32.71</span>", "")
        self.assertIsNone(
            parse_snapshot(html, ticker="BITB", watchlist_entry=BITB_WATCHLIST)
        )

    def test_missing_as_of_drops_row(self) -> None:
        """No NAV strike date → None (we won't guess the snapshot_date)."""
        html = LIVE_FRAGMENT_HTML.replace("Data as of <!-- -->06/28/2026", "")
        self.assertIsNone(
            parse_snapshot(html, ticker="BITB", watchlist_entry=BITB_WATCHLIST)
        )

    def test_incoherent_values_drop_row(self) -> None:
        """When nav x shares can't reconcile to net assets, skip the snapshot.

        Replace net assets with a figure ~50% off the implied value
        (32.71 x 66.69M ≈ $2.18B): a wildly different reported AUM means the
        three fields aren't one coherent snapshot.
        """
        html = LIVE_FRAGMENT_HTML.replace("$2,181,609,770", "$1,000,000,000")
        self.assertIsNone(
            parse_snapshot(html, ticker="BITB", watchlist_entry=BITB_WATCHLIST)
        )

    def test_reconciliation_tolerance_is_small(self) -> None:
        """The coherence gate is tight (a few percent), not permissive."""
        self.assertLessEqual(RECONCILE_TOLERANCE, Decimal("0.05"))


if __name__ == "__main__":
    unittest.main()
