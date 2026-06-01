"""Unit tests for the stack-outcome backtest (B-101).

Tests are scoped to the pure functions (compute_stack_returns,
compute_baseline, aggregate_stack_returns, stratifiers, and the
orchestrator with mocked DB calls). The lake-bound run_backtest is
exercised with mocked query_events + load_price_series.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from genkei.experiments.eight_k_impact import PricePoint
from genkei.experiments.signal_store import SignalEvent, Stack
from genkei.experiments.stack_backtest import (
    BASELINE_SAMPLE_STEP_DAYS,
    STACK_WINDOWS,
    BaselineStats,
    StackReturns,
    _hit_rate,
    _mean,
    _median,
    aggregate_stack_returns,
    compute_baseline,
    compute_stack_returns,
    run_backtest,
    stratify_by_asset,
    stratify_by_direction,
    stratify_by_rule,
)

EQUITY_CORE = "equity:core"


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def _event(
    *,
    asset: str = "AAPL",
    ts: datetime,
    source: str = "insider_clusters",
    signal_kind: str = "buy_cluster",
    direction: str = "bullish",
) -> SignalEvent:
    return SignalEvent(
        asset=asset,
        asset_class="equity",
        horizon=EQUITY_CORE,
        ts=ts,
        source=source,
        signal_kind=signal_kind,
        direction=direction,
        strength=Decimal("1.0"),
        payload={},
        source_ref=f"{asset}:{ts.date().isoformat()}",
    )


_DEFAULT_WINDOW_END = _utc(2024, 1, 15)


def _stack(
    *,
    rule_name: str = "smart_money_buy",
    asset: str = "AAPL",
    direction: str = "bullish",
    window_end: datetime = _DEFAULT_WINDOW_END,
    window_start: datetime | None = None,
    score: Decimal = Decimal("1.5"),
    distinct_sources: int = 2,
    event_count: int = 2,
    events: list[SignalEvent] | None = None,
) -> Stack:
    return Stack(
        rule_name=rule_name,
        asset=asset,
        asset_class="equity",
        direction=direction,
        window_start=window_start or window_end - timedelta(days=7),
        window_end=window_end,
        score=score,
        distinct_sources=distinct_sources,
        event_count=event_count,
        horizon=EQUITY_CORE,
        events=events or [],
    )


def _linear_price_series(
    *,
    start: date,
    days: int,
    start_price: Decimal = Decimal("100"),
    daily_step: Decimal = Decimal("1"),
) -> list[PricePoint]:
    """Daily price series stepping by ``daily_step`` per calendar day."""
    return [
        PricePoint(
            ts=start + timedelta(days=i),
            adj_close=start_price + daily_step * Decimal(i),
        )
        for i in range(days)
    ]


def _flat_price_series(
    *, start: date, days: int, price: Decimal = Decimal("100")
) -> list[PricePoint]:
    return [
        PricePoint(ts=start + timedelta(days=i), adj_close=price)
        for i in range(days)
    ]


class PureHelpersTests(unittest.TestCase):
    def test_mean_empty_is_none(self) -> None:
        self.assertIsNone(_mean([]))

    def test_mean_basic(self) -> None:
        self.assertEqual(_mean([Decimal("1"), Decimal("2"), Decimal("3")]), Decimal("2"))

    def test_median_odd_count(self) -> None:
        self.assertEqual(
            _median([Decimal("1"), Decimal("5"), Decimal("3")]), Decimal("3")
        )

    def test_median_even_count_averages(self) -> None:
        self.assertEqual(
            _median([Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")]),
            Decimal("2.5"),
        )

    def test_hit_rate_empty_is_none(self) -> None:
        self.assertIsNone(_hit_rate([]))

    def test_hit_rate_counts_strict_positives(self) -> None:
        vals = [Decimal("1"), Decimal("0"), Decimal("-1"), Decimal("2")]
        # 2 of 4 strictly positive → 50.0%.
        self.assertEqual(_hit_rate(vals), Decimal("50"))


class ComputeStackReturnsTests(unittest.TestCase):
    def test_climbing_series_yields_positive_returns(self) -> None:
        # 100 → 101 → 102 ... so any forward window returns positive.
        prices = _linear_price_series(start=date(2024, 1, 1), days=400)
        stack = _stack(window_end=_utc(2024, 1, 15))
        out = compute_stack_returns([stack], {"AAPL": prices})
        self.assertEqual(len(out), 1)
        post_5d = out[0].windows["post_5d"]
        self.assertIsNotNone(post_5d)
        # 5 cal days @ +1/day on a base of ~114 = +5/114 ≈ 4.39%
        assert post_5d is not None
        self.assertGreater(post_5d, Decimal("4"))
        self.assertLess(post_5d, Decimal("5"))

    def test_flat_series_yields_zero(self) -> None:
        prices = _flat_price_series(start=date(2024, 1, 1), days=400)
        stack = _stack(window_end=_utc(2024, 1, 15))
        out = compute_stack_returns([stack], {"AAPL": prices})
        for label in ("post_5d", "post_30d", "post_90d"):
            self.assertEqual(out[0].windows[label], Decimal("0"))

    def test_missing_asset_yields_all_null_windows(self) -> None:
        # No prices for the stack's asset → every window is None.
        stack = _stack(asset="UNKNOWN", window_end=_utc(2024, 1, 15))
        out = compute_stack_returns([stack], {})
        for label, _, _ in STACK_WINDOWS:
            self.assertIsNone(out[0].windows[label])

    def test_recent_stack_yields_null_for_unreachable_windows(self) -> None:
        # Stack at day 100 of a 200-day series: post_5d / post_30d work
        # (still inside the series), but post_180d / post_365d extend
        # past the end and resolve to None.
        prices = _linear_price_series(start=date(2024, 1, 1), days=200)
        stack = _stack(window_end=_utc(2024, 4, 10))  # ~day 100
        out = compute_stack_returns([stack], {"AAPL": prices})
        self.assertIsNotNone(out[0].windows["post_5d"])
        self.assertIsNotNone(out[0].windows["post_30d"])
        self.assertIsNone(out[0].windows["post_180d"])
        self.assertIsNone(out[0].windows["post_365d"])

    def test_empty_stack_list_yields_empty_result(self) -> None:
        self.assertEqual(compute_stack_returns([], {}), [])

    def test_crowding_stack_return_starts_after_13f_lag(self) -> None:
        prices = [
            PricePoint(ts=date(2024, 3, 31), adj_close=Decimal("100")),
            PricePoint(ts=date(2024, 4, 5), adj_close=Decimal("200")),
            PricePoint(ts=date(2024, 5, 15), adj_close=Decimal("100")),
            PricePoint(ts=date(2024, 5, 20), adj_close=Decimal("110")),
        ]
        stack = _stack(
            window_start=_utc(2024, 3, 30),
            window_end=_utc(2024, 3, 31),
            events=[
                _event(
                    asset="AAPL",
                    ts=_utc(2024, 3, 30),
                    source="insider_clusters",
                    signal_kind="buy_cluster",
                ),
                _event(
                    asset="AAPL",
                    ts=_utc(2024, 3, 31),
                    source="crowding",
                    signal_kind="crowding_add",
                ),
            ],
        )

        out = compute_stack_returns(
            [stack],
            {"AAPL": prices},
            windows=(("post_5d", 0, 5),),
        )

        self.assertEqual(out[0].windows["post_5d"], Decimal("10"))

    def test_insider_cluster_stack_return_starts_after_form4_lag(self) -> None:
        prices = [
            PricePoint(ts=date(2024, 1, 5), adj_close=Decimal("100")),
            PricePoint(ts=date(2024, 1, 9), adj_close=Decimal("100")),
            PricePoint(ts=date(2024, 1, 10), adj_close=Decimal("200")),
            PricePoint(ts=date(2024, 1, 14), adj_close=Decimal("110")),
        ]
        stack = _stack(
            window_start=_utc(2024, 1, 5),
            window_end=_utc(2024, 1, 5),
            events=[
                _event(
                    asset="AAPL",
                    ts=_utc(2024, 1, 5),
                    source="insider_clusters",
                    signal_kind="buy_cluster",
                ),
            ],
        )

        out = compute_stack_returns(
            [stack],
            {"AAPL": prices},
            windows=(("post_5d", 0, 5),),
        )

        self.assertEqual(out[0].windows["post_5d"], Decimal("10"))


class ComputeBaselineTests(unittest.TestCase):
    def test_empty_prices_yields_null_baseline(self) -> None:
        b = compute_baseline("AAPL", [])
        self.assertEqual(b.asset, "AAPL")
        self.assertEqual(b.n_baseline_samples, 0)
        for label, _, _ in STACK_WINDOWS:
            self.assertIsNone(b.mean_pct[label])

    def test_baseline_is_positive_on_climbing_series(self) -> None:
        prices = _linear_price_series(start=date(2020, 1, 1), days=1000)
        b = compute_baseline("AAPL", prices)
        # Every forward window is positive on a monotonic climber.
        for label, _, _ in STACK_WINDOWS:
            mean = b.mean_pct[label]
            if mean is not None:
                self.assertGreater(mean, Decimal("0"), f"baseline {label} not > 0")
        # Hit rate should be 100% (everything positive).
        self.assertEqual(b.hit_rate_pct["post_5d"], Decimal("100"))

    def test_baseline_is_zero_on_flat_series(self) -> None:
        prices = _flat_price_series(start=date(2020, 1, 1), days=1000)
        b = compute_baseline("AAPL", prices)
        for label, _, _ in STACK_WINDOWS:
            mean = b.mean_pct[label]
            if mean is not None:
                self.assertEqual(mean, Decimal("0"))

    def test_baseline_sample_count_matches_step(self) -> None:
        # 1000 days / 7-day step ≈ 143 samples + 1.
        prices = _flat_price_series(start=date(2020, 1, 1), days=1000)
        b = compute_baseline("AAPL", prices, sample_step_days=BASELINE_SAMPLE_STEP_DAYS)
        # Allow off-by-one; sampling steps from prices[0].ts to prices[-1].ts.
        self.assertGreaterEqual(b.n_baseline_samples, 142)
        self.assertLessEqual(b.n_baseline_samples, 144)

    def test_non_positive_sample_step_rejected(self) -> None:
        prices = _flat_price_series(start=date(2020, 1, 1), days=10)
        with self.assertRaisesRegex(ValueError, "sample_step_days"):
            compute_baseline("AAPL", prices, sample_step_days=0)


class AggregateStackReturnsTests(unittest.TestCase):
    def test_empty_input_yields_zero_stack_stratum(self) -> None:
        out = aggregate_stack_returns([], {})
        self.assertEqual(out.n_stacks, 0)
        for label, _, _ in STACK_WINDOWS:
            self.assertEqual(out.n_evaluable[label], 0)
            self.assertIsNone(out.mean_pct[label])

    def test_mean_and_median_basic(self) -> None:
        stack_returns = [
            StackReturns(
                stack=_stack(),
                windows={"post_5d": Decimal("1"), "post_30d": Decimal("2")},
            ),
            StackReturns(
                stack=_stack(),
                windows={"post_5d": Decimal("3"), "post_30d": Decimal("4")},
            ),
            StackReturns(
                stack=_stack(),
                windows={"post_5d": Decimal("5"), "post_30d": Decimal("6")},
            ),
        ]
        # No baselines passed → mean_excess_pct should be None.
        stats = aggregate_stack_returns(
            stack_returns,
            {},
            windows=(("post_5d", 0, 5), ("post_30d", 0, 30)),
        )
        self.assertEqual(stats.n_stacks, 3)
        self.assertEqual(stats.horizons, frozenset({EQUITY_CORE}))
        self.assertEqual(stats.n_evaluable["post_5d"], 3)
        self.assertEqual(stats.mean_pct["post_5d"], Decimal("3"))
        self.assertEqual(stats.median_pct["post_5d"], Decimal("3"))
        self.assertEqual(stats.hit_rate_pct["post_5d"], Decimal("100"))
        self.assertIsNone(stats.mean_excess_pct["post_5d"])

    def test_excess_vs_baseline(self) -> None:
        # Three AAPL stacks with mean post_5d = 3.0; AAPL baseline = 1.0.
        # Excess = 3.0 - 1.0 = +2.0pp (bullish-stack win on AAPL).
        stack_returns = [
            StackReturns(stack=_stack(asset="AAPL"), windows={"post_5d": Decimal("3")}),
            StackReturns(stack=_stack(asset="AAPL"), windows={"post_5d": Decimal("3")}),
            StackReturns(stack=_stack(asset="AAPL"), windows={"post_5d": Decimal("3")}),
        ]
        baselines = {
            "AAPL": BaselineStats(
                asset="AAPL",
                n_baseline_samples=100,
                mean_pct={"post_5d": Decimal("1")},
                hit_rate_pct={"post_5d": Decimal("55")},
            )
        }
        stats = aggregate_stack_returns(
            stack_returns, baselines, windows=(("post_5d", 0, 5),)
        )
        self.assertEqual(stats.mean_excess_pct["post_5d"], Decimal("2"))

    def test_excess_is_asset_weighted(self) -> None:
        # AAPL contributes 3 stacks at 5.0 pct each (baseline 1.0pp);
        # NVDA contributes 1 stack at 5.0 pct (baseline 4.0pp).
        # Asset-weighted baseline mean across the 4 stacks: (1+1+1+4)/4 = 1.75.
        # Stack mean = 5.0. Excess = 3.25.
        stack_returns = [
            StackReturns(stack=_stack(asset="AAPL"), windows={"post_5d": Decimal("5")}),
            StackReturns(stack=_stack(asset="AAPL"), windows={"post_5d": Decimal("5")}),
            StackReturns(stack=_stack(asset="AAPL"), windows={"post_5d": Decimal("5")}),
            StackReturns(stack=_stack(asset="NVDA"), windows={"post_5d": Decimal("5")}),
        ]
        baselines = {
            "AAPL": BaselineStats(
                asset="AAPL",
                n_baseline_samples=100,
                mean_pct={"post_5d": Decimal("1")},
                hit_rate_pct={"post_5d": Decimal("55")},
            ),
            "NVDA": BaselineStats(
                asset="NVDA",
                n_baseline_samples=100,
                mean_pct={"post_5d": Decimal("4")},
                hit_rate_pct={"post_5d": Decimal("55")},
            ),
        }
        stats = aggregate_stack_returns(
            stack_returns, baselines, windows=(("post_5d", 0, 5),)
        )
        self.assertEqual(stats.mean_excess_pct["post_5d"], Decimal("3.25"))

    def test_null_window_dropped_from_aggregate(self) -> None:
        # Mixed: one stack has post_30d, another doesn't.
        stack_returns = [
            StackReturns(
                stack=_stack(),
                windows={"post_5d": Decimal("2"), "post_30d": Decimal("4")},
            ),
            StackReturns(stack=_stack(), windows={"post_5d": Decimal("4"), "post_30d": None}),
        ]
        stats = aggregate_stack_returns(
            stack_returns,
            {},
            windows=(("post_5d", 0, 5), ("post_30d", 0, 30)),
        )
        self.assertEqual(stats.n_evaluable["post_5d"], 2)
        self.assertEqual(stats.n_evaluable["post_30d"], 1)
        self.assertEqual(stats.mean_pct["post_30d"], Decimal("4"))

    def test_baseline_missing_for_one_asset_still_computes_lift(self) -> None:
        # NVDA has no baseline; AAPL does. The asset-weighted mean uses
        # only the assets that DO have a baseline.
        stack_returns = [
            StackReturns(stack=_stack(asset="AAPL"), windows={"post_5d": Decimal("3")}),
            StackReturns(stack=_stack(asset="NVDA"), windows={"post_5d": Decimal("3")}),
        ]
        baselines = {
            "AAPL": BaselineStats(
                asset="AAPL",
                n_baseline_samples=100,
                mean_pct={"post_5d": Decimal("1")},
                hit_rate_pct={"post_5d": None},
            )
        }
        stats = aggregate_stack_returns(
            stack_returns, baselines, windows=(("post_5d", 0, 5),)
        )
        # Excess uses only AAPL's baseline mean (1.0); stack mean = 3.0; excess = 2.0.
        self.assertEqual(stats.mean_excess_pct["post_5d"], Decimal("2"))


class StratifierTests(unittest.TestCase):
    def _mixed_returns(self) -> list[StackReturns]:
        return [
            StackReturns(
                stack=_stack(rule_name="smart_money_buy", asset="AAPL", direction="bullish"),
                windows={"post_5d": Decimal("2")},
            ),
            StackReturns(
                stack=_stack(rule_name="smart_money_buy", asset="NVDA", direction="bullish"),
                windows={"post_5d": Decimal("4")},
            ),
            StackReturns(
                stack=_stack(rule_name="broad_exit", asset="AAPL", direction="bearish"),
                windows={"post_5d": Decimal("-3")},
            ),
        ]

    def test_stratify_by_rule(self) -> None:
        out = stratify_by_rule(self._mixed_returns(), {}, windows=(("post_5d", 0, 5),))
        keys = [s.stratum_key for s in out]
        self.assertEqual(sorted(keys), ["broad_exit", "smart_money_buy"])
        rule_to_mean = {s.stratum_key: s.mean_pct["post_5d"] for s in out}
        self.assertEqual(rule_to_mean["smart_money_buy"], Decimal("3"))
        self.assertEqual(rule_to_mean["broad_exit"], Decimal("-3"))

    def test_stratify_by_direction(self) -> None:
        out = stratify_by_direction(
            self._mixed_returns(), {}, windows=(("post_5d", 0, 5),)
        )
        keys = sorted(s.stratum_key for s in out)
        self.assertEqual(keys, ["bearish", "bullish"])

    def test_stratify_by_asset(self) -> None:
        out = stratify_by_asset(self._mixed_returns(), {}, windows=(("post_5d", 0, 5),))
        keys = sorted(s.stratum_key for s in out)
        self.assertEqual(keys, ["AAPL", "NVDA"])
        aapl = next(s for s in out if s.stratum_key == "AAPL")
        # AAPL contributed 2 stacks (one bull at +2, one bear at -3); mean = -0.5.
        self.assertEqual(aapl.n_stacks, 2)
        self.assertEqual(aapl.mean_pct["post_5d"], Decimal("-0.5"))


class RunBacktestTests(unittest.TestCase):
    def test_returns_empty_when_no_stacks_match(self) -> None:
        with patch(
            "genkei.experiments.stack_backtest.query_events", return_value=[]
        ):
            stack_returns, baselines = run_backtest()
        self.assertEqual(stack_returns, [])
        self.assertEqual(baselines, {})

    def test_loads_prices_once_per_asset(self) -> None:
        # Two AAPL stacks share one price-series load; one NVDA stack
        # triggers a second load.
        def insider(asset: str, ts: datetime) -> SignalEvent:
            return _event(
                asset=asset, ts=ts, source="insider_clusters", signal_kind="buy_cluster"
            )

        def crowding(asset: str, ts: datetime) -> SignalEvent:
            return _event(
                asset=asset, ts=ts, source="crowding", signal_kind="crowding_add"
            )

        events = [
            insider("AAPL", _utc(2024, 1, 1)),
            crowding("AAPL", _utc(2024, 1, 2)),
            insider("AAPL", _utc(2024, 6, 1)),
            crowding("AAPL", _utc(2024, 6, 2)),
            insider("NVDA", _utc(2024, 3, 1)),
            crowding("NVDA", _utc(2024, 3, 2)),
        ]

        def fake_load(ticker: str, *, since: date, until: date) -> list[PricePoint]:
            return _linear_price_series(start=since, days=(until - since).days + 1)

        # Use the rule that matches our synthetic events (buy_cluster + crowding_add).
        with (
            patch(
                "genkei.experiments.stack_backtest.query_events",
                return_value=events,
            ),
            patch(
                "genkei.experiments.stack_backtest.load_price_series",
                side_effect=fake_load,
            ) as load_mock,
        ):
            stack_returns, baselines = run_backtest(rule="smart_money_buy")

        # AAPL stacks share one load call; NVDA triggers a second.
        loaded_assets = sorted({call.args[0] for call in load_mock.call_args_list})
        self.assertEqual(loaded_assets, ["AAPL", "NVDA"])
        self.assertEqual(load_mock.call_count, 2)

        # Baselines populated for both assets.
        self.assertEqual(sorted(baselines.keys()), ["AAPL", "NVDA"])

        # At least one stack on each asset should have computable windows.
        assets_with_returns = {sr.stack.asset for sr in stack_returns}
        self.assertIn("AAPL", assets_with_returns)
        self.assertIn("NVDA", assets_with_returns)

    def test_direction_filter_applied(self) -> None:
        # Inject only bearish-direction events; the bullish filter should
        # return zero stacks even though there are matching events.
        bearish_events = [
            _event(
                asset="AAPL",
                ts=_utc(2024, 1, 1),
                source="insider_clusters",
                signal_kind="sell_cluster",
                direction="bearish",
            ),
            _event(
                asset="AAPL",
                ts=_utc(2024, 1, 5),
                source="crowding",
                signal_kind="crowding_exit",
                direction="bearish",
            ),
        ]

        def fake_load(ticker: str, *, since: date, until: date) -> list[PricePoint]:
            return _linear_price_series(start=since, days=(until - since).days + 1)

        with (
            patch(
                "genkei.experiments.stack_backtest.query_events",
                return_value=bearish_events,
            ),
            patch(
                "genkei.experiments.stack_backtest.load_price_series",
                side_effect=fake_load,
            ),
        ):
            stack_returns, _ = run_backtest(direction="bullish")
        self.assertEqual(stack_returns, [])

    def test_since_query_includes_rule_lookback_then_filters_stack_end(self) -> None:
        def insider(asset: str, ts: datetime) -> SignalEvent:
            return _event(
                asset=asset, ts=ts, source="insider_clusters", signal_kind="buy_cluster"
            )

        def crowding(asset: str, ts: datetime) -> SignalEvent:
            return _event(
                asset=asset, ts=ts, source="crowding", signal_kind="crowding_add"
            )

        events = [
            insider("AAPL", _utc(2023, 12, 29)),
            crowding("AAPL", _utc(2024, 1, 2)),
        ]

        def fake_load(ticker: str, *, since: date, until: date) -> list[PricePoint]:
            return _linear_price_series(start=since, days=(until - since).days + 1)

        with (
            patch(
                "genkei.experiments.stack_backtest.query_events",
                return_value=events,
            ) as query_mock,
            patch(
                "genkei.experiments.stack_backtest.load_price_series",
                side_effect=fake_load,
            ),
        ):
            stack_returns, _ = run_backtest(
                rule="smart_money_buy",
                since=date(2024, 1, 1),
            )

        self.assertEqual(query_mock.call_args.kwargs["since"], _utc(2023, 12, 25))
        self.assertEqual(len(stack_returns), 1)
        self.assertEqual(stack_returns[0].stack.window_end, _utc(2024, 1, 2))

    def test_price_load_starts_before_return_anchor_date(self) -> None:
        events = [
            _event(
                asset="AAPL",
                ts=_utc(2024, 1, 5),
                source="insider_clusters",
                signal_kind="buy_cluster",
            ),
            _event(
                asset="AAPL",
                ts=_utc(2024, 1, 6),
                source="crowding",
                signal_kind="crowding_add",
            ),
        ]

        def fake_load(ticker: str, *, since: date, until: date) -> list[PricePoint]:
            return _linear_price_series(start=since, days=(until - since).days + 1)

        with (
            patch(
                "genkei.experiments.stack_backtest.query_events",
                return_value=events,
            ),
            patch(
                "genkei.experiments.stack_backtest.load_price_series",
                side_effect=fake_load,
            ) as load_mock,
        ):
            run_backtest(rule="smart_money_buy")

        self.assertEqual(load_mock.call_args.kwargs["since"], date(2024, 2, 13))


if __name__ == "__main__":
    unittest.main()
