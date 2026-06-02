"""Live-correlator benchmark adjustment for stack scores (B-100).

Presentation-layer counterpart to the B-102 backtest's abnormal-return
column. When `genkei signals` shows a stack, this module attaches "how
did the asset do vs its market benchmark over the stack's window?" so
the reader can interpret the stack against broad-market context at the
moment of decision, not only post-hoc in the backtest.

**Design choice: presentation layer, not score mutation.** The B-100
acceptance criteria leaves room to modify the correlator's score
directly. We chose to leave `detect_stacks` math invariant and surface
benchmark context as an additional column. Two reasons:

  1. The B-102 backtest already proved the *honest* framing is "show
     both the raw and benchmark-adjusted reads; let the reader weigh
     them against the rule direction." Modifying the score would
     collapse those two reads into one number and lose the framing.
  2. Asset-class-aware benchmark routing means different stacks get
     different benchmarks (equity → SPY, crypto → BTC). A single
     "score" mutation would have to pick one or average them; the
     column approach keeps each stack's comparator explicit.

**Per-asset-class benchmark routing.** Each stack's `asset_class`
picks its comparator:

  * `equity` → SPY (from `yahoo.candles`).
  * `crypto` → BTC (from `coinbase.candles` via the `BTC-USD` product).
  * Other / future asset classes → no benchmark; abnormal column
    renders n/a.

Both benchmarks are overridable per-class via CLI flags so a user can
ask "but vs QQQ?" or "but vs ETH?" without code changes.

**Window math.** The "asset return" is computed from the close on-or-
before `window_start` to the close on-or-before `window_end`. The
benchmark return uses the same date pair. ``abnormal_pct =
asset_return_pct - benchmark_return_pct`` in percentage points.

A stack with a single-day window (window_start == window_end, all
events on one day) has zero return on both sides → abnormal = 0; that's
a degenerate case the column documents rather than hides.

**No-lookahead.** All inputs are observable at `window_end` by
construction — the stack itself can't be detected until then. Same
guarantee the backtest's anchor-date logic provides.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from genkei.common import db
from genkei.experiments.signal_store import Stack

# Default benchmark choices per asset class. Both live in already-
# populated tables: SPY landed in yahoo.candles via B-102, BTC has been
# in coinbase.candles since the original B-035 ingester. Both go back
# years (SPY: 1993, BTC: 2015), so the benchmark series covers any
# historical stack the correlator can surface.
DEFAULT_EQUITY_BENCHMARK = "SPY"
DEFAULT_CRYPTO_BENCHMARK = "BTC"

# Asset-class → (loader-source, default-benchmark) map. Kept here so a
# future asset class lands a single entry rather than threading through
# the orchestrator.
ASSET_CLASS_BENCHMARK_SOURCES: dict[str, str] = {
    "equity": "yahoo",
    "crypto": "coinbase",
}


@dataclass(frozen=True)
class StackBenchmarkContext:
    """Per-stack benchmark-relative return over the stack's own window.

    ``stack_index`` joins back to the parallel input ``stacks`` list so
    callers can render the column alongside the stack without mutating
    the immutable ``Stack`` dataclass. ``abnormal_pct`` may be None
    when the benchmark series doesn't cover the stack's window (e.g.
    crypto stack pre-2015 — shouldn't happen given current data spans —
    or an equity stack on a watchlist whose benchmark is missing).
    """

    stack_index: int
    benchmark_ticker: str | None
    asset_return_pct: Decimal | None
    benchmark_return_pct: Decimal | None
    abnormal_pct: Decimal | None


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _dt_to_date(value: datetime) -> date:
    """Convert a Stack's window_start/end (UTC datetime) to a UTC date."""
    if value.tzinfo is None:
        return value.date()
    return value.astimezone(timezone.utc).date()


