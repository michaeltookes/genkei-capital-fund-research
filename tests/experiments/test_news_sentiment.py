"""Unit tests for the news-sentiment experiment (B-056).

Pure-function tests for the aggregator, return computation, alignment,
correlation math (Pearson + Spearman, including rank-with-ties), and
the per-quartile breakdown. Database-touching loaders are exercised
behind the CLI tests with mocked _fetch helpers.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from genkei.experiments.news_sentiment import (
    DEFAULT_HORIZON_DAYS,
    DEFAULT_MIN_ARTICLES_PER_DAY,
    MIN_OBSERVATIONS_FOR_SIGNAL,
    AlignedPoint,
    ArticleRow,
    ReturnPoint,
    SentimentPoint,
    _pearson_correlation,
    _quartile_means,
    _rank_with_ties,
    _spearman_correlation,
    aggregate_daily_sentiment,
    align_sentiment_with_returns,
    compute_correlation,
    compute_daily_returns,
)


def _utc(year: int, month: int, day: int, hour: int = 12) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


class AggregateDailySentimentTests(unittest.TestCase):
    def test_groups_by_asset_day_and_means_non_null_tones(self) -> None:
        articles = [
            ArticleRow(_utc(2026, 6, 10, 9), "AAPL", Decimal("-3.0"), None, None),
            ArticleRow(_utc(2026, 6, 10, 12), "AAPL", Decimal("1.0"), None, None),
            # Different day → different bucket.
            ArticleRow(_utc(2026, 6, 11, 12), "AAPL", Decimal("4.0"), None, None),
            # Different asset → different bucket.
            ArticleRow(_utc(2026, 6, 10, 12), "BTC", Decimal("-1.0"), None, None),
        ]
        pts = aggregate_daily_sentiment(articles)
        self.assertEqual(len(pts), 3)
        # Sorted by (asset, ts) for deterministic downstream order.
        self.assertEqual(pts[0].asset, "AAPL")
        self.assertEqual(pts[0].ts, date(2026, 6, 10))
        self.assertEqual(pts[0].article_count, 2)
        self.assertAlmostEqual(pts[0].mean_tone, -1.0)

    def test_null_tone_counts_toward_article_count_not_mean(self) -> None:
        articles = [
            ArticleRow(_utc(2026, 6, 10), "AAPL", Decimal("4.0"), None, None),
            ArticleRow(_utc(2026, 6, 10), "AAPL", None, None, None),
        ]
        [pt] = aggregate_daily_sentiment(articles)
        # Mean over the one non-NULL article.
        self.assertEqual(pt.mean_tone, 4.0)
        # But the article_count reflects all rows.
        self.assertEqual(pt.article_count, 2)

    def test_all_null_tone_day_emits_row_with_none_mean(self) -> None:
        articles = [
            ArticleRow(_utc(2026, 6, 10), "AAPL", None, None, None),
            ArticleRow(_utc(2026, 6, 10), "AAPL", None, None, None),
        ]
        [pt] = aggregate_daily_sentiment(articles)
        # The bucket still emits — we keep the article_count signal.
        self.assertIsNone(pt.mean_tone)
        self.assertEqual(pt.article_count, 2)

    def test_published_at_floor_uses_utc_date(self) -> None:
        # 22:00 ET = 02:00 UTC next day → article bucketed on the UTC day.
        articles = [
            ArticleRow(
                datetime(2026, 6, 10, 22, 0, tzinfo=timezone.utc),
                "AAPL",
                Decimal("1.0"),
                None,
                None,
            ),
            ArticleRow(
                datetime(2026, 6, 11, 2, 0, tzinfo=timezone.utc),
                "AAPL",
                Decimal("2.0"),
                None,
                None,
            ),
        ]
        pts = aggregate_daily_sentiment(articles)
        self.assertEqual(len(pts), 2)
        # First row is on 2026-06-10 UTC; second on 2026-06-11 UTC.
        self.assertEqual([p.ts for p in pts], [date(2026, 6, 10), date(2026, 6, 11)])

    def test_positive_and_negative_means_computed_independently(self) -> None:
        # GDELT V1.5 carries pos + neg as separate channels; the mean
        # of each is over its own non-null subset.
        articles = [
            ArticleRow(_utc(2026, 6, 10), "AAPL", Decimal("0.0"), Decimal("4.0"), Decimal("2.0")),
            ArticleRow(_utc(2026, 6, 10), "AAPL", Decimal("0.0"), None, Decimal("6.0")),
        ]
        [pt] = aggregate_daily_sentiment(articles)
        self.assertEqual(pt.positive_mean, 4.0)
        self.assertEqual(pt.negative_mean, 4.0)


class ComputeDailyReturnsTests(unittest.TestCase):
    def test_first_day_return_is_none(self) -> None:
        returns = compute_daily_returns(
            "BTC",
            days=[date(2026, 6, 10), date(2026, 6, 11)],
            closes=[100.0, 110.0],
        )
        self.assertIsNone(returns[0].pct_return)
        # 10% gain over the second day.
        self.assertAlmostEqual(returns[1].pct_return, 10.0)

    def test_zero_prior_close_yields_none(self) -> None:
        # Defensive — don't emit Inf if a price feed silently writes 0.
        returns = compute_daily_returns(
            "X",
            days=[date(2026, 6, 10), date(2026, 6, 11)],
            closes=[0.0, 50.0],
        )
        self.assertIsNone(returns[1].pct_return)

    def test_null_close_passes_through_and_does_not_set_prev(self) -> None:
        # A None close in the middle of the series shouldn't poison
        # the next return — the prior-non-None close should still be
        # the denominator.
        returns = compute_daily_returns(
            "X",
            days=[date(2026, 6, 10), date(2026, 6, 11), date(2026, 6, 12)],
            closes=[100.0, None, 110.0],
        )
        self.assertIsNone(returns[1].pct_return)
        # Day-12 return uses Day-10 as denominator (the most recent
        # non-None close).
        self.assertAlmostEqual(returns[2].pct_return, 10.0)

    def test_length_mismatch_raises(self) -> None:
        with self.assertRaises(ValueError):
            compute_daily_returns("X", days=[date(2026, 6, 10)], closes=[1.0, 2.0])


class AlignSentimentWithReturnsTests(unittest.TestCase):
    def _sent(self, ts: date, tone: float | None, n: int = 5) -> SentimentPoint:
        return SentimentPoint(
            ts=ts,
            asset="AAPL",
            mean_tone=tone,
            article_count=n,
            positive_mean=None,
            negative_mean=None,
        )

    def _ret(
        self, ts: date, ret: float | None, *, close: float | None = 100.0
    ) -> ReturnPoint:
        return ReturnPoint(ts=ts, asset="AAPL", close=close, pct_return=ret)

    def test_horizon_1_joins_sentiment_day_to_next_day_return(self) -> None:
        sent = [self._sent(date(2026, 6, 10), 1.5)]
        rets = [
            self._ret(date(2026, 6, 10), None, close=100.0),
            self._ret(date(2026, 6, 11), 0.5, close=100.5),
        ]
        aligned = align_sentiment_with_returns(
            sent, rets, horizon_days=1, min_articles_per_day=1
        )
        self.assertEqual(len(aligned), 1)
        self.assertEqual(aligned[0].sentiment_day, date(2026, 6, 10))
        self.assertEqual(aligned[0].return_day, date(2026, 6, 11))
        self.assertAlmostEqual(aligned[0].forward_return_pct, 0.5)

    def test_horizon_5_joins_sentiment_to_5_day_forward(self) -> None:
        sent = [self._sent(date(2026, 6, 10), 1.5)]
        rets = [
            self._ret(date(2026, 6, 10), None, close=100.0),
            self._ret(date(2026, 6, 15), 2.5, close=110.0),
        ]
        aligned = align_sentiment_with_returns(
            sent, rets, horizon_days=5, min_articles_per_day=1
        )
        self.assertEqual(aligned[0].return_day, date(2026, 6, 15))
        # Uses cumulative close-to-close return, not the target day's daily return.
        self.assertAlmostEqual(aligned[0].forward_return_pct, 10.0)

    def test_below_min_articles_dropped(self) -> None:
        # Single-article days are noise — drop by default.
        sent = [self._sent(date(2026, 6, 10), 1.5, n=2)]
        rets = [self._ret(date(2026, 6, 11), 0.5)]
        aligned = align_sentiment_with_returns(
            sent, rets, horizon_days=1, min_articles_per_day=3
        )
        self.assertEqual(aligned, [])

    def test_null_mean_tone_dropped(self) -> None:
        # No tone signal to correlate against → drop.
        sent = [self._sent(date(2026, 6, 10), None)]
        rets = [self._ret(date(2026, 6, 11), 0.5)]
        aligned = align_sentiment_with_returns(
            sent, rets, horizon_days=1, min_articles_per_day=1
        )
        self.assertEqual(aligned, [])

    def test_missing_return_day_dropped(self) -> None:
        # Calendar gap on return side — common for equities over weekends.
        sent = [self._sent(date(2026, 6, 10), 1.5)]
        rets = []  # No matching return row.
        aligned = align_sentiment_with_returns(
            sent, rets, horizon_days=1, min_articles_per_day=1
        )
        self.assertEqual(aligned, [])

    def test_horizon_zero_or_negative_raises(self) -> None:
        with self.assertRaises(ValueError):
            align_sentiment_with_returns([], [], horizon_days=0)
        with self.assertRaises(ValueError):
            align_sentiment_with_returns([], [], horizon_days=-1)


class PearsonCorrelationTests(unittest.TestCase):
    def test_perfect_positive_correlation_is_1(self) -> None:
        # y = 2x — perfectly linear.
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [2.0, 4.0, 6.0, 8.0, 10.0]
        self.assertAlmostEqual(_pearson_correlation(xs, ys), 1.0)

    def test_perfect_negative_correlation_is_minus_1(self) -> None:
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [10.0, 8.0, 6.0, 4.0, 2.0]
        self.assertAlmostEqual(_pearson_correlation(xs, ys), -1.0)

    def test_zero_variance_returns_none(self) -> None:
        # No variation in x → correlation undefined, not 0.
        self.assertIsNone(_pearson_correlation([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]))
        self.assertIsNone(_pearson_correlation([1.0, 2.0, 3.0], [5.0, 5.0, 5.0]))

    def test_uncorrelated_near_zero(self) -> None:
        # Two-cycle anti-pattern that should give near-zero correlation.
        xs = [1.0, 2.0, 3.0, 4.0]
        ys = [1.0, 4.0, 2.0, 3.0]
        r = _pearson_correlation(xs, ys)
        assert r is not None
        self.assertLess(abs(r), 0.5)


class SpearmanCorrelationTests(unittest.TestCase):
    def test_monotonic_nonlinear_returns_1(self) -> None:
        # Pearson on y = x^2 over positives isn't 1, but Spearman is —
        # the relationship is perfectly monotonic.
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [1.0, 4.0, 9.0, 16.0, 25.0]
        pearson = _pearson_correlation(xs, ys)
        spearman = _spearman_correlation(xs, ys)
        assert pearson is not None and spearman is not None
        self.assertLess(pearson, 1.0)
        self.assertAlmostEqual(spearman, 1.0)

    def test_ties_are_average_ranked(self) -> None:
        # Two values tied at 2.0 should each get rank (2+3)/2 = 2.5.
        ranks = _rank_with_ties([1.0, 2.0, 2.0, 4.0])
        self.assertEqual(ranks, [1.0, 2.5, 2.5, 4.0])

    def test_all_ties_returns_none_on_correlation(self) -> None:
        # All x tied → zero rank variance → correlation undefined.
        xs = [2.0, 2.0, 2.0, 2.0]
        ys = [1.0, 2.0, 3.0, 4.0]
        self.assertIsNone(_spearman_correlation(xs, ys))


class QuartileMeansTests(unittest.TestCase):
    def _aligned(self, tone: float, ret: float) -> AlignedPoint:
        return AlignedPoint(
            sentiment_day=date(2026, 6, 10),
            return_day=date(2026, 6, 11),
            asset="X",
            mean_tone=tone,
            article_count=5,
            forward_return_pct=ret,
        )

    def test_four_buckets_equal_size_when_n_divisible(self) -> None:
        aligned = [
            self._aligned(t, t * 0.1) for t in (-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 4.0)
        ]
        qs = _quartile_means(aligned, n_quartiles=4)
        self.assertEqual(len(qs), 4)
        # Each quartile carries 2 of the 8 points.
        self.assertTrue(all(q.n == 2 for q in qs))
        # Q1 (most negative) carries the lowest tones; Q4 the highest.
        self.assertLess(qs[0].mean_tone, qs[-1].mean_tone)

    def test_last_bucket_grabs_remainder_when_n_not_divisible(self) -> None:
        aligned = [self._aligned(float(i), 0.0) for i in range(10)]
        qs = _quartile_means(aligned, n_quartiles=4)
        # 10 / 4 = 2 with remainder 2 → first three buckets get 2 each,
        # last bucket gets 4. No row dropped to integer truncation.
        sizes = [q.n for q in qs]
        self.assertEqual(sizes, [2, 2, 2, 4])
        self.assertEqual(sum(sizes), 10)

    def test_invalid_n_quartiles_raises(self) -> None:
        with self.assertRaises(ValueError):
            _quartile_means([], n_quartiles=1)


class ComputeCorrelationTests(unittest.TestCase):
    def _aligned(self, n: int) -> list[AlignedPoint]:
        # Synthetic series with mild positive correlation:
        # tone_i = i, forward_return = i*0.1 + small jitter.
        return [
            AlignedPoint(
                sentiment_day=date(2026, 6, 10),
                return_day=date(2026, 6, 11),
                asset="X",
                mean_tone=float(i),
                article_count=5,
                forward_return_pct=i * 0.1,
            )
            for i in range(n)
        ]

    def test_insufficient_data_below_floor(self) -> None:
        aligned = self._aligned(MIN_OBSERVATIONS_FOR_SIGNAL - 1)
        report = compute_correlation(aligned, asset="X", horizon_days=1)
        self.assertEqual(report.status, "insufficient_data")
        self.assertIsNone(report.pearson)
        self.assertIsNone(report.spearman)
        self.assertEqual(report.quartiles, [])
        self.assertEqual(report.n_observations, MIN_OBSERVATIONS_FOR_SIGNAL - 1)

    def test_ok_status_at_or_above_floor(self) -> None:
        aligned = self._aligned(MIN_OBSERVATIONS_FOR_SIGNAL)
        report = compute_correlation(aligned, asset="X", horizon_days=1)
        self.assertEqual(report.status, "ok")
        # Synthetic series is perfectly correlated.
        assert report.pearson is not None
        self.assertAlmostEqual(report.pearson, 1.0, places=4)
        assert report.spearman is not None
        self.assertAlmostEqual(report.spearman, 1.0, places=4)
        # Four quartiles by default.
        self.assertEqual(len(report.quartiles), 4)


class ConstantsTests(unittest.TestCase):
    def test_default_horizon_is_one_day(self) -> None:
        self.assertEqual(DEFAULT_HORIZON_DAYS, 1)

    def test_min_articles_floor_kept_at_three(self) -> None:
        self.assertEqual(DEFAULT_MIN_ARTICLES_PER_DAY, 3)

    def test_min_observations_floor_is_thirty(self) -> None:
        # 30 is the conventional threshold for "approximately normal"
        # sampling distributions; pin it so a careless change doesn't
        # lower the bar silently.
        self.assertEqual(MIN_OBSERVATIONS_FOR_SIGNAL, 30)


if __name__ == "__main__":
    unittest.main()
