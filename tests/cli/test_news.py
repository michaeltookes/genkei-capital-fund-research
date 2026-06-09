"""Unit tests for ``genkei news`` (B-043).

Pure-function tests for the SQL-query builder, the in-memory clustering,
and the human formatter. Database-touching paths are exercised via
``unittest.mock`` for ``db.connection`` — the integration shape lives
behind ``genkei query`` and the GDELT ingest tests, not here.
"""

from __future__ import annotations

import io
import json as json_mod
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import typer

from genkei.cli import main
from genkei.cli.news import (
    ARTICLE_POOL_CAP,
    HORIZON,
    SAMPLE_URLS_PER_CLUSTER,
    _build_article_query,
    _cluster_articles,
    _day_sort_key,
    _format_clusters_human,
    _resolve_watchlist_label,
)

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

WATCHLIST_YAML = (
    "crypto:\n"
    "  primary:\n"
    "    - symbol: BTC\n"
    "      name: Bitcoin\n"
    "      coingecko_id: bitcoin\n"
    "      tier: primary\n"
    "equities:\n"
    "  primary:\n"
    "    - symbol: AAPL\n"
    "      name: Apple Inc.\n"
    "      cik: '0000320193'\n"
    "      tier: primary\n"
)


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def _watchlist_path(case: unittest.TestCase) -> Path:
    ctx = TemporaryDirectory()
    case.addCleanup(ctx.cleanup)
    tmp = Path(ctx.name)
    path = tmp / "watchlists.yml"
    path.write_text(WATCHLIST_YAML, encoding="utf-8")
    return path


class ResolveWatchlistLabelTests(unittest.TestCase):
    def test_none_returns_none(self) -> None:
        self.assertIsNone(
            _resolve_watchlist_label(
                ticker=None, asset=None, config=Path("/dev/null")
            )
        )

    def test_ticker_returns_upper_case_symbol(self) -> None:
        cfg = _watchlist_path(self)
        # Lower-case input → upper-case label per the ingester's convention.
        self.assertEqual(
            _resolve_watchlist_label(ticker="aapl", asset=None, config=cfg),
            "AAPL",
        )

    def test_asset_returns_upper_case_symbol(self) -> None:
        cfg = _watchlist_path(self)
        self.assertEqual(
            _resolve_watchlist_label(ticker=None, asset="btc", config=cfg),
            "BTC",
        )

    def test_mutually_exclusive_raises(self) -> None:
        with self.assertRaises(typer.BadParameter):
            _resolve_watchlist_label(
                ticker="AAPL", asset="BTC", config=Path("/dev/null")
            )

    def test_unknown_ticker_fails_loud(self) -> None:
        cfg = _watchlist_path(self)
        # Better to fail loud than silently match zero rows.
        with self.assertRaises(typer.BadParameter):
            _resolve_watchlist_label(ticker="MSFT", asset=None, config=cfg)

    def test_unknown_asset_fails_loud(self) -> None:
        cfg = _watchlist_path(self)
        with self.assertRaises(typer.BadParameter):
            _resolve_watchlist_label(ticker=None, asset="ETH", config=cfg)