def compute_window_return_pct(
    prices: Sequence[tuple[date, Decimal]],
    start_date: date,
    end_date: date,
) -> Decimal | None:
    """Compute pct return from close on-or-before ``start_date`` to
    close on-or-before ``end_date``.

    Returns None when either anchor falls before the series starts,
    the start price is zero, or end_date ≤ start_date (zero-width
    window — caller decides whether that's 0% or n/a; we return None
    so the column reads honestly as "no data" rather than silently 0%).
    """
    if not prices:
        return None
    if end_date <= start_date:
        return None
    dates = [d for d, _ in prices]
    start_idx = bisect_right(dates, start_date) - 1
    end_idx = bisect_right(dates, end_date) - 1
    if start_idx < 0 or end_idx <= start_idx:
        return None
    start_price = prices[start_idx][1]
    end_price = prices[end_idx][1]
    if start_price == 0:
        return None
    return Decimal("100") * (end_price - start_price) / start_price


# ---------------------------------------------------------------------------
# Lake loaders (one per benchmark source)
# ---------------------------------------------------------------------------


def _load_yahoo_series(
    ticker: str,
    *,
    since: date | None = None,
    until: date | None = None,
) -> list[tuple[date, Decimal]]:
    """Load (ts, adj_close) from ``yahoo.candles`` for one ticker, ascending."""
    sql = (
        "SELECT ts::date AS d, COALESCE(adj_close, close)::numeric "
        "FROM yahoo.candles "
        "WHERE ticker = %s AND COALESCE(adj_close, close) IS NOT NULL"
    )
    params: list[Any] = [ticker]
    if since is not None:
        sql += " AND ts::date >= %s"
        params.append(since)
    if until is not None:
        sql += " AND ts::date <= %s"
        params.append(until)
    sql += " ORDER BY ts::date ASC"
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [(d, Decimal(price)) for d, price in cur.fetchall()]


def _load_coinbase_series(
    product: str,
    *,
    since: date | None = None,
    until: date | None = None,
) -> list[tuple[date, Decimal]]:
    """Load (ts, close) from ``coinbase.candles`` for one product, ascending."""
    sql = (
        "SELECT ts::date AS d, close::numeric "
        "FROM coinbase.candles "
        "WHERE product = %s AND close IS NOT NULL"
    )
    params: list[Any] = [product]
    if since is not None:
        sql += " AND ts::date >= %s"
        params.append(since)
    if until is not None:
        sql += " AND ts::date <= %s"
        params.append(until)
    sql += " ORDER BY ts::date ASC"
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [(d, Decimal(price)) for d, price in cur.fetchall()]


def _load_asset_series(
    asset: str,
    asset_class: str,
    *,
    since: date | None = None,
    until: date | None = None,
) -> list[tuple[date, Decimal]]:
    """Route an asset to its lake source by class.

    Equity assets live in ``yahoo.candles`` keyed by ticker. Crypto
    assets live in ``coinbase.candles`` keyed by the coinbase product
    code, which is ``<UPPER>-USD`` for the assets the watchlist
    tracks. The crypto-side emitters (B-095, B-098) write events with
    the coingecko_id as ``asset`` (lowercase: "ethereum", "solana"),
    so we uppercase + suffix to derive the coinbase product.
    """
    if asset_class == "equity":
        return _load_yahoo_series(asset, since=since, until=until)
    if asset_class == "crypto":
        product = f"{asset.upper()}-USD"
        return _load_coinbase_series(product, since=since, until=until)
    return []


