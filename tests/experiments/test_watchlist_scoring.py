"""Unit tests for the watchlist scoring rubric (B-065).

Tests are scoped to the pure scoring functions and mocked DB loaders.
The compose+persist pipeline is exercised at the CLI integration layer
via test_watchlist_cmd with mocked DB calls.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from genkei.experiments.watchlist_scoring import (
    RUBRIC_VERSION,
    ComponentScore,
    _daily_score_ts,
    _load_crypto_signals,
    _load_macro_inputs,
    compose_crypto_score,
    compose_equity_score,
    compute_today,
    score_filings_velocity,
    score_insider_flow,
    score_macro_regime,
    score_relative_strength,
    score_revenue_trend,
    score_tvl_trend,
    score_volume_momentum,
)
from genkei.common.watchlist import EquityEntry, Watchlist


class InsiderFlowTests(unittest.TestCase):
    def test_buy_cluster_three_reporters_scores_plus_three(self) -> None:
        c = score_insider_flow(
            buy_cluster_reporters_30d=3,
            sell_cluster_reporters_30d=0,
            any_open_market_buy_14d=True,
        )
        self.assertEqual(c.score, 3)
        self.assertIn("buy cluster", c.detail)

    def test_buy_cluster_overrides_sell_cluster(self) -> None:
        # Rare but possible — both clusters in the same 30d window;
        # buy wins because the rubric is bull-leaning on clusters.
        c = score_insider_flow(
            buy_cluster_reporters_30d=4,
            sell_cluster_reporters_30d=5,
            any_open_market_buy_14d=False,
        )
        self.assertEqual(c.score, 3)
        self.assertIn("buy cluster", c.detail)

    def test_sell_cluster_three_reporters_scores_minus_two(self) -> None:
        c = score_insider_flow(
            buy_cluster_reporters_30d=0,
            sell_cluster_reporters_30d=4,
            any_open_market_buy_14d=False,
        )
        self.assertEqual(c.score, -2)
        self.assertIn("sell cluster", c.detail)

    def test_open_market_buy_only_scores_plus_one(self) -> None:
        c = score_insider_flow(
            buy_cluster_reporters_30d=1,
            sell_cluster_reporters_30d=0,
            any_open_market_buy_14d=True,
        )
        self.assertEqual(c.score, 1)
        self.assertIn("14d", c.detail)

    def test_no_signal_scores_zero(self) -> None:
        c = score_insider_flow(
            buy_cluster_reporters_30d=0,
            sell_cluster_reporters_30d=0,
            any_open_market_buy_14d=False,
        )
        self.assertEqual(c.score, 0)
        self.assertEqual(c.detail, "no signal")


class RevenueTrendTests(unittest.TestCase):
    def test_strong_growth_scores_plus_two(self) -> None:
        c = score_revenue_trend(revenue_yoy_pct=Decimal("18.5"))
        self.assertEqual(c.score, 2)
        self.assertIn("18.5%", c.detail)

    def test_moderate_growth_scores_plus_one(self) -> None:
        c = score_revenue_trend(revenue_yoy_pct=Decimal("8"))
        self.assertEqual(c.score, 1)

    def test_decline_scores_minus_one(self) -> None:
        c = score_revenue_trend(revenue_yoy_pct=Decimal("-5"))
        self.assertEqual(c.score, -1)

    def test_steep_decline_scores_minus_two(self) -> None:
        c = score_revenue_trend(revenue_yoy_pct=Decimal("-15"))
        self.assertEqual(c.score, -2)

    def test_flat_growth_scores_zero(self) -> None:
        c = score_revenue_trend(revenue_yoy_pct=Decimal("2"))
        self.assertEqual(c.score, 0)
        self.assertIn("flat", c.detail)

    def test_missing_data_scores_zero(self) -> None:
        c = score_revenue_trend(revenue_yoy_pct=None)
        self.assertEqual(c.score, 0)
        self.assertIn("no recent XBRL", c.detail)


class FilingsVelocityTests(unittest.TestCase):
    def test_normal_cadence_scores_plus_one(self) -> None:
        c = score_filings_velocity(eight_k_count_30d=2)
        self.assertEqual(c.score, 1)
        self.assertIn("normal cadence", c.detail)

    def test_high_filings_scores_minus_two(self) -> None:
        c = score_filings_velocity(eight_k_count_30d=7)
        self.assertEqual(c.score, -2)
        self.assertIn("investigate", c.detail)

    def test_no_filings_scores_zero(self) -> None:
        c = score_filings_velocity(eight_k_count_30d=0)
        self.assertEqual(c.score, 0)


class RelativeStrengthTests(unittest.TestCase):
    def test_strong_outperformance(self) -> None:
        c = score_relative_strength(rel_strength_pct=Decimal("15"))
        self.assertEqual(c.score, 2)

    def test_moderate_outperformance(self) -> None:
        c = score_relative_strength(rel_strength_pct=Decimal("5"))
        self.assertEqual(c.score, 1)

    def test_flat_within_3pp(self) -> None:
        c = score_relative_strength(rel_strength_pct=Decimal("1.5"))
        self.assertEqual(c.score, 0)

    def test_moderate_underperformance(self) -> None:
        c = score_relative_strength(rel_strength_pct=Decimal("-5"))
        self.assertEqual(c.score, -1)

    def test_strong_underperformance_like_sui_session(self) -> None:
        # SUI was -22.8pp vs SOL @ 365d in the 2026-05-20 session.
        # At 30d (this rubric's window) it was actually +15.6pp, but
        # this test pins the -22.8 case symmetrically.
        c = score_relative_strength(rel_strength_pct=Decimal("-22.8"))
        self.assertEqual(c.score, -2)

    def test_no_data_scores_zero(self) -> None:
        c = score_relative_strength(rel_strength_pct=None)
        self.assertEqual(c.score, 0)


class TvlTrendTests(unittest.TestCase):
    def test_strong_growth(self) -> None:
        c = score_tvl_trend(tvl_change_30d_pct=Decimal("20"))
        self.assertEqual(c.score, 2)

    def test_steep_decline(self) -> None:
        c = score_tvl_trend(tvl_change_30d_pct=Decimal("-25"))
        self.assertEqual(c.score, -2)

    def test_no_coverage(self) -> None:
        c = score_tvl_trend(tvl_change_30d_pct=None)
        self.assertEqual(c.score, 0)


class VolumeMomentumTests(unittest.TestCase):
    def test_surge(self) -> None:
        c = score_volume_momentum(
            volume_7d_avg=Decimal("500_000_000"),
            volume_30d_avg=Decimal("300_000_000"),
        )
        self.assertEqual(c.score, 1)
        self.assertIn("surge", c.detail)

    def test_fade(self) -> None:
        c = score_volume_momentum(
            volume_7d_avg=Decimal("100_000_000"),
            volume_30d_avg=Decimal("250_000_000"),
        )
        self.assertEqual(c.score, -1)
        self.assertIn("fade", c.detail)

    def test_flat(self) -> None:
        c = score_volume_momentum(
            volume_7d_avg=Decimal("100"),
            volume_30d_avg=Decimal("100"),
        )
        self.assertEqual(c.score, 0)

    def test_missing_30d_yields_zero_no_division_error(self) -> None:
        c = score_volume_momentum(
            volume_7d_avg=Decimal("100"),
            volume_30d_avg=None,
        )
        self.assertEqual(c.score, 0)

    def test_zero_30d_yields_zero_no_division_error(self) -> None:
        c = score_volume_momentum(
            volume_7d_avg=Decimal("100"),
            volume_30d_avg=Decimal("0"),
        )
        self.assertEqual(c.score, 0)


class MacroRegimeTests(unittest.TestCase):
    def test_risk_on_when_three_inputs_aligned(self) -> None:
        # HY tight + VIX benign + USD weakening = +3 total → risk-on.
        c = score_macro_regime(
            dgs10_pct=None,
            dgs10_pct_30d_ago=None,
            hy_oas_pct=Decimal("2.8"),
            vix=Decimal("15"),
            usd_index=Decimal("117"),
            usd_index_30d_ago=Decimal("119"),
        )
        self.assertEqual(c.score, 1)
        self.assertIn("risk-on", c.detail)

    def test_risk_off_when_inputs_misaligned(self) -> None:
        # Rate spike + HY wide + VIX elevated = -3 → risk-off.
        c = score_macro_regime(
            dgs10_pct=Decimal("5.2"),
            dgs10_pct_30d_ago=Decimal("4.7"),
            hy_oas_pct=Decimal("5.5"),
            vix=Decimal("30"),
            usd_index=None,
            usd_index_30d_ago=None,
        )
        self.assertEqual(c.score, -1)
        self.assertIn("risk-off", c.detail)

    def test_mixed_inputs_score_zero(self) -> None:
        # HY tight (+1) + DGS10 rising (-1) = mixed.
        c = score_macro_regime(
            dgs10_pct=Decimal("5.0"),
            dgs10_pct_30d_ago=Decimal("4.5"),
            hy_oas_pct=Decimal("2.5"),
            vix=Decimal("20"),
            usd_index=Decimal("118"),
            usd_index_30d_ago=Decimal("118.5"),
        )
        self.assertEqual(c.score, 0)
        self.assertIn("mixed", c.detail)

    def test_no_inputs_scores_zero(self) -> None:
        c = score_macro_regime(
            dgs10_pct=None, dgs10_pct_30d_ago=None,
            hy_oas_pct=None, vix=None,
            usd_index=None, usd_index_30d_ago=None,
        )
        self.assertEqual(c.score, 0)
        self.assertIn("no recent macro", c.detail)


class CompositeScoreTests(unittest.TestCase):
    def test_equity_composite_sums_four_components(self) -> None:
        score = compose_equity_score(
            ticker="AAPL",
            sleeve="equity-core",
            insider=ComponentScore("insider_flow", 3, "buy cluster"),
            revenue=ComponentScore("revenue_trend", 2, "YoY +18%"),
            filings=ComponentScore("filings_velocity", 1, "1 8-K"),
            macro=ComponentScore("macro_regime", 1, "risk-on"),
        )
        self.assertEqual(score.asset, "AAPL")
        self.assertEqual(score.asset_class, "equity")
        self.assertEqual(score.composite_score, 7)
        self.assertEqual(len(score.components), 4)

    def test_crypto_composite_sums_four_components(self) -> None:
        score = compose_crypto_score(
            coingecko_id="sui",
            sleeve="crypto-tactical",
            rel_strength=ComponentScore("relative_strength", -2, "-22pp vs BTC"),
            tvl=ComponentScore("tvl_trend", -2, "TVL -25%"),
            volume=ComponentScore("volume_momentum", -1, "vol fade"),
            macro=ComponentScore("macro_regime", 1, "risk-on"),
        )
        self.assertEqual(score.asset, "sui")
        self.assertEqual(score.asset_class, "crypto")
        self.assertEqual(score.composite_score, -4)
        self.assertEqual(score.sleeve, "crypto-tactical")

    def test_meta_signal_row_shape(self) -> None:
        score = compose_equity_score(
            ticker="MSFT",
            sleeve="equity-core",
            insider=ComponentScore("insider_flow", 0, "no signal"),
            revenue=ComponentScore("revenue_trend", 2, "YoY +12%"),
            filings=ComponentScore("filings_velocity", 1, "normal cadence"),
            macro=ComponentScore("macro_regime", 0, "mixed"),
        )
        row = score.to_meta_signal_row(ingest_run_id=42)
        self.assertEqual(row["asset"], "MSFT")
        self.assertEqual(row["rubric_version"], RUBRIC_VERSION)
        self.assertEqual(row["composite_score"], 3)
        self.assertEqual(row["ingest_run_id"], 42)
        # `components` is a dict-of-dicts keyed by component name.
        self.assertEqual(set(row["components"].keys()), {
            "insider_flow", "revenue_trend", "filings_velocity", "macro_regime",
        })
        self.assertEqual(row["components"]["revenue_trend"]["score"], 2)


class RubricVersionTests(unittest.TestCase):
    """Pin RUBRIC_VERSION so a silent change forces a deliberate bump."""

    def test_rubric_version_is_v1(self) -> None:
        self.assertEqual(RUBRIC_VERSION, "v1")


class DailyTimestampTests(unittest.TestCase):
    def test_daily_score_ts_buckets_same_utc_day(self) -> None:
        ts = datetime(2026, 5, 22, 15, 45, 30, 123456, tzinfo=timezone.utc)

        self.assertEqual(
            _daily_score_ts(ts),
            datetime(2026, 5, 22, tzinfo=timezone.utc),
        )

    def test_compute_today_uses_daily_score_ts_but_live_loader_ts(self) -> None:
        run_ts = datetime(2026, 5, 22, 15, 45, tzinfo=timezone.utc)
        watchlist = Watchlist(
            crypto=[],
            equities=[
                EquityEntry(
                    symbol="AAPL",
                    name="Apple Inc.",
                    cik="0000320193",
                    tier="primary",
                    sleeve="core",
                )
            ],
            macro=[],
            protocols=[],
        )

        with (
            patch(
                "genkei.experiments.watchlist_scoring._load_macro_inputs",
                return_value={
                    "dgs10_pct": None,
                    "dgs10_pct_30d_ago": None,
                    "hy_oas_pct": None,
                    "vix": None,
                    "usd_index": None,
                    "usd_index_30d_ago": None,
                },
            ) as macro_loader,
            patch(
                "genkei.experiments.watchlist_scoring._load_equity_signals",
                return_value={
                    "buy_cluster_reporters_30d": 0,
                    "sell_cluster_reporters_30d": 0,
                    "any_open_market_buy_14d": False,
                    "revenue_yoy_pct": None,
                    "eight_k_count_30d": 0,
                },
            ) as equity_loader,
        ):
            scores = compute_today(watchlist=watchlist, ts=run_ts)

        self.assertEqual(scores[0].ts, datetime(2026, 5, 22, tzinfo=timezone.utc))
        macro_loader.assert_called_once_with(run_ts)
        equity_loader.assert_called_once_with("AAPL", "0000320193", run_ts)


class LakeLoaderTests(unittest.TestCase):
    def test_load_macro_inputs_fetches_full_anchor_window(self) -> None:
        ts = datetime(2026, 5, 22, tzinfo=timezone.utc)
        captured_sql: list[str] = []

        class FakeCursor:
            def __init__(self) -> None:
                self.series_id = ""

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=None):
                captured_sql.append(sql)
                self.series_id = params[0]

            def fetchall(self):
                if self.series_id == "DGS10":
                    return [
                        (ts - timedelta(days=1), Decimal("4.8")),
                        (ts - timedelta(days=10), Decimal("4.7")),
                        (ts - timedelta(days=31), Decimal("4.2")),
                    ]
                if self.series_id == "DTWEXBGS":
                    return [
                        (ts - timedelta(days=1), Decimal("118")),
                        (ts - timedelta(days=31), Decimal("116")),
                    ]
                return [(ts - timedelta(days=1), Decimal("3.0"))]

        class FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return FakeCursor()

        with patch(
            "genkei.experiments.watchlist_scoring.db.connection",
            return_value=FakeConn(),
        ):
            inputs = _load_macro_inputs(ts)

        self.assertTrue(captured_sql)
        self.assertNotIn("LIMIT 2", "\n".join(captured_sql))
        self.assertEqual(inputs["dgs10_pct"], Decimal("4.8"))
        self.assertEqual(inputs["dgs10_pct_30d_ago"], Decimal("4.2"))
        self.assertEqual(inputs["usd_index_30d_ago"], Decimal("116"))

    def test_load_crypto_signals_uses_protocol_tvl_fallback(self) -> None:
        ts = datetime(2026, 5, 22, tzinfo=timezone.utc)
        captured_params: list[object] = []

        class FakeCursor:
            def __init__(self) -> None:
                self.query = ""

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=None):
                self.query = sql
                captured_params.append(params)

            def fetchone(self):
                if "analytics.crypto_relative_strength" in self.query:
                    return [None]
                if "defillama.protocol_tvl" in self.query:
                    return [Decimal("120"), Decimal("100")]
                if "coingecko.market_data" in self.query:
                    return [None, None]
                return None

        class FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return FakeCursor()

        with patch(
            "genkei.experiments.watchlist_scoring.db.connection",
            return_value=FakeConn(),
        ):
            inputs = _load_crypto_signals(
                "chainlink",
                ts,
                protocol_slugs=["chainlink", "chainlink-ccip"],
            )

        self.assertEqual(inputs["tvl_change_30d_pct"], Decimal("20.0"))
        self.assertIn(["chainlink", "chainlink-ccip"], captured_params[1])