class BuildArticleQueryTests(unittest.TestCase):
    """Pin the SQL filter shape — silent drift here misroutes queries."""

    def _build(self, **kwargs) -> tuple[str, list]:
        defaults = dict(
            matched_label=None,
            theme=None,
            topic=None,
            since=None,
            until=None,
            tone_min=None,
            tone_max=None,
            source=None,
            pool_cap=100,
        )
        defaults.update(kwargs)
        return _build_article_query(**defaults)

    def test_no_filters_just_selects_with_pool_cap(self) -> None:
        sql, params = self._build()
        # The base SELECT + ORDER BY + LIMIT placeholder.
        self.assertIn("FROM gdelt.gkg WHERE 1=1", sql)
        self.assertIn("ORDER BY published_at DESC LIMIT %s", sql)
        # No filter placeholders → only the pool_cap placeholder remains.
        self.assertEqual(params, [100])

    def test_matched_label_uses_gin_contains(self) -> None:
        sql, params = self._build(matched_label="AAPL")
        self.assertIn("matched_assets @> %s", sql)
        # @> takes an array, not a scalar — bind a single-element list.
        self.assertEqual(params[0], ["AAPL"])

    def test_theme_uses_gin_contains(self) -> None:
        sql, params = self._build(theme="ECON_BITCOIN")
        self.assertIn("themes @> %s", sql)
        self.assertEqual(params[0], ["ECON_BITCOIN"])

    def test_topic_substring_searches_url_and_themes(self) -> None:
        sql, params = self._build(topic="AI capex")
        self.assertIn("document_identifier ILIKE %s", sql)
        self.assertIn("array_to_string(themes, ' ') ILIKE %s", sql)
        # Same pattern bound twice (URL + themes), with %...% wrap.
        self.assertEqual(params[0], "%AI capex%")
        self.assertEqual(params[1], "%AI capex%")

    def test_since_uses_utc_date_lower_bound(self) -> None:
        sql, params = self._build(since=date(2026, 5, 1))
        self.assertIn("(published_at AT TIME ZONE 'UTC')::date >= %s", sql)
        self.assertEqual(params[0], date(2026, 5, 1))

    def test_until_uses_utc_date_upper_bound(self) -> None:
        # --until is a date, not a datetime — compare against the UTC
        # date column so callers don't have to think about boundaries.
        sql, params = self._build(until=date(2026, 6, 9))
        self.assertIn("(published_at AT TIME ZONE 'UTC')::date <= %s", sql)
        self.assertEqual(params[0], date(2026, 6, 9))

    def test_tone_window_both_bounds(self) -> None:
        sql, params = self._build(tone_min=-5.0, tone_max=5.0)
        self.assertIn("tone >= %s", sql)
        self.assertIn("tone <= %s", sql)
        self.assertEqual(params, [-5.0, 5.0, 100])

    def test_source_lower_cased_match(self) -> None:
        sql, params = self._build(source="NYTimes.com")
        # Case-insensitive equality via lower() on both sides.
        self.assertIn("lower(source_common_name) = %s", sql)
        self.assertEqual(params[0], "nytimes.com")

    def test_combined_filters_ordered_consistently(self) -> None:
        # Stable param ordering matters for psycopg parameter binding.
        _sql, params = self._build(
            matched_label="BTC",
            theme="ECON_BITCOIN",
            topic="rally",
            since=date(2026, 5, 1),
            until=date(2026, 6, 9),
            tone_min=-2.0,
            tone_max=5.0,
            source="cnbc.com",
            pool_cap=42,
        )
        # Last param is always pool_cap.
        self.assertEqual(params[-1], 42)
        # First param is matched_assets (the first filter added).
        self.assertEqual(params[0], ["BTC"])
        # Topic binds twice (URL + themes).
        self.assertEqual(params.count("%rally%"), 2)


