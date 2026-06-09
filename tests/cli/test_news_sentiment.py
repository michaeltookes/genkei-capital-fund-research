"""Unit tests for ``genkei news-sentiment`` (B-056).

End-to-end CLI invocation paths with the lake loaders mocked. Pure
correlation math lives in tests/experiments/test_news_sentiment.py.
"""

from __future__ import annotations

import io
import json as json_mod
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from genkei.cli import main
from genkei.cli.news_sentiment import _resolve_asset_or_exit
from genkei.experiments.news_sentiment import (
    MIN_OBSERVATIONS_FOR_SIGNAL,
    ArticleRow,
    ReturnPoint,
)

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


def _watchlist_path(case: unittest.TestCase) -> Path:
    ctx = TemporaryDirectory()
    case.addCleanup(ctx.cleanup)
    tmp = Path(ctx.name)
    path = tmp / "watchlists.yml"
    path.write_text(WATCHLIST_YAML, encoding="utf-8")
    return path


class ResolveAssetOrExitTests(unittest.TestCase):
    def test_equity_returns_uppercase_ticker_and_equity_class(self) -> None:
        cfg = _watchlist_path(self)
        label, asset_class, coingecko_id = _resolve_asset_or_exit(
            ticker="aapl", asset=None, config=cfg
        )
        self.assertEqual(label, "AAPL")
        self.assertEqual(asset_class, "equity")
        self.assertIsNone(coingecko_id)

    def test_crypto_returns_uppercase_symbol_and_coingecko_id(self) -> None:
        cfg = _watchlist_path(self)
        label, asset_class, coingecko_id = _resolve_asset_or_exit(
            ticker=None, asset="btc", config=cfg
        )
        self.assertEqual(label, "BTC")
        self.assertEqual(asset_class, "crypto")
        self.assertEqual(coingecko_id, "bitcoin")

    def test_neither_passed_fails_loud(self) -> None:
        import typer

        cfg = _watchlist_path(self)
        with self.assertRaises(typer.BadParameter):
            _resolve_asset_or_exit(ticker=None, asset=None, config=cfg)

    def test_both_passed_fails_loud(self) -> None:
        import typer

        cfg = _watchlist_path(self)
        with self.assertRaises(typer.BadParameter):
            _resolve_asset_or_exit(ticker="AAPL", asset="BTC", config=cfg)


class CmdInvocationTests(unittest.TestCase):
    """End-to-end CLI paths with the lake-loaders mocked."""

    def _invoke(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["news-sentiment", *args])
        return rc, out.getvalue(), err.getvalue()

    _BASE_DAY = date(2026, 6, 1)

    def _day(self, i: int) -> date:
        return self._BASE_DAY + timedelta(days=i)

    def _make_articles(self, n: int) -> list[ArticleRow]:
        # Synthetic articles tone = +i over n consecutive days.
        return [
            ArticleRow(
                published_at=datetime.combine(
                    self._day(i), datetime.min.time(), tzinfo=timezone.utc
                ).replace(hour=12),
                asset="BTC",
                tone=Decimal(str(float(i))),
                positive_score=None,
                negative_score=None,
            )
            for i in range(n)
            # Repeat each day three times so the min_articles_per_day=3
            # floor doesn't drop everything.
            for _ in range(3)
        ]

    def _make_returns(self, n: int) -> list[ReturnPoint]:
        return [
            ReturnPoint(
                ts=self._day(i),
                asset="BTC",
                close=100.0 + i,
                # First day return is None (no prior close), subsequent
                # days mirror the sentiment so correlation is positive.
                pct_return=None if i == 0 else 0.1 * i,
            )
            for i in range(n + 1)  # +1 to provide the day-N+1 horizon row
        ]

    def test_insufficient_data_path_renders_help_hint(self) -> None:
        cfg = _watchlist_path(self)
        # Only 5 days of data → well below MIN_OBSERVATIONS_FOR_SIGNAL.
        with (
            patch(
                "genkei.cli.news_sentiment.load_articles_for_asset",
                return_value=self._make_articles(5),
            ),
            patch(
                "genkei.cli.news_sentiment.load_price_returns",
                return_value=self._make_returns(5),
            ),
        ):
            rc, out, err = self._invoke("--asset", "BTC", "--config", str(cfg))
        self.assertEqual(rc, 0)
        self.assertIn("status=insufficient_data", out)
        # Backfill hint surfaces — the agent needs to know what to do.
        self.assertIn("genkei.ingest.gdelt --backfill", out)

    def test_ok_status_renders_pearson_spearman_quartiles(self) -> None:
        cfg = _watchlist_path(self)
        # Enough days to clear the floor.
        n = MIN_OBSERVATIONS_FOR_SIGNAL + 5
        with (
            patch(
                "genkei.cli.news_sentiment.load_articles_for_asset",
                return_value=self._make_articles(n),
            ),
            patch(
                "genkei.cli.news_sentiment.load_price_returns",
                return_value=self._make_returns(n),
            ),
        ):
            rc, out, _err = self._invoke("--asset", "BTC", "--config", str(cfg))
        self.assertEqual(rc, 0)
        self.assertIn("Pearson", out)
        self.assertIn("Spearman", out)
        self.assertIn("Per tone-quartile mean forward return", out)
        # Each quartile labeled Q1..Q4.
        for label in ("Q1", "Q2", "Q3", "Q4"):
            self.assertIn(label, out)

    def test_json_payload_shape(self) -> None:
        cfg = _watchlist_path(self)
        n = MIN_OBSERVATIONS_FOR_SIGNAL + 1
        with (
            patch(
                "genkei.cli.news_sentiment.load_articles_for_asset",
                return_value=self._make_articles(n),
            ),
            patch(
                "genkei.cli.news_sentiment.load_price_returns",
                return_value=self._make_returns(n),
            ),
        ):
            rc, out, _err = self._invoke(
                "--asset", "BTC", "--json", "--config", str(cfg)
            )
        self.assertEqual(rc, 0)
        payload = json_mod.loads(out)
        self.assertEqual(payload["asset"], "BTC")
        self.assertEqual(payload["asset_class"], "crypto")
        self.assertEqual(payload["status"], "ok")
        self.assertIsNotNone(payload["pearson"])
        self.assertIsNotNone(payload["spearman"])
        self.assertEqual(len(payload["quartiles"]), 4)
        # Quartile dict shape is stable.
        q1 = payload["quartiles"][0]
        for key in ("quartile", "n", "mean_tone", "mean_forward_return_pct"):
            self.assertIn(key, q1)

    def test_since_after_until_rejected(self) -> None:
        cfg = _watchlist_path(self)
        with (
            patch("genkei.cli.news_sentiment.load_articles_for_asset", return_value=[]),
            patch("genkei.cli.news_sentiment.load_price_returns", return_value=[]),
        ):
            rc, _out, err = self._invoke(
                "--asset",
                "BTC",
                "--since",
                "2026-06-10",
                "--until",
                "2026-06-01",
                "--config",
                str(cfg),
            )
        self.assertNotEqual(rc, 0)
        self.assertIn("--since must be on or before --until", err)


if __name__ == "__main__":
    unittest.main()
