"""Unit tests for the 8-K filing impact experiment (B-057).

Tests are scoped to the pure functions (parse_item_codes,
compute_windowed_returns, aggregate, stratifiers). The lake loaders
and run_event_study are exercised in live smoke during development;
not in CI.
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from decimal import Decimal

from genkei.experiments.eight_k_impact import (
    DEFAULT_WINDOWS,
    EventReturns,
    FilingEvent,
    PricePoint,
    _hit_rate,
    _mean,
    _median,
    aggregate,
    compute_windowed_returns,
    parse_item_codes,
    stratify_by_item_code,
    stratify_by_regime,
    stratify_by_ticker,
)


def _make_event(
    ticker: str = "AAPL",
    filed_at: date = date(2024, 6, 15),
    item_codes: tuple[str, ...] = ("2.02", "9.01"),
) -> FilingEvent:
    return FilingEvent(
        ticker=ticker,
        cik="0000320193",
        filed_at=filed_at,
        accession_number=f"acc-{ticker}-{filed_at}",
        item_codes=item_codes,
    )


def _make_prices(start: date, prices: list[Decimal | float]) -> list[PricePoint]:
    """Build a daily price series of length `len(prices)` from `start`."""
    return [
        PricePoint(ts=start + timedelta(days=i), adj_close=Decimal(str(p)))
        for i, p in enumerate(prices)
    ]


# ---------------------------------------------------------------------------
# parse_item_codes
# ---------------------------------------------------------------------------


class ParseItemCodesTests(unittest.TestCase):
    def test_single_code(self) -> None:
        self.assertEqual(parse_item_codes("7.01"), ("7.01",))

    def test_comma_list(self) -> None:
        self.assertEqual(parse_item_codes("7.01,9.01"), ("7.01", "9.01"))

    def test_whitespace_stripped(self) -> None:
        # SEC filings occasionally include trailing whitespace per code.
        self.assertEqual(parse_item_codes(" 2.02 , 9.01 "), ("2.02", "9.01"))

    def test_legacy_dotless_codes(self) -> None:
        # Pre-2009 8-Ks used "5" or "5,7" instead of "5.01"/"5.07".
        self.assertEqual(parse_item_codes("5,7"), ("5", "7"))

    def test_empty_and_none(self) -> None:
        self.assertEqual(parse_item_codes(None), ())
        self.assertEqual(parse_item_codes(""), ())
        self.assertEqual(parse_item_codes("   "), ())

    def test_drops_empty_entries(self) -> None:
        # "2.02,," collapses to ("2.02",) rather than ("2.02", "").
        self.assertEqual(parse_item_codes("2.02,,9.01"), ("2.02", "9.01"))


# ---------------------------------------------------------------------------
# compute_windowed_returns
# ---------------------------------------------------------------------------


class ComputeWindowedReturnsTests(unittest.TestCase):
    def test_flat_series_yields_zero_returns(self) -> None:
        # 100 days flat → all five windows fit (event at day 31).
        prices = _make_prices(date(2024, 1, 1), [100.0] * 100)
        windows = compute_windowed_returns(prices, event_date=date(2024, 2, 1))
        for label in ("pre_5d", "same_day", "post_1d", "post_5d", "post_30d"):
            self.assertEqual(windows[label], Decimal("0"))

    def test_climbing_series_yields_positive_returns(self) -> None:
        # +1 per day from 100 → 199 over 100 days.
        prices = _make_prices(
            date(2024, 1, 1), [100.0 + i for i in range(100)]
        )
        windows = compute_windowed_returns(prices, event_date=date(2024, 2, 1))
        # All windows should be > 0.
        for label, value in windows.items():
            self.assertGreater(value, Decimal("0"), f"window {label!r}")

    def test_post_5d_specific_value(self) -> None:
        # Day 0 price 100, day 5 price 105 → +5%.
        prices = _make_prices(date(2024, 1, 1), [100.0 + i for i in range(100)])
        windows = compute_windowed_returns(prices, event_date=date(2024, 2, 1))
        # event_date is day 31 (Feb 1). Day 31 price = 100 + 31 = 131.
        # Day 36 price = 100 + 36 = 136. Return = (136-131)/131 ≈ 3.817%.
        self.assertAlmostEqual(float(windows["post_5d"]), 100 * 5 / 131, places=3)

    def test_weekend_filing_uses_prior_trading_day_as_start(self) -> None:
        # Prices Mon-Fri only (5 days), filing on Saturday. The "same_day"
        # start (T-1) lands on Friday, end (T) also lands on Friday since
        # there's no later price ≤ Saturday — wait, that's not right.
        # _price_at_or_before(target=Saturday) returns Friday;
        # _price_at_or_after(target=Saturday) returns the following
        # Monday (the next price ≥ Saturday).
        # Let's construct: Mon=100, Tue=101, Wed=102, Thu=103, Fri=104,
        # then SKIP Sat/Sun, Mon=110 (big jump over the weekend).
        prices = [
            PricePoint(ts=date(2024, 6, 10), adj_close=Decimal("100")),
            PricePoint(ts=date(2024, 6, 11), adj_close=Decimal("101")),
            PricePoint(ts=date(2024, 6, 12), adj_close=Decimal("102")),
            PricePoint(ts=date(2024, 6, 13), adj_close=Decimal("103")),
            PricePoint(ts=date(2024, 6, 14), adj_close=Decimal("104")),  # Fri
            PricePoint(ts=date(2024, 6, 17), adj_close=Decimal("110")),  # Mon
        ]
        # Filed Saturday 2024-06-15. same_day window: start = T-1 = Fri
        # (104), end = T = Sat → no price on/after but before Mon's
        # close. _price_at_or_after gives Mon = 110. Return = (110-104)/104
        # ≈ 5.769%.
        windows = compute_windowed_returns(
            prices, event_date=date(2024, 6, 15)
        )
        self.assertAlmostEqual(
            float(windows["same_day"]), 100 * 6 / 104, places=3
        )

    def test_event_before_series_start_yields_nulls(self) -> None:
        # Event in Jan 2023, prices only from Jan 2024 — no pre-window.
        prices = _make_prices(date(2024, 1, 1), [100.0] * 30)
        windows = compute_windowed_returns(prices, event_date=date(2023, 1, 1))
        # All windows should be None (no price ≤ event_date - X exists).
        for label, value in windows.items():
            self.assertIsNone(value, f"window {label!r}")

    def test_event_after_series_end_yields_nulls(self) -> None:
        # Event in 2026, prices only through 2024 — no post-window.
        prices = _make_prices(date(2024, 1, 1), [100.0] * 30)
        windows = compute_windowed_returns(prices, event_date=date(2026, 1, 1))
        # post_* windows are None (no price ≥ event_date + N exists).
        self.assertIsNone(windows["post_1d"])
        self.assertIsNone(windows["post_5d"])
        self.assertIsNone(windows["post_30d"])

    def test_zero_start_price_yields_none(self) -> None:
        # Defensive — never happens in real data but math shouldn't crash.
        prices = [
            PricePoint(ts=date(2024, 6, 10), adj_close=Decimal("0")),
            PricePoint(ts=date(2024, 6, 15), adj_close=Decimal("100")),
        ]
        windows = compute_windowed_returns(prices, event_date=date(2024, 6, 15))
        # same_day's start = 2024-06-14 → picks 2024-06-10 close (=0).
        # _pct_return on 0 start returns None.
        self.assertIsNone(windows["same_day"])


# ---------------------------------------------------------------------------
# aggregate
# ---------------------------------------------------------------------------


def _make_event_returns(
    ticker: str,
    same_day: float | None,
    *,
    item_codes: tuple[str, ...] = ("2.02",),
    regime: str | None = "mixed",
) -> EventReturns:
    return EventReturns(
        event=_make_event(ticker=ticker, item_codes=item_codes),
        windows={
            "pre_5d": Decimal("0"),
            "same_day": Decimal(str(same_day)) if same_day is not None else None,
            "post_1d": Decimal("0"),
            "post_5d": Decimal("0"),
            "post_30d": Decimal("0"),
        },
        regime=regime,
    )


class AggregateTests(unittest.TestCase):
    def test_empty_input_yields_zero_event_stratum(self) -> None:
        stats = aggregate([])
        self.assertEqual(stats.n_events, 0)
        for label, _, _ in DEFAULT_WINDOWS:
            self.assertIsNone(stats.mean_pct[label])
            self.assertIsNone(stats.median_pct[label])
            self.assertIsNone(stats.hit_rate_pct[label])

    def test_basic_mean_and_median(self) -> None:
        events = [
            _make_event_returns("AAPL", 1.0),
            _make_event_returns("MSFT", 3.0),
            _make_event_returns("NVDA", 5.0),
        ]
        stats = aggregate(events)
        self.assertEqual(stats.n_events, 3)
        self.assertEqual(stats.mean_pct["same_day"], Decimal("3"))
        self.assertEqual(stats.median_pct["same_day"], Decimal("3"))
        self.assertEqual(stats.hit_rate_pct["same_day"], Decimal("100"))

    def test_hit_rate_counts_strict_positives_only(self) -> None:
        events = [
            _make_event_returns("a", -1.0),
            _make_event_returns("b", 0.0),  # not a hit
            _make_event_returns("c", 1.0),
        ]
        stats = aggregate(events)
        # 1 of 3 strictly positive → 33.33%.
        self.assertAlmostEqual(float(stats.hit_rate_pct["same_day"]), 100 / 3, places=2)

    def test_null_window_dropped_from_aggregate(self) -> None:
        # Mix of None and real values for same_day.
        events = [
            _make_event_returns("a", 1.0),
            _make_event_returns("b", None),
            _make_event_returns("c", 5.0),
        ]
        stats = aggregate(events)
        # Mean computed only over non-null values: (1+5)/2 = 3.
        self.assertEqual(stats.mean_pct["same_day"], Decimal("3"))
        # n_events is still 3 (the null'd event still exists, just
        # doesn't contribute to that window).
        self.assertEqual(stats.n_events, 3)


# ---------------------------------------------------------------------------
# Stratifiers
# ---------------------------------------------------------------------------


class StratifierTests(unittest.TestCase):
    def test_stratify_by_ticker(self) -> None:
        events = [
            _make_event_returns("AAPL", 1.0),
            _make_event_returns("AAPL", 3.0),
            _make_event_returns("MSFT", 5.0),
        ]
        strata = stratify_by_ticker(events)
        self.assertEqual(len(strata), 2)
        # Sorted alphabetically.
        self.assertEqual(strata[0].stratum_key, "AAPL")
        self.assertEqual(strata[0].n_events, 2)
        self.assertEqual(strata[0].mean_pct["same_day"], Decimal("2"))
        self.assertEqual(strata[1].stratum_key, "MSFT")
        self.assertEqual(strata[1].n_events, 1)

    def test_stratify_by_item_code_counts_each_independently(self) -> None:
        # The 2.02,9.01 event contributes to BOTH the 2.02 and 9.01
        # buckets — this is the load-bearing semantic for item-code
        # stratification (Item 9.01 almost always rides with Items
        # 2.02 / 5.02, so counting it joint would conflate them).
        events = [
            _make_event_returns("AAPL", 1.0, item_codes=("2.02", "9.01")),
            _make_event_returns("AAPL", 3.0, item_codes=("2.02",)),
            _make_event_returns("MSFT", 5.0, item_codes=("9.01",)),
        ]
        strata = stratify_by_item_code(events)
        by_key = {s.stratum_key: s for s in strata}
        # 2.02 has 2 events (AAPL with 2.02,9.01 + AAPL with 2.02).
        self.assertEqual(by_key["2.02"].n_events, 2)
        # 9.01 has 2 events (AAPL with 2.02,9.01 + MSFT with 9.01).
        self.assertEqual(by_key["9.01"].n_events, 2)

    def test_stratify_by_regime_handles_unknown(self) -> None:
        # Some events fall outside the regime view's coverage (pre-2006).
        events = [
            _make_event_returns("a", 1.0, regime="risk_on"),
            _make_event_returns("b", -1.0, regime="risk_off"),
            _make_event_returns("c", 0.5, regime=None),
        ]
        strata = stratify_by_regime(events)
        by_key = {s.stratum_key: s for s in strata}
        self.assertEqual(by_key["risk_on"].n_events, 1)
        self.assertEqual(by_key["risk_off"].n_events, 1)
        # None regime is bucketed as "unknown" rather than silently
        # dropped — explicit honest record over missing-coverage data.
        self.assertEqual(by_key["unknown"].n_events, 1)


# ---------------------------------------------------------------------------
# Aggregate helpers
# ---------------------------------------------------------------------------


class AggregateHelperTests(unittest.TestCase):
    def test_mean(self) -> None:
        self.assertEqual(_mean([Decimal("1"), Decimal("2"), Decimal("3")]), Decimal("2"))
        self.assertIsNone(_mean([]))

    def test_median_odd_count(self) -> None:
        self.assertEqual(_median([Decimal("1"), Decimal("3"), Decimal("5")]), Decimal("3"))

    def test_median_even_count(self) -> None:
        self.assertEqual(
            _median([Decimal("1"), Decimal("2"), Decimal("4"), Decimal("8")]),
            Decimal("3"),  # (2 + 4) / 2
        )

    def test_median_empty(self) -> None:
        self.assertIsNone(_median([]))

    def test_hit_rate(self) -> None:
        self.assertEqual(
            _hit_rate([Decimal("-1"), Decimal("0"), Decimal("1"), Decimal("2")]),
            Decimal("50"),  # 2 of 4 strictly positive
        )
        self.assertIsNone(_hit_rate([]))


if __name__ == "__main__":
    unittest.main()
