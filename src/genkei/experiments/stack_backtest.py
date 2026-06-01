"""Stack-outcome backtest (B-101).

Phase 6.2 experiment. **Asks: do historical multi-source stacks actually
precede meaningful forward returns?** That's the payoff question for the
whole cross-source correlation engine (B-064) — without a measurable lift
vs a random-day baseline, the stacks are just plumbing.

The shape mirrors B-057's 8-K event study: load a stream of events (here:
``Stack`` records produced by ``detect_stacks``), join each to the issuer's
adjusted-close price series, compute per-window forward returns, then
aggregate by stratum (rule / direction / asset) into mean / median /
hit-rate / lift summaries. The reusable bits — ``PricePoint``,
``load_price_series``, ``compute_windowed_returns`` — are imported
directly from ``eight_k_impact`` rather than re-implemented; two callers
isn't enough to extract them into a shared module (per the project's
"wait for the third copy" rule), but it's enough to share existing
generic helpers cleanly.

**Baseline.** Per-asset random-day mean over the same windows. For each
asset that fires at least one stack, sample every Nth trading day of
its history and compute mean / hit-rate forward returns at each
``STACK_WINDOWS`` horizon. The stack-side stats are then compared
against this asset-specific baseline so that a strong underlying name
(NVDA, AAPL) doesn't make every stack look "good" just because the
ticker went up. The `mean_excess_pct` field captures the lift: positive
means the stack outperformed the asset's random-day average. The
reader interprets the *sign* against the rule's direction (bullish:
positive excess is the win; bearish: negative excess is the win).

**No-lookahead guarantee.** Every forward return is computed from the
stack's knowable date forward — never backward. Most emitters are knowable
at their event timestamp, but delayed sources like 13F crowding and Form 4
insider clusters are shifted by their filing lag before the return window
starts. We do not filter or weight stacks based on what happens after that
anchor date.

**v1 scope.** Raw returns only. SPY-adjusted abnormal returns require
the SPY ingester (filed as B-102 alongside this work) and the
benchmark-adjustment logic from B-100; both are separate follow-ups
that don't block honest v1 measurement. Stacks on crypto assets are
out of scope until B-095–B-098 add the crypto-side emitters and
crypto-side correlation rules — no rule today emits crypto stacks
so this is moot.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from genkei.experiments.eight_k_impact import (
    PricePoint,
    compute_windowed_returns,
    load_price_series,
)
from genkei.experiments.signal_rules import DEFAULT_RULES_PATH, load_rules
from genkei.experiments.signal_store import (
    CorrelationRule,
    SignalEvent,
    Stack,
    detect_stacks,
    query_events,
)

# Forward-return windows tailored to stack-driven investment horizons.
# Calendar-day offsets (compute_windowed_returns picks the nearest trading
# day via bisect). 5 / 30 / 90 / 180 / 365 ≈ 1wk / 1mo / 1q / 6mo / 1y of
# real investment-decision horizon. The 5d window aligns with the
# `smart_money_buy` rule's 7d signal-stacking window so a reader can read
# "the stack fired; did the price move within the rule's own horizon?"
# The 90 / 365d windows cover the `broad_exit` 90d horizon and the
# longer-term-hold sleeve evaluation.
STACK_WINDOWS: tuple[tuple[str, int, int], ...] = (
    ("post_5d", 0, 5),
    ("post_30d", 0, 30),
    ("post_90d", 0, 90),
    ("post_180d", 0, 180),
    ("post_365d", 0, 365),
)
# Baseline sampling cadence. 7-day step over ~10y of price history yields
# ~520 baseline samples per asset — enough for a stable mean without
# burning compute or relying on a random seed (which would break
# determinism). Stride larger than 1 because adjacent trading days carry
# heavy autocorrelation; weekly samples are nearly independent.
BASELINE_SAMPLE_STEP_DAYS = 7
# Cushion added to the forward end of the price load so the widest
# window's offset always resolves to a real trading day rather than
# falling off the end of the loaded series.
PRICE_LOAD_CUSHION_DAYS = 14
# Pull a small lookback before the first stack date so weekend/holiday
# stack endpoints can still resolve to the prior trading close.
PRICE_LOAD_LOOKBACK_DAYS = 7
# `crowding` events are timestamped at 13F period_of_report. The signal is
# not knowable until the 13F filing lag has elapsed.
SOURCE_AVAILABILITY_LAG_DAYS = {"crowding": 45}
FORM4_AVAILABILITY_LAG_WEEKDAYS = 2


@dataclass(frozen=True)
class StackReturns:
    """Per-stack forward returns at each backtest window.

    ``windows`` keys match the labels in ``STACK_WINDOWS`` (or whichever
    window set was passed in). A ``None`` value means the price series
    didn't reach that window's end — typical for recent stacks where the
    post_365d horizon hasn't elapsed yet.
    """

    stack: Stack
    windows: dict[str, Decimal | None]


@dataclass(frozen=True)
class BaselineStats:
    """Per-asset random-day baseline for forward-return comparison.

    Sampled every ``BASELINE_SAMPLE_STEP_DAYS`` calendar days from the
    asset's loaded price history; for each sample date, the same window
    set as the stack returns. The mean / hit-rate is the "what would a
    random day predict?" comparator.
    """

    asset: str
    n_baseline_samples: int
    mean_pct: dict[str, Decimal | None]
    hit_rate_pct: dict[str, Decimal | None]


@dataclass(frozen=True)
class StackStratumStats:
    """Aggregated backtest stats for one stratum (rule / direction / asset)."""

    stratum_key: str
    n_stacks: int
    horizons: frozenset[str] = field(default_factory=frozenset)
    # Per-window stats — keys match the windows passed to ``aggregate_stack_returns``.
    n_evaluable: dict[str, int] = field(default_factory=dict)
    mean_pct: dict[str, Decimal | None] = field(default_factory=dict)
    median_pct: dict[str, Decimal | None] = field(default_factory=dict)
    hit_rate_pct: dict[str, Decimal | None] = field(default_factory=dict)
    # Excess vs the asset-weighted baseline mean (in percentage points).
    # Positive = stacks beat the random-day mean upward; negative = beat
    # it downward. Interpret sign against the stack's direction.
    mean_excess_pct: dict[str, Decimal | None] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _dt_to_utc_date(value: datetime) -> date:
    if value.tzinfo is None:
        return value.date()
    return value.astimezone(timezone.utc).date()


def _add_weekdays(value: date, weekdays: int) -> date:
    cursor = value
    remaining = weekdays
    while remaining > 0:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            remaining -= 1
    return cursor


def _event_available_date(event: SignalEvent) -> date:
    base_date = _dt_to_utc_date(event.ts)
    if event.source == "insider_clusters":
        return _add_weekdays(base_date, FORM4_AVAILABILITY_LAG_WEEKDAYS)
    lag_days = SOURCE_AVAILABILITY_LAG_DAYS.get(event.source, 0)
    return base_date + timedelta(days=lag_days)


def _stack_return_anchor_date(stack: Stack) -> date:
    """Date from which a stack's forward return could be known and traded."""
    if not stack.events:
        return _dt_to_utc_date(stack.window_end)
    return max(_event_available_date(event) for event in stack.events)


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, Decimal("0")) / Decimal(len(values))