class ClusterArticlesTests(unittest.TestCase):
    """Group raw articles → cluster rows; pin sort + tone-mean math."""

    def _article(self, **overrides) -> dict:
        base = {
            "published_at": datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc),
            "source_common_name": "nytimes.com",
            "document_identifier": "https://example.com/a",
            "tone": Decimal("-2.0"),
            "matched_assets": ["AAPL"],
            "themes": ["ECON_STOCKMARKET"],
        }
        base.update(overrides)
        return base

    def test_groups_by_date_and_source(self) -> None:
        articles = [
            self._article(document_identifier="https://example.com/a"),
            self._article(document_identifier="https://example.com/b"),
            # Different source same day → different cluster.
            self._article(
                source_common_name="bloomberg.com",
                document_identifier="https://example.com/c",
            ),
            # Same source different day → different cluster.
            self._article(
                published_at=datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc),
                document_identifier="https://example.com/d",
            ),
        ]
        clusters = _cluster_articles(articles, limit=10)
        self.assertEqual(len(clusters), 3)
        # Sort: article_count DESC, then day DESC. The 2-article cluster
        # leads; the two 1-article clusters tie on count, broken by day.
        self.assertEqual(clusters[0]["article_count"], 2)
        self.assertEqual(clusters[0]["source"], "nytimes.com")
        # Second-place ties: the 2026-06-09 one comes before 2026-06-08.
        self.assertEqual(clusters[1]["day"], "2026-06-09")
        self.assertEqual(clusters[2]["day"], "2026-06-08")

    def test_groups_offset_aware_timestamps_by_utc_date(self) -> None:
        articles = [
            self._article(
                # Local date is June 8, but the UTC date is June 9.
                published_at=datetime(
                    2026, 6, 8, 20, 15, tzinfo=timezone(timedelta(hours=-4))
                ),
                document_identifier="https://example.com/utc-midnight",
            )
        ]
        [cluster] = _cluster_articles(articles, limit=10)
        self.assertEqual(cluster["day"], "2026-06-09")

    def test_mean_tone_computed_from_non_null_tones(self) -> None:
        articles = [
            self._article(tone=Decimal("-4.0")),
            self._article(tone=Decimal("0.0")),
            self._article(tone=None),
        ]
        [cluster] = _cluster_articles(articles, limit=10)
        # Mean over the 2 non-null tones is -2.0.
        self.assertEqual(cluster["mean_tone"], -2.0)
        self.assertEqual(cluster["article_count"], 3)

    def test_mean_tone_none_when_every_article_null(self) -> None:
        articles = [self._article(tone=None), self._article(tone=None)]
        [cluster] = _cluster_articles(articles, limit=10)
        self.assertIsNone(cluster["mean_tone"])

    def test_matched_assets_unioned_and_sorted(self) -> None:
        articles = [
            self._article(matched_assets=["AAPL"]),
            self._article(matched_assets=["BTC", "AAPL"]),
            self._article(matched_assets=["BTC"]),
        ]
        [cluster] = _cluster_articles(articles, limit=10)
        # Set-dedup + sorted for stable output.
        self.assertEqual(cluster["matched_assets"], ["AAPL", "BTC"])

    def test_sample_urls_capped_at_constant(self) -> None:
        articles = [
            self._article(document_identifier=f"https://example.com/{i}")
            for i in range(SAMPLE_URLS_PER_CLUSTER + 5)
        ]
        [cluster] = _cluster_articles(articles, limit=10)
        self.assertEqual(len(cluster["sample_urls"]), SAMPLE_URLS_PER_CLUSTER)

    def test_sample_urls_dedupes_within_cluster(self) -> None:
        # GDELT occasionally lands the same URL twice in different
        # 15-min slots — the sample shouldn't repeat it.
        articles = [
            self._article(document_identifier="https://example.com/a"),
            self._article(document_identifier="https://example.com/a"),
            self._article(document_identifier="https://example.com/b"),
        ]
        [cluster] = _cluster_articles(articles, limit=10)
        self.assertEqual(cluster["sample_urls"], [
            "https://example.com/a",
            "https://example.com/b",
        ])

    def test_limit_truncates(self) -> None:
        articles = []
        for i in range(5):
            articles.append(
                self._article(
                    published_at=datetime(2026, 6, i + 1, 12, 0, tzinfo=timezone.utc),
                    source_common_name=f"src-{i}.com",
                )
            )
        clusters = _cluster_articles(articles, limit=3)
        self.assertEqual(len(clusters), 3)

    def test_unknown_source_or_date_bucket_does_not_crash(self) -> None:
        # Defensive — if the SQL layer ever returns NULL for source or
        # published_at the cluster still emits with 'unknown' bucket.
        articles = [
            self._article(source_common_name=None),
            self._article(published_at=None),
        ]
        clusters = _cluster_articles(articles, limit=10)
        # Both fall into the unknown source / unknown day buckets;
        # they may or may not collide depending on the other key.
        self.assertEqual(sum(c["article_count"] for c in clusters), 2)


class DaySortKeyTests(unittest.TestCase):
    def test_canonical_date_encodes(self) -> None:
        self.assertEqual(_day_sort_key("2026-06-09"), 20260609)

    def test_unknown_sorts_last(self) -> None:
        # -1 sentinel means "unknown" goes after every real date in DESC sort.
        self.assertEqual(_day_sort_key("unknown"), -1)