def _load_benchmark_series(
    benchmark: str,
    asset_class: str,
    *,
    since: date | None = None,
    until: date | None = None,
) -> list[tuple[date, Decimal]]:
    """Load the benchmark price series for an asset class.

    Equity-side benchmarks live in ``yahoo.candles`` (SPY/QQQ/IWM
    landed via B-102). Crypto-side benchmarks live in
    ``coinbase.candles`` (BTC-USD has been there since B-035). The
    benchmark ticker on the crypto side is the watchlist symbol
    ("BTC"), which we suffix into the coinbase product code.
    """
    if asset_class == "equity":
        return _load_yahoo_series(benchmark, since=since, until=until)
    if asset_class == "crypto":
        product = f"{benchmark.upper()}-USD"
        return _load_coinbase_series(product, since=since, until=until)
    return []


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def compute_stack_benchmark_contexts(
    stacks: Sequence[Stack],
    *,
    equity_benchmark: str = DEFAULT_EQUITY_BENCHMARK,
    crypto_benchmark: str = DEFAULT_CRYPTO_BENCHMARK,
) -> list[StackBenchmarkContext]:
    """For each stack, compute asset return + benchmark return + abnormal
    over the stack's own window (window_start → window_end).

    Loads each (asset_class, ticker) series once across the union of
    stack windows. Stacks whose ``asset_class`` has no benchmark
    mapping render a context with ``benchmark_ticker=None`` so the
    consumer can show the column as n/a without branching.
    """
    if not stacks:
        return []

    # Bucket by (asset_class, ticker) for load-once-per-series.
    asset_keys: dict[tuple[str, str], list[int]] = {}
    benchmark_keys: dict[tuple[str, str], list[int]] = {}
    for idx, stack in enumerate(stacks):
        asset_keys.setdefault((stack.asset_class, stack.asset), []).append(idx)
        bench_ticker = _benchmark_for(stack.asset_class, equity_benchmark, crypto_benchmark)
        if bench_ticker is not None:
            benchmark_keys.setdefault((stack.asset_class, bench_ticker), []).append(idx)

    # Date range needed per series.
    span_by_asset: dict[tuple[str, str], tuple[date, date]] = {}
    for key, stack_indices in asset_keys.items():
        starts = [_dt_to_date(stacks[i].window_start) for i in stack_indices]
        ends = [_dt_to_date(stacks[i].window_end) for i in stack_indices]
        span_by_asset[key] = (min(starts), max(ends))

    span_by_benchmark: dict[tuple[str, str], tuple[date, date]] = {}
    for key, stack_indices in benchmark_keys.items():
        starts = [_dt_to_date(stacks[i].window_start) for i in stack_indices]
        ends = [_dt_to_date(stacks[i].window_end) for i in stack_indices]
        span_by_benchmark[key] = (min(starts), max(ends))

    asset_series: dict[tuple[str, str], list[tuple[date, Decimal]]] = {}
    for key, (start, end) in span_by_asset.items():
        asset_class, asset = key
        asset_series[key] = _load_asset_series(
            asset, asset_class, since=start, until=end
        )

    benchmark_series: dict[tuple[str, str], list[tuple[date, Decimal]]] = {}
    for key, (start, end) in span_by_benchmark.items():
        asset_class, benchmark = key
        benchmark_series[key] = _load_benchmark_series(
            benchmark, asset_class, since=start, until=end
        )

    out: list[StackBenchmarkContext] = []
    for idx, stack in enumerate(stacks):
        bench_ticker = _benchmark_for(stack.asset_class, equity_benchmark, crypto_benchmark)
        asset_prices = asset_series.get((stack.asset_class, stack.asset), [])
        bench_prices = (
            benchmark_series.get((stack.asset_class, bench_ticker), [])
            if bench_ticker
            else []
        )
        start_date = _dt_to_date(stack.window_start)
        end_date = _dt_to_date(stack.window_end)
        asset_ret = compute_window_return_pct(asset_prices, start_date, end_date)
        bench_ret = compute_window_return_pct(bench_prices, start_date, end_date)
        abnormal = (
            asset_ret - bench_ret
            if asset_ret is not None and bench_ret is not None
            else None
        )
        out.append(
            StackBenchmarkContext(
                stack_index=idx,
                benchmark_ticker=bench_ticker,
                asset_return_pct=asset_ret,
                benchmark_return_pct=bench_ret,
                abnormal_pct=abnormal,
            )
        )
    return out


def _benchmark_for(
    asset_class: str,
    equity_benchmark: str,
    crypto_benchmark: str,
) -> str | None:
    """Pick the benchmark ticker for an asset class. None for unknown classes."""
    if asset_class == "equity":
        return equity_benchmark
    if asset_class == "crypto":
        return crypto_benchmark
    return None