def _median(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n % 2 == 1:
        return sorted_vals[n // 2]
    return (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / Decimal("2")


def _hit_rate(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    hits = sum(1 for v in values if v > Decimal("0"))
    return Decimal("100") * Decimal(hits) / Decimal(len(values))


# ---------------------------------------------------------------------------
# Core computations
# ---------------------------------------------------------------------------


def compute_stack_returns(
    stacks: Sequence[Stack],
    prices_by_asset: dict[str, list[PricePoint]],
    *,
    windows: Sequence[tuple[str, int, int]] = STACK_WINDOWS,
) -> list[StackReturns]:
    """Compute per-window forward returns for each stack.

    ``prices_by_asset`` is supplied by the caller so tests can pass
    synthetic price series and the orchestrator can load each asset's
    history once and share it across the stack-returns pass and the
    baseline pass.
    """
    if not stacks:
        return []
    out: list[StackReturns] = []
    for stack in stacks:
        prices = prices_by_asset.get(stack.asset, [])
        windows_dict = compute_windowed_returns(
            prices, event_date=_stack_return_anchor_date(stack), windows=windows
        )
        out.append(StackReturns(stack=stack, windows=windows_dict))
    return out


def compute_baseline(
    asset: str,
    prices: Sequence[PricePoint],
    *,
    windows: Sequence[tuple[str, int, int]] = STACK_WINDOWS,
    sample_step_days: int = BASELINE_SAMPLE_STEP_DAYS,
) -> BaselineStats:
    """Sample dates uniformly through the asset's history and compute
    mean / hit-rate forward returns at each window.

    Uniform sampling (every Nth calendar day) is deterministic without
    a seed and produces near-independent draws because adjacent trading
    days carry heavy autocorrelation. The first / last sample dates
    naturally exclude windows that would extend past the loaded price
    series — ``compute_windowed_returns`` returns ``None`` for those
    and the per-window mean drops them.
    """
    if sample_step_days <= 0:
        raise ValueError("sample_step_days must be > 0")
    if not prices:
        return BaselineStats(
            asset=asset,
            n_baseline_samples=0,
            mean_pct={label: None for label, _, _ in windows},
            hit_rate_pct={label: None for label, _, _ in windows},
        )
    sample_dates: list[date] = []
    cursor = prices[0].ts
    end = prices[-1].ts
    while cursor <= end:
        sample_dates.append(cursor)
        cursor += timedelta(days=sample_step_days)

    per_window: dict[str, list[Decimal]] = {label: [] for label, _, _ in windows}
    for sample_date in sample_dates:
        windows_dict = compute_windowed_returns(
            prices, event_date=sample_date, windows=windows
        )
        for label, value in windows_dict.items():
            if value is not None:
                per_window[label].append(value)

    mean_pct: dict[str, Decimal | None] = {}
    hit_rate_pct: dict[str, Decimal | None] = {}
    for label, _, _ in windows:
        vals = per_window[label]
        mean_pct[label] = _mean(vals)
        hit_rate_pct[label] = _hit_rate(vals)

    return BaselineStats(
        asset=asset,
        n_baseline_samples=len(sample_dates),
        mean_pct=mean_pct,
        hit_rate_pct=hit_rate_pct,
    )


def aggregate_stack_returns(
    stack_returns: Sequence[StackReturns],
    baselines: dict[str, BaselineStats],
    *,
    stratum_key: str = "all",
    windows: Sequence[tuple[str, int, int]] = STACK_WINDOWS,
) -> StackStratumStats:
    """Aggregate a group of StackReturns into stratum-level stats.

    For each window, computes mean / median / hit_rate over the non-null
    stack returns, then computes ``mean_excess_pct`` as
    ``stack_mean - per-stratum-asset-weighted baseline_mean``. Excess is
    in percentage points (not a ratio) so a +1.50 means "stacks ran 1.5pp
    hotter than the random-day baseline on average."
    """
    n = len(stack_returns)
    n_evaluable: dict[str, int] = {}
    mean_pct: dict[str, Decimal | None] = {}
    median_pct: dict[str, Decimal | None] = {}
    hit_rate_pct: dict[str, Decimal | None] = {}
    mean_excess_pct: dict[str, Decimal | None] = {}

    for label, _, _ in windows:
        non_null = [
            (sr, value)
            for sr in stack_returns
            if (value := sr.windows.get(label)) is not None
        ]
        n_evaluable[label] = len(non_null)
        if not non_null:
            mean_pct[label] = median_pct[label] = hit_rate_pct[label] = None
            mean_excess_pct[label] = None
            continue
        vals = [v for _, v in non_null]
        mean_pct[label] = _mean(vals)
        median_pct[label] = _median(vals)
        hit_rate_pct[label] = _hit_rate(vals)
        # Excess vs baseline: weight each stack by its asset's baseline
        # mean so an asset that contributed many stacks doesn't bias the
        # comparator toward its baseline disproportionately.
        baseline_means: list[Decimal] = []
        for sr, _ in non_null:
            baseline = baselines.get(sr.stack.asset)
            if baseline is None:
                continue
            asset_mean = baseline.mean_pct.get(label)
            if asset_mean is not None:
                baseline_means.append(asset_mean)
        if baseline_means:
            weighted_baseline = _mean(baseline_means)
            if weighted_baseline is not None and mean_pct[label] is not None:
                mean_excess_pct[label] = mean_pct[label] - weighted_baseline
            else:
                mean_excess_pct[label] = None
        else:
            mean_excess_pct[label] = None

    return StackStratumStats(
        stratum_key=stratum_key,
        n_stacks=n,
        horizons=frozenset(sorted(sr.stack.horizon for sr in stack_returns)),
        n_evaluable=n_evaluable,
        mean_pct=mean_pct,
        median_pct=median_pct,
        hit_rate_pct=hit_rate_pct,
        mean_excess_pct=mean_excess_pct,
    )


# ---------------------------------------------------------------------------
# Stratifiers
# ---------------------------------------------------------------------------


def stratify_by_rule(
    stack_returns: Sequence[StackReturns],
    baselines: dict[str, BaselineStats],
    *,
    windows: Sequence[tuple[str, int, int]] = STACK_WINDOWS,
) -> list[StackStratumStats]:
    by_rule: dict[str, list[StackReturns]] = defaultdict(list)
    for sr in stack_returns:
        by_rule[sr.stack.rule_name].append(sr)
    return [
        aggregate_stack_returns(rs, baselines, stratum_key=rule, windows=windows)
        for rule, rs in sorted(by_rule.items())
    ]


def stratify_by_direction(
    stack_returns: Sequence[StackReturns],
    baselines: dict[str, BaselineStats],
    *,
    windows: Sequence[tuple[str, int, int]] = STACK_WINDOWS,
) -> list[StackStratumStats]:
    by_direction: dict[str, list[StackReturns]] = defaultdict(list)
    for sr in stack_returns:
        by_direction[sr.stack.direction].append(sr)
    return [
        aggregate_stack_returns(rs, baselines, stratum_key=direction, windows=windows)
        for direction, rs in sorted(by_direction.items())
    ]


def stratify_by_asset(
    stack_returns: Sequence[StackReturns],
    baselines: dict[str, BaselineStats],
    *,
    windows: Sequence[tuple[str, int, int]] = STACK_WINDOWS,
) -> list[StackStratumStats]:
    by_asset: dict[str, list[StackReturns]] = defaultdict(list)
    for sr in stack_returns:
        by_asset[sr.stack.asset].append(sr)
    return [
        aggregate_stack_returns(rs, baselines, stratum_key=asset, windows=windows)
        for asset, rs in sorted(by_asset.items())
    ]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _date_to_dt(d: date | None, *, end_of_day: bool = False) -> datetime | None:
    if d is None:
        return None
    day_time = (
        time(23, 59, 59, 999999, tzinfo=timezone.utc)
        if end_of_day
        else time(0, 0, tzinfo=timezone.utc)
    )
    return datetime.combine(d, day_time)


def _required_forward_days(
    windows: Sequence[tuple[str, int, int]],
) -> int:
    """Calendar days of forward price history to load past the last stack."""
    return max((max(hi, 0) for _, _, hi in windows), default=0) + PRICE_LOAD_CUSHION_DAYS


def _required_rule_lookback_days(rules: Sequence[CorrelationRule]) -> int:
    return max((rule.window_days for rule in rules), default=0)


def run_backtest(
    *,
    rule: str | None = None,
    direction: str | None = None,
    asset: str | None = None,
    since: date | None = None,
    until: date | None = None,
    windows: Sequence[tuple[str, int, int]] = STACK_WINDOWS,
    rules_path: Path = DEFAULT_RULES_PATH,
) -> tuple[list[StackReturns], dict[str, BaselineStats]]:
    """Top-level orchestrator: load events → detect stacks → load prices →
    compute per-stack returns + per-asset baselines.

    Filter knobs:
      * ``rule`` — scope to one correlation rule.
      * ``direction`` — bullish / bearish / neutral.
      * ``asset`` — single ticker.
      * ``since`` / ``until`` — bound the event ts range (stacks whose
        ``window_end`` falls in the range).

    Returns ``(stack_returns, baselines)`` so the CLI can aggregate
    multiple stratifications without re-running the underlying queries.
    """
    rules = load_rules(rules_path)
    if rule:
        rules = [r for r in rules if r.name == rule]
    query_since = (
        since - timedelta(days=_required_rule_lookback_days(rules))
        if since is not None
        else None
    )
    events = query_events(
        asset=asset,
        since=_date_to_dt(query_since),
        until=_date_to_dt(until, end_of_day=True),
    )
    stacks = detect_stacks(events, rules)
    if since:
        since_dt = _date_to_dt(since)
        stacks = [s for s in stacks if since_dt is not None and s.window_end >= since_dt]
    if until:
        until_dt = _date_to_dt(until, end_of_day=True)
        stacks = [s for s in stacks if until_dt is not None and s.window_end <= until_dt]
    if direction:
        stacks = [s for s in stacks if s.direction == direction]

    if not stacks:
        return [], {}

    # Load each asset's price series once and share it between the
    # stack-returns pass and the baseline pass — the two passes use
    # different event dates (stack return anchors vs sampled dates) but
    # the same underlying ticker data.
    forward_days = _required_forward_days(windows)
    by_asset: dict[str, list[Stack]] = defaultdict(list)
    for stack in stacks:
        by_asset[stack.asset].append(stack)

    prices_by_asset: dict[str, list[PricePoint]] = {}
    baselines: dict[str, BaselineStats] = {}
    for asset_name, asset_stacks in by_asset.items():
        first = min(_stack_return_anchor_date(s) for s in asset_stacks)
        last = max(_stack_return_anchor_date(s) for s in asset_stacks)
        prices = load_price_series(
            asset_name,
            since=first - timedelta(days=PRICE_LOAD_LOOKBACK_DAYS),
            until=last + timedelta(days=forward_days),
        )
        prices_by_asset[asset_name] = prices
        baselines[asset_name] = compute_baseline(asset_name, prices, windows=windows)

    stack_returns = compute_stack_returns(stacks, prices_by_asset, windows=windows)
    return stack_returns, baselines