class FormatClustersHumanTests(unittest.TestCase):
    def test_empty_clusters_returns_help_message(self) -> None:
        output = _format_clusters_human([], summary={"asset": "BTC"})
        self.assertIn("No matching clusters", output)
        self.assertIn("genkei.ingest.gdelt", output)

    def test_renders_one_cluster_block_per_row(self) -> None:
        clusters = [
            {
                "day": "2026-06-09",
                "source": "nytimes.com",
                "article_count": 5,
                "mean_tone": -2.3,
                "matched_assets": ["AAPL"],
                "sample_urls": ["https://example.com/a", "https://example.com/b"],
            }
        ]
        output = _format_clusters_human(
            clusters, summary={"asset": None, "ticker": "AAPL"}
        )
        # Header reflects the filter summary.
        self.assertIn("ticker=AAPL", output)
        # Cluster row carries count + tone + matched assets.
        self.assertIn("articles=  5", output)
        self.assertIn("-2.30", output)
        self.assertIn("AAPL", output)
        # Sample URLs are indented + dash-prefixed.
        self.assertIn("    - https://example.com/a", output)
        # Horizon footer surfaces.
        self.assertIn("Horizon:", output)


class CmdInvocationTests(unittest.TestCase):
    """End-to-end CLI invocation with the DB layer mocked."""

    def setUp(self) -> None:
        # Articles fixture returned by the mocked _fetch_articles.
        self.fake_articles = [
            {
                "published_at": datetime(2026, 6, 9, 14, 0, tzinfo=timezone.utc),
                "source_common_name": "nytimes.com",
                "document_identifier": "https://example.com/a",
                "tone": Decimal("-3.0"),
                "matched_assets": ["AAPL"],
                "themes": ["ECON_STOCKMARKET"],
            },
            {
                "published_at": datetime(2026, 6, 9, 9, 0, tzinfo=timezone.utc),
                "source_common_name": "nytimes.com",
                "document_identifier": "https://example.com/b",
                "tone": Decimal("-1.0"),
                "matched_assets": ["AAPL"],
                "themes": ["EARNINGS"],
            },
        ]

    def _invoke(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["news", *args])
        return rc, out.getvalue(), err.getvalue()

    def test_json_payload_includes_summary_and_clusters(self) -> None:
        cfg = _watchlist_path(self)
        with patch(
            "genkei.cli.news._fetch_articles", return_value=self.fake_articles
        ):
            rc, out, _err = self._invoke(
                "--ticker",
                "AAPL",
                "--config",
                str(cfg),
                "--json",
            )
        self.assertEqual(rc, 0)
        payload = json_mod.loads(out)
        self.assertEqual(payload["horizon"], HORIZON)
        self.assertIn("summary", payload)
        self.assertIn("clusters", payload)
        self.assertEqual(payload["summary"]["ticker"], "AAPL")
        # Pool size reflects what the fetcher returned.
        self.assertEqual(payload["summary"]["pool_size"], 2)
        # One cluster (same date + source).
        self.assertEqual(len(payload["clusters"]), 1)
        cluster = payload["clusters"][0]
        self.assertEqual(cluster["article_count"], 2)
        self.assertEqual(cluster["matched_assets"], ["AAPL"])

    def test_since_after_until_rejected(self) -> None:
        with patch("genkei.cli.news._fetch_articles", return_value=[]):
            rc, _out, err = self._invoke(
                "--since", "2026-06-09", "--until", "2026-06-01"
            )
        self.assertNotEqual(rc, 0)
        clean_err = _strip_ansi(err)
        self.assertIn("Invalid value: --since must be on or before --until", clean_err)

    def test_tone_min_greater_than_tone_max_rejected(self) -> None:
        with patch("genkei.cli.news._fetch_articles", return_value=[]):
            rc, _out, err = self._invoke("--tone-min", "5", "--tone-max", "-5")
        self.assertNotEqual(rc, 0)
        clean_err = _strip_ansi(err)
        self.assertIn("Invalid value: --tone-min must be ≤ --tone-max", clean_err)


class PoolCapTests(unittest.TestCase):
    """Ensure the pool cap constant is a sensible default."""

    def test_pool_cap_at_least_60x_default_cluster_limit(self) -> None:
        # Default --limit is 30 clusters; the pool needs to be large
        # enough that the per-cluster article count is meaningful even
        # after sort+truncate.
        self.assertGreaterEqual(ARTICLE_POOL_CAP, 30 * 60)


if __name__ == "__main__":
    unittest.main()
