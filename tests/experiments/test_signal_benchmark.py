"""Unit tests for the live-correlator benchmark adjustment (B-100)."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from genkei.experiments.signal_benchmark import (
    ASSET_CLASS_BENCHMARK_SOURCES,
    DEFAULT_CRYPTO_BENCHMARK,
    DEFAULT_EQUITY_BENCHMARK,
    StackBenchmarkContext,
    _benchmark_for,
    _coinbase_product_for,
    _dt_to_date,
    _load_coinbase_series,
    _load_yahoo_series,
    compute_stack_benchmark_contexts,
    compute_window_return_pct,
)
from genkei.experiments.signal_store import Stack


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


_DEFAULT_WINDOW_START = _utc(2024, 1, 1)
_DEFAULT_WINDOW_END = _utc(2024, 1, 31)


def _stack(
    *,
    asset: str = "AAPL",
    asset_class: str = "equity",
    rule_name: str = "broad_exit",
    direction: str = "bearish",
    window_start: datetime = _DEFAULT_WINDOW_START,
    window_end: datetime = _DEFAULT_WINDOW_END,
    horizon: str = "equity:core",
) -> Stack:
    return Stack(
        rule_name=rule_name,
        asset=asset,
        asset_class=asset_class,
        direction=direction,
        window_start=window_start,
        window_end=window_end,
        score=Decimal("2.0"),
        distinct_sources=2,
        event_count=2,
        horizon=horizon,
        events=[],
    )


class ComputeWindowReturnPctTests(unittest.TestCase):
    def test_empty_series_yields_none(self) -> None:
        self.assertIsNone(
            compute_window_return_pct([], date(2024, 1, 1), date(2024, 1, 31))
        )

    def test_zero_width_window_yields_none(self) -> None:
        # end == start → no window to measure over, return None rather
        # than silently zero (the latter would look like "no benchmark
        # move" which is materially different from "couldn't compute").
        prices = [(date(2024, 1, 1), Decimal("100"))]
        self.assertIsNone(
            compute_window_return_pct(prices, date(2024, 1, 1), date(2024, 1, 1))
        )

    def test_negative_window_yields_none(self) -> None:
        prices = [(date(2024, 1, 1), Decimal("100"))]
        self.assertIsNone(
            compute_window_return_pct(prices, date(2024, 1, 31), date(2024, 1, 1))
        )

    def test_basic_positive_return(self) -> None:
        # 100 → 120 over the window = +20%
        prices = [
            (date(2024, 1, 1), Decimal("100")),
            (date(2024, 1, 15), Decimal("110")),
            (date(2024, 1, 31), Decimal("120")),
        ]
        self.assertEqual(
            compute_window_return_pct(prices, date(2024, 1, 1), date(2024, 1, 31)),
            Decimal("20"),
        )

    def test_basic_negative_return(self) -> None:
        # 100 → 80 over the window = -20%
        prices = [
            (date(2024, 1, 1), Decimal("100")),
            (date(2024, 1, 31), Decimal("80")),
        ]
        self.assertEqual(
            compute_window_return_pct(prices, date(2024, 1, 1), date(2024, 1, 31)),
            Decimal("-20"),
        )

    def test_start_before_series_yields_none(self) -> None:
        prices = [(date(2024, 6, 1), Decimal("100"))]
        self.assertIsNone(
            compute_window_return_pct(prices, date(2024, 1, 1), date(2024, 6, 30))
        )

    def test_start_price_zero_yields_none(self) -> None:
        prices = [
            (date(2024, 1, 1), Decimal("0")),
            (date(2024, 1, 31), Decimal("100")),
        ]
        self.assertIsNone(
            compute_window_return_pct(prices, date(2024, 1, 1), date(2024, 1, 31))
        )

    def test_uses_close_on_or_before(self) -> None:
        # Anchor dates fall on non-trading days (no rows on those exact
        # dates) — the helper picks the most recent prior close. Asking
        # for [1/3, 1/31] uses 1/2 (100, on-or-before 1/3) and 1/30
        # (110, on-or-before 1/31) = +10%.
        prices = [
            (date(2024, 1, 2), Decimal("100")),
            (date(2024, 1, 30), Decimal("110")),
        ]
        self.assertEqual(
            compute_window_return_pct(prices, date(2024, 1, 3), date(2024, 1, 31)),
            Decimal("10"),
        )


class BenchmarkForTests(unittest.TestCase):
    def test_equity_class_returns_equity_benchmark(self) -> None:
        self.assertEqual(_benchmark_for("equity", "SPY", "BTC"), "SPY")

    def test_crypto_class_returns_crypto_benchmark(self) -> None:
        self.assertEqual(_benchmark_for("crypto", "SPY", "BTC"), "BTC")

    def test_unknown_class_yields_none(self) -> None:
        self.assertIsNone(_benchmark_for("protocol", "SPY", "BTC"))

    def test_defaults_are_documented_constants(self) -> None:
        # Pinning the defaults so a future re-tune notices these are
        # the documented per-class comparators.
        self.assertEqual(DEFAULT_EQUITY_BENCHMARK, "SPY")
        self.assertEqual(DEFAULT_CRYPTO_BENCHMARK, "BTC")
        # Asset-class → source map is the future-extension hook.
        self.assertEqual(ASSET_CLASS_BENCHMARK_SOURCES["equity"], "yahoo")
        self.assertEqual(ASSET_CLASS_BENCHMARK_SOURCES["crypto"], "coinbase")


class CoinbaseProductForTests(unittest.TestCase):
    def test_resolves_coingecko_id_to_watchlist_product(self) -> None:
        self.assertEqual(_coinbase_product_for("ethereum"), "ETH-USD")
        self.assertEqual(_coinbase_product_for("pyth-network"), "PYTH-USD")
        self.assertEqual(_coinbase_product_for("bitcoin"), "BTC-USD")

    def test_resolves_symbol_to_watchlist_product(self) -> None:
        self.assertEqual(_coinbase_product_for("ETH"), "ETH-USD")
        self.assertEqual(_coinbase_product_for("BTC"), "BTC-USD")


class DtToDateTests(unittest.TestCase):
    def test_aware_datetime_converts_to_utc_date(self) -> None:
        self.assertEqual(_dt_to_date(_utc(2024, 6, 1)), date(2024, 6, 1))

    def test_naive_datetime_passes_through(self) -> None:
        # Defensive — we should never see naive datetimes in practice,
        # but the helper handles them by treating the date directly.
        self.assertEqual(
            _dt_to_date(datetime(2024, 6, 1, 12, 0)), date(2024, 6, 1)
        )


class ComputeStackBenchmarkContextsTests(unittest.TestCase):
    """Mock the lake loaders and exercise the orchestrator."""

    def test_empty_stacks_yields_empty(self) -> None:
        self.assertEqual(compute_stack_benchmark_contexts([]), [])

    def test_equity_stack_uses_yahoo_loaders_with_spy(self) -> None:
        stack = _stack(asset="NVDA", asset_class="equity")
        # NVDA up 30%; SPY up 10% over the same window → abnormal +20pp.
        nvda_prices = [
            (date(2024, 1, 1), Decimal("100")),
            (date(2024, 1, 31), Decimal("130")),
        ]
        spy_prices = [
            (date(2024, 1, 1), Decimal("100")),
            (date(2024, 1, 31), Decimal("110")),
        ]

        def fake_yahoo(ticker: str, *, since: date, until: date) -> list:
            if ticker == "NVDA":
                return nvda_prices
            if ticker == "SPY":
                return spy_prices
            return []

        with patch(
            "genkei.experiments.signal_benchmark._load_yahoo_series",
            side_effect=fake_yahoo,
        ):
            out = compute_stack_benchmark_contexts([stack])
        self.assertEqual(len(out), 1)
        ctx = out[0]
        self.assertEqual(ctx.benchmark_ticker, "SPY")
        self.assertEqual(ctx.asset_return_pct, Decimal("30"))
        self.assertEqual(ctx.benchmark_return_pct, Decimal("10"))
        self.assertEqual(ctx.abnormal_pct, Decimal("20"))

    def test_crypto_stack_uses_coinbase_loaders_with_btc(self) -> None:
        stack = _stack(
            asset="ethereum",
            asset_class="crypto",
            horizon="crypto:core",
        )
        # ETH down 30%; BTC down 10% over window → abnormal -20pp.
        eth_prices = [
            (date(2024, 1, 1), Decimal("3000")),
            (date(2024, 1, 31), Decimal("2100")),
        ]
        btc_prices = [
            (date(2024, 1, 1), Decimal("50000")),
            (date(2024, 1, 31), Decimal("45000")),
        ]

        def fake_coinbase(product: str, *, since: date, until: date) -> list:
            if product == "ETH-USD":
                return eth_prices
            if product == "BTC-USD":
                return btc_prices
            return []

        with patch(
            "genkei.experiments.signal_benchmark._load_coinbase_series",
            side_effect=fake_coinbase,
        ):
            out = compute_stack_benchmark_contexts([stack])
        ctx = out[0]
        self.assertEqual(ctx.benchmark_ticker, "BTC")
        self.assertEqual(ctx.asset_return_pct, Decimal("-30"))
        self.assertEqual(ctx.benchmark_return_pct, Decimal("-10"))
        self.assertEqual(ctx.abnormal_pct, Decimal("-20"))

    def test_unknown_asset_class_yields_no_benchmark(self) -> None:
        stack = _stack(
            asset="some-protocol",
            asset_class="protocol",
            horizon="protocol:lending",
        )
        # No DB calls should fire for an unmapped asset_class — the
        # orchestrator short-circuits via _benchmark_for returning None.
        with (
            patch(
                "genkei.experiments.signal_benchmark._load_yahoo_series",
                side_effect=AssertionError("should not be called"),
            ),
            patch(
                "genkei.experiments.signal_benchmark._load_coinbase_series",
                side_effect=AssertionError("should not be called"),
            ),
        ):
            out = compute_stack_benchmark_contexts([stack])
        ctx = out[0]
        self.assertIsNone(ctx.benchmark_ticker)
        self.assertIsNone(ctx.asset_return_pct)
        self.assertIsNone(ctx.benchmark_return_pct)
        self.assertIsNone(ctx.abnormal_pct)

    def test_loads_each_series_once_per_ticker(self) -> None:
        # Two stacks on NVDA + one on AAPL → three asset-loads if no
        # caching, two if the orchestrator dedupes by (asset_class, ticker).
        stacks = [
            _stack(asset="NVDA", window_end=_utc(2024, 2, 1)),
            _stack(asset="NVDA", window_end=_utc(2024, 3, 1)),
            _stack(asset="AAPL", window_end=_utc(2024, 4, 1)),
        ]
        empty_prices: list = []
        calls: list[str] = []

        def fake_yahoo(ticker: str, *, since: date, until: date) -> list:
            calls.append(ticker)
            return empty_prices

        with patch(
            "genkei.experiments.signal_benchmark._load_yahoo_series",
            side_effect=fake_yahoo,
        ):
            compute_stack_benchmark_contexts(stacks)
        # Three unique tickers loaded: NVDA, AAPL, SPY. Each exactly once.
        self.assertEqual(sorted(calls), ["AAPL", "NVDA", "SPY"])

    def test_mixed_asset_classes_route_to_correct_loaders(self) -> None:
        stacks = [
            _stack(asset="AAPL", asset_class="equity"),
            _stack(asset="ethereum", asset_class="crypto", horizon="crypto:core"),
        ]
        yahoo_calls: list[str] = []
        coinbase_calls: list[str] = []

        def fake_yahoo(ticker: str, *, since: date, until: date) -> list:
            yahoo_calls.append(ticker)
            return []

        def fake_coinbase(product: str, *, since: date, until: date) -> list:
            coinbase_calls.append(product)
            return []

        with (
            patch(
                "genkei.experiments.signal_benchmark._load_yahoo_series",
                side_effect=fake_yahoo,
            ),
            patch(
                "genkei.experiments.signal_benchmark._load_coinbase_series",
                side_effect=fake_coinbase,
            ),
        ):
            compute_stack_benchmark_contexts(stacks)
        # Yahoo loaded for AAPL + SPY benchmark.
        self.assertEqual(sorted(yahoo_calls), ["AAPL", "SPY"])
        # Coinbase loaded for ETH-USD asset + BTC-USD benchmark.
        self.assertEqual(sorted(coinbase_calls), ["BTC-USD", "ETH-USD"])

    def test_non_trading_window_start_uses_prior_close(self) -> None:
        stack = _stack(
            asset="NVDA",
            asset_class="equity",
            window_start=_utc(2024, 3, 2),
            window_end=_utc(2024, 3, 4),
        )
        calls: list[tuple[str, date, date]] = []

        def fake_yahoo(ticker: str, *, since: date, until: date) -> list:
            calls.append((ticker, since, until))
            if ticker == "NVDA":
                return [
                    (date(2024, 3, 1), Decimal("100")),
                    (date(2024, 3, 4), Decimal("110")),
                ]
            if ticker == "SPY":
                return [
                    (date(2024, 3, 1), Decimal("200")),
                    (date(2024, 3, 4), Decimal("210")),
                ]
            return []

        with patch(
            "genkei.experiments.signal_benchmark._load_yahoo_series",
            side_effect=fake_yahoo,
        ):
            out = compute_stack_benchmark_contexts([stack])
        self.assertEqual(
            sorted(calls),
            [
                ("NVDA", date(2024, 3, 2), date(2024, 3, 4)),
                ("SPY", date(2024, 3, 2), date(2024, 3, 4)),
            ],
        )
        self.assertEqual(out[0].asset_return_pct, Decimal("10"))
        self.assertEqual(out[0].benchmark_return_pct, Decimal("5.00"))
        self.assertEqual(out[0].abnormal_pct, Decimal("5.00"))

    def test_overrides_propagate(self) -> None:
        stack = _stack(asset="NVDA")
        calls: list[str] = []

        def fake_yahoo(ticker: str, *, since: date, until: date) -> list:
            calls.append(ticker)
            return []

        with patch(
            "genkei.experiments.signal_benchmark._load_yahoo_series",
            side_effect=fake_yahoo,
        ):
            out = compute_stack_benchmark_contexts(
                [stack], equity_benchmark="QQQ"
            )
        self.assertIn("QQQ", calls)
        self.assertNotIn("SPY", calls)
        self.assertEqual(out[0].benchmark_ticker, "QQQ")


class StackBenchmarkContextDataclassTests(unittest.TestCase):
    def test_fields_default_to_none(self) -> None:
        ctx = StackBenchmarkContext(
            stack_index=0,
            benchmark_ticker=None,
            asset_return_pct=None,
            benchmark_return_pct=None,
            abnormal_pct=None,
        )
        self.assertEqual(ctx.stack_index, 0)
        self.assertIsNone(ctx.benchmark_ticker)


class LoaderSqlTests(unittest.TestCase):
    def test_yahoo_loader_includes_latest_prior_close(self) -> None:
        cursor = _FakeCursor(rows=[(date(2024, 3, 1), Decimal("100"))])
        with patch(
            "genkei.experiments.signal_benchmark.db.connection",
            return_value=_FakeConn(cursor),
        ):
            rows = _load_yahoo_series(
                "NVDA", since=date(2024, 3, 2), until=date(2024, 3, 4)
            )
        self.assertEqual(rows, [(date(2024, 3, 1), Decimal("100"))])
        self.assertIn("SELECT (ts AT TIME ZONE 'UTC')::date AS d", cursor.sql)
        self.assertIn("SELECT MAX((ts AT TIME ZONE 'UTC')::date)", cursor.sql)
        self.assertIn("AND (ts AT TIME ZONE 'UTC')::date <= %s", cursor.sql)
        self.assertIn("ORDER BY (ts AT TIME ZONE 'UTC')::date ASC", cursor.sql)
        self.assertNotIn("ts::date", cursor.sql)
        self.assertEqual(
            cursor.params,
            [
                "NVDA",
                date(2024, 3, 2),
                "NVDA",
                date(2024, 3, 2),
                date(2024, 3, 4),
            ],
        )

    def test_coinbase_loader_includes_latest_prior_close(self) -> None:
        cursor = _FakeCursor(rows=[(date(2024, 3, 1), Decimal("100"))])
        with patch(
            "genkei.experiments.signal_benchmark.db.connection",
            return_value=_FakeConn(cursor),
        ):
            rows = _load_coinbase_series(
                "ETH-USD", since=date(2024, 3, 2), until=date(2024, 3, 4)
            )
        self.assertEqual(rows, [(date(2024, 3, 1), Decimal("100"))])
        self.assertIn("SELECT (ts AT TIME ZONE 'UTC')::date AS d", cursor.sql)
        self.assertIn("SELECT MAX((ts AT TIME ZONE 'UTC')::date)", cursor.sql)
        self.assertIn("AND (ts AT TIME ZONE 'UTC')::date <= %s", cursor.sql)
        self.assertIn("ORDER BY (ts AT TIME ZONE 'UTC')::date ASC", cursor.sql)
        self.assertNotIn("ts::date", cursor.sql)
        self.assertEqual(
            cursor.params,
            [
                "ETH-USD",
                date(2024, 3, 2),
                "ETH-USD",
                date(2024, 3, 2),
                date(2024, 3, 4),
            ],
        )


class _FakeCursor:
    def __init__(self, *, rows: list[tuple[date, Decimal]]) -> None:
        self.rows = rows
        self.sql = ""
        self.params: list[object] = []

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def execute(self, sql: str, params: list[object]) -> None:
        self.sql = sql
        self.params = params

    def fetchall(self) -> list[tuple[date, Decimal]]:
        return self.rows


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> _FakeConn:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return self._cursor


if __name__ == "__main__":
    unittest.main()
