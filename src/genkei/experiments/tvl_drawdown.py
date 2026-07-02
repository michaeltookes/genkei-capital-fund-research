"""TVL drawdown early-warning experiment (B-058).

Phase 5 experiment that asks: *does on-chain TVL change predict future
price drawdowns?* Unblocked by B-035 (long-history crypto prices on
Coinbase Exchange) — until B-035 landed, prices had only 377 days of
depth and any OOS validation sat inside a single macro regime.

**Question framing.** Concrete operational question — given today's
TVL features (change over 7d / 30d / 90d, current drawdown from
trailing peak, z-score vs trailing 90d distribution), predict
whether the chain's native token will fall by ≥X% within the next
N days. Default: 15% drawdown within 30 days.

**Why a rule-based baseline, not logistic regression.** The acceptance
criteria says "logistic or simple ML baseline." We ship a rule-based
threshold classifier rather than logistic regression because:

  1. The project's experiment-module pattern (B-062, B-065, B-090,
     B-059) is pure-Python rule-based with no sklearn/numpy/pandas
     dependency. A logistic-regression baseline would either pull
     scientific-Python deps (scope creep) or hand-roll the optimizer
     (fragile + unmotivated).
  2. With ~3,000 daily observations per chain and 4 chains, a hand-
     rolled threshold classifier is interpretable + sufficient to
     answer the directional question. Logistic regression's value
     (calibrated probabilities, regularization, feature
     interactions) doesn't pay off at this sample size and
     interpretability cost.
  3. The OOS evaluation discipline — time-based train/test split,
     precision / recall / AUC vs base rate — is what matters for the
     scientific question. The classifier form is incidental.

**Chain → token mapping.** The natural join is chain TVL ↔ chain's
native token:

  * Ethereum TVL ↔ ETH-USD
  * Solana TVL  ↔ SOL-USD
  * Sui TVL     ↔ SUI-USD
  * Bitcoin TVL ↔ BTC-USD  (skipped by default — Bitcoin TVL is mostly
                            wrapped BTC + Lightning + Stacks; real BTC
                            price drivers are macro-led, not on-chain
                            DeFi. The lake-loader and classifier work
                            for BTC; the CLI just excludes it.)

**Train / test split.** Time-based (NOT random). Default train end
2024-01-01: train sees 2018 bear / 2020 COVID / 2021 boom / 2022
hiking; test sees 2024-25 bull. A random split would leak future
information into the training set and inflate metrics.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from genkei.common import db

# Default chain → native token mapping. BTC intentionally excluded
# (real price drivers are macro, not on-chain TVL).
DEFAULT_CHAIN_PRODUCT_PAIRS: tuple[tuple[str, str], ...] = (
    ("Ethereum", "ETH-USD"),
    ("Solana", "SOL-USD"),
    ("Sui", "SUI-USD"),
)

# Default classifier + evaluation parameters. Documented in
# docs/experiments/tvl-drawdown.md.
DEFAULT_TRAIN_END = date(2024, 1, 1)
DEFAULT_FORWARD_WINDOW_DAYS = 30
DEFAULT_DRAWDOWN_THRESHOLD_PCT = Decimal("15")
DEFAULT_TVL_CHANGE_30D_THRESHOLD_PCT = Decimal("-10")
DEFAULT_TVL_DRAWDOWN_THRESHOLD_PCT = Decimal("15")
DEFAULT_TVL_ZSCORE_THRESHOLD = Decimal("-1")

# Sustained-drawdown threshold for the emitter's slow-bleed detection (B-095
# follow-up). The B-058 acute classifier above keys on a *90-day* peak, which
# by construction can't see a multi-quarter decline — the reference peak keeps
# resetting downward, so a slow bleed never registers as a large drawdown
# (this is why the emitter went dark 2018→2026 while ETH TVL fell ~60% off its
# 1-year peak). ``tvl_drawdown_from_peak_365d_pct`` measures drawdown from the
# trailing *365-day* peak; a value past this threshold is a sustained stress
# state. Descriptive (not a forward predictor) — the correlator's second stack
# leg (relative-strength laggard) supplies the "still weak" confirmation.
DEFAULT_TVL_SUSTAINED_DRAWDOWN_THRESHOLD_PCT = Decimal("30")
# Minimum trailing observations before a 365-day drawdown is meaningful — below
# this a young series' "peak" is too shallow to call a sustained drawdown.
_MIN_365D_OBSERVATIONS = 90


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AlignedRow:
    """One day's (chain TVL, native token price) observation."""

    ts: date
    tvl_usd: Decimal
    price_usd: Decimal


@dataclass(frozen=True)
class FeatureRow:
    """One day's engineered features + ground-truth label for the experiment.

    ``forward_drawdown_pct`` is the magnitude of the worst drawdown
    that occurs in the [t+1, t+N] window (positive number = price
    fell by that pct from price[t] at some point within the window).
    ``None`` near the end of the series where the lookahead window
    doesn't fully fit.
    """

    ts: date
    tvl_usd: Decimal
    price_usd: Decimal
    tvl_change_7d_pct: Decimal | None
    tvl_change_30d_pct: Decimal | None
    tvl_change_90d_pct: Decimal | None
    tvl_drawdown_from_peak_90d_pct: Decimal | None
    tvl_zscore_90d: Decimal | None
    forward_drawdown_pct: Decimal | None
    # Drawdown from the trailing 365-day peak — the slow-bleed feature the
    # emitter uses (B-095 follow-up). Additive + defaulted so it does not
    # disturb the B-058 acute classifier or its validated results, and so
    # existing FeatureRow constructions keep working unchanged.
    tvl_drawdown_from_peak_365d_pct: Decimal | None = None


@dataclass(frozen=True)
class ClassifierResult:
    """Confusion-matrix-style evaluation of the rule-based classifier."""

    chain: str
    product: str
    period_start: date
    period_end: date
    days_evaluated: int
    base_rate_pct: Decimal      # share of days where target == 1
    signal_rate_pct: Decimal    # share of days where the rule fires
    precision_pct: Decimal      # of days the rule fires, what share are true positives
    recall_pct: Decimal         # of true-positive days, what share does the rule catch
    lift: Decimal               # precision / base_rate — > 1 means the rule beats the base rate
    true_positives: int
    false_positives: int
    true_negatives: int
    false_negatives: int


# ---------------------------------------------------------------------------
# Pure: feature engineering
# ---------------------------------------------------------------------------


def _pct_change(now: Decimal, prior: Decimal | None) -> Decimal | None:
    if prior is None or prior == 0:
        return None
    return Decimal("100") * (now - prior) / prior


def _max(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return max(values)


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, Decimal("0")) / Decimal(len(values))


def _stddev(values: Sequence[Decimal], mean: Decimal) -> Decimal | None:
    """Sample stddev (n-1 denominator). Returns None for n<2."""
    if len(values) < 2:
        return None
    diffs_sq = sum(((v - mean) ** 2 for v in values), Decimal("0"))
    variance = diffs_sq / Decimal(len(values) - 1)
    # Decimal.sqrt() isn't available; use a Newton iteration on the
    # variance for a deterministic result. Two iterations are plenty
    # at the magnitudes we deal with.
    if variance <= 0:
        return Decimal("0")
    guess = variance / Decimal("2")
    for _ in range(20):
        guess = (guess + variance / guess) / Decimal("2")
    return guess


def engineer_features(
    aligned: Sequence[AlignedRow],
    *,
    forward_window_days: int = DEFAULT_FORWARD_WINDOW_DAYS,
) -> list[FeatureRow]:
    """Compute per-day features + the forward drawdown label.

    Pure function — no DB, no I/O. Inputs are date-ascending; output
    is the same length with features filled in where the lookback /
    lookahead windows have enough data.
    """
    if forward_window_days <= 0:
        raise ValueError("forward_window_days must be a positive integer")
    if not aligned:
        return []
    dates = [row.ts for row in aligned]
    out: list[FeatureRow] = []

    def row_at_or_before(current: date, days_back: int) -> AlignedRow | None:
        target = current - timedelta(days=days_back)
        prior_index = bisect_right(dates, target) - 1
        return aligned[prior_index] if prior_index >= 0 else None

    for i, row in enumerate(aligned):
        tvl_7d_row = row_at_or_before(row.ts, 7)
        tvl_30d_row = row_at_or_before(row.ts, 30)
        tvl_90d_row = row_at_or_before(row.ts, 90)
        tvl_7d = tvl_7d_row.tvl_usd if tvl_7d_row is not None else None
        tvl_30d = tvl_30d_row.tvl_usd if tvl_30d_row is not None else None
        tvl_90d = tvl_90d_row.tvl_usd if tvl_90d_row is not None else None

        # Drawdown from trailing 90d peak (positive number = currently
        # below peak). None when we don't have 90 days of history yet.
        lookback_90_start = row.ts - timedelta(days=90)
        lookback_90_start_index = bisect_left(dates, lookback_90_start)
        peak_90d = (
            _max([r.tvl_usd for r in aligned[lookback_90_start_index : i + 1]])
            if tvl_90d_row is not None
            else None
        )
        tvl_drawdown_pct = (
            (Decimal("100") * (peak_90d - row.tvl_usd) / peak_90d)
            if peak_90d is not None and peak_90d != 0
            else None
        )

        # Drawdown from the trailing 365-day peak — the slow-bleed feature.
        # Unlike the 90d peak above, a 1-year window doesn't reset under a
        # multi-quarter decline, so a sustained bleed shows up here. Gated on
        # a minimum observation count so a young series doesn't report a
        # shallow-peak drawdown as "sustained".
        lookback_365_start = row.ts - timedelta(days=365)
        lookback_365_start_index = bisect_left(dates, lookback_365_start)
        window_365 = [r.tvl_usd for r in aligned[lookback_365_start_index : i + 1]]
        peak_365d = _max(window_365) if len(window_365) >= _MIN_365D_OBSERVATIONS else None
        tvl_drawdown_365_pct = (
            (Decimal("100") * (peak_365d - row.tvl_usd) / peak_365d)
            if peak_365d is not None and peak_365d != 0
            else None
        )

        # 90d z-score: where does today's TVL sit in the trailing 90d
        # distribution? Negative = unusually low.
        window = [r.tvl_usd for r in aligned[lookback_90_start_index : i + 1]]
        zscore = None
        if len(window) >= 30:
            mu = _mean(window)
            sigma = _stddev(window, mu) if mu is not None else None
            if mu is not None and sigma is not None and sigma > 0:
                zscore = (row.tvl_usd - mu) / sigma

        # Forward drawdown: worst (largest) pct drop from row.price_usd
        # within the next forward_window_days days. None if the window
        # doesn't fit.
        forward_end = row.ts + timedelta(days=forward_window_days)
        future_end_index = bisect_right(dates, forward_end) - 1
        if dates[-1] >= forward_end and future_end_index > i:
            future_min = min(r.price_usd for r in aligned[i + 1 : future_end_index + 1])
            forward_drawdown_pct = (
                max(
                    Decimal("0"),
                    Decimal("100") * (row.price_usd - future_min) / row.price_usd,
                )
                if row.price_usd > 0
                else None
            )
        else:
            forward_drawdown_pct = None

        out.append(
            FeatureRow(
                ts=row.ts,
                tvl_usd=row.tvl_usd,
                price_usd=row.price_usd,
                tvl_change_7d_pct=_pct_change(row.tvl_usd, tvl_7d),
                tvl_change_30d_pct=_pct_change(row.tvl_usd, tvl_30d),
                tvl_change_90d_pct=_pct_change(row.tvl_usd, tvl_90d),
                tvl_drawdown_from_peak_90d_pct=tvl_drawdown_pct,
                tvl_zscore_90d=zscore,
                forward_drawdown_pct=forward_drawdown_pct,
                tvl_drawdown_from_peak_365d_pct=tvl_drawdown_365_pct,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Pure: classifier
# ---------------------------------------------------------------------------


def classifier_fires(
    row: FeatureRow,
    *,
    tvl_change_30d_threshold_pct: Decimal = DEFAULT_TVL_CHANGE_30D_THRESHOLD_PCT,
    tvl_drawdown_threshold_pct: Decimal = DEFAULT_TVL_DRAWDOWN_THRESHOLD_PCT,
    tvl_zscore_threshold: Decimal = DEFAULT_TVL_ZSCORE_THRESHOLD,
) -> bool:
    """Rule-based classifier: predict 'price drawdown likely' iff *all three*
    TVL stress indicators fire.

    The three-condition AND is deliberate. Each condition alone has
    high false-positive rate in bull markets (TVL pct-change drops on
    any short consolidation). Requiring all three to align gives the
    rule meaningful selectivity.
    """
    if (
        row.tvl_change_30d_pct is None
        or row.tvl_drawdown_from_peak_90d_pct is None
        or row.tvl_zscore_90d is None
    ):
        return False
    return (
        row.tvl_change_30d_pct < tvl_change_30d_threshold_pct
        and row.tvl_drawdown_from_peak_90d_pct > tvl_drawdown_threshold_pct
        and row.tvl_zscore_90d < tvl_zscore_threshold
    )


def sustained_drawdown_fires(
    row: FeatureRow,
    *,
    threshold_pct: Decimal = DEFAULT_TVL_SUSTAINED_DRAWDOWN_THRESHOLD_PCT,
) -> bool:
    """Descriptive slow-bleed check: is TVL meaningfully below its 1-year peak?

    Single-condition (drawdown from the trailing 365-day peak past
    ``threshold_pct``), complementary to the three-condition acute
    ``classifier_fires``. Where the acute rule catches fast crashes (30d
    change + 90d-peak drawdown + z-score all aligned), this catches the
    multi-quarter grind the 90-day windows are blind to. Returns False when
    the 365d feature is absent (series too young — see ``_MIN_365D_OBSERVATIONS``).
    """
    value = row.tvl_drawdown_from_peak_365d_pct
    return value is not None and value > threshold_pct


# ---------------------------------------------------------------------------
# Pure: evaluator
# ---------------------------------------------------------------------------


def evaluate(
    features: Sequence[FeatureRow],
    *,
    chain: str,
    product: str,
    period_start: date,
    period_end: date,
    drawdown_threshold_pct: Decimal = DEFAULT_DRAWDOWN_THRESHOLD_PCT,
    tvl_change_30d_threshold_pct: Decimal = DEFAULT_TVL_CHANGE_30D_THRESHOLD_PCT,
    tvl_drawdown_threshold_pct: Decimal = DEFAULT_TVL_DRAWDOWN_THRESHOLD_PCT,
    tvl_zscore_threshold: Decimal = DEFAULT_TVL_ZSCORE_THRESHOLD,
) -> ClassifierResult:
    """Score the classifier against the forward-drawdown ground truth.

    Drops rows with missing forward_drawdown_pct (the lookahead window
    didn't fit) or missing features (the lookback didn't fit). The
    remaining rows are the evaluable set; we report precision, recall,
    and lift (precision / base_rate) so a reader can see at a glance
    whether the rule beats picking at random.
    """
    evaluable = [
        r
        for r in features
        if r.ts >= period_start
        and r.ts <= period_end
        and r.forward_drawdown_pct is not None
        and r.tvl_change_30d_pct is not None
        and r.tvl_drawdown_from_peak_90d_pct is not None
        and r.tvl_zscore_90d is not None
    ]
    n = len(evaluable)
    if n == 0:
        return ClassifierResult(
            chain=chain,
            product=product,
            period_start=period_start,
            period_end=period_end,
            days_evaluated=0,
            base_rate_pct=Decimal("0"),
            signal_rate_pct=Decimal("0"),
            precision_pct=Decimal("0"),
            recall_pct=Decimal("0"),
            lift=Decimal("0"),
            true_positives=0,
            false_positives=0,
            true_negatives=0,
            false_negatives=0,
        )

    tp = fp = tn = fn = 0
    for r in evaluable:
        positive = r.forward_drawdown_pct >= drawdown_threshold_pct
        fires = classifier_fires(
            r,
            tvl_change_30d_threshold_pct=tvl_change_30d_threshold_pct,
            tvl_drawdown_threshold_pct=tvl_drawdown_threshold_pct,
            tvl_zscore_threshold=tvl_zscore_threshold,
        )
        if positive and fires:
            tp += 1
        elif positive and not fires:
            fn += 1
        elif not positive and fires:
            fp += 1
        else:
            tn += 1

    positives = tp + fn
    fires_count = tp + fp
    base_rate = Decimal("100") * Decimal(positives) / Decimal(n)
    signal_rate = Decimal("100") * Decimal(fires_count) / Decimal(n)
    precision = (
        Decimal("100") * Decimal(tp) / Decimal(fires_count)
        if fires_count > 0
        else Decimal("0")
    )
    recall = (
        Decimal("100") * Decimal(tp) / Decimal(positives) if positives > 0 else Decimal("0")
    )
    lift = precision / base_rate if base_rate > 0 else Decimal("0")

    return ClassifierResult(
        chain=chain,
        product=product,
        period_start=period_start,
        period_end=period_end,
        days_evaluated=n,
        base_rate_pct=base_rate,
        signal_rate_pct=signal_rate,
        precision_pct=precision,
        recall_pct=recall,
        lift=lift,
        true_positives=tp,
        false_positives=fp,
        true_negatives=tn,
        false_negatives=fn,
    )


# ---------------------------------------------------------------------------
# Lake loader
# ---------------------------------------------------------------------------


def load_aligned_series(
    chain: str,
    product: str,
    *,
    since: date | None = None,
    until: date | None = None,
) -> list[AlignedRow]:
    """Join ``defillama.chain_tvl`` and ``coinbase.candles`` on the same date.

    Returns rows ascending by date. Inner join — a date appears only
    when BOTH TVL and price are present (so the feature engineer can
    walk the array without nullability concerns). Pre-listing windows
    for the chain or the product are silently absent from the output.
    """
    sql = """
        SELECT t.ts::date AS d, t.tvl_usd::numeric, c.close::numeric
        FROM defillama.chain_tvl t
        JOIN coinbase.candles c
          ON c.product = %s AND c.ts::date = t.ts::date
        WHERE t.chain = %s
          AND t.tvl_usd IS NOT NULL
          AND c.close IS NOT NULL
    """
    params: list[Any] = [product, chain]
    if since is not None:
        sql += " AND t.ts::date >= %s"
        params.append(since)
    if until is not None:
        sql += " AND t.ts::date <= %s"
        params.append(until)
    sql += " ORDER BY t.ts::date ASC"

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [AlignedRow(ts=d, tvl_usd=tvl, price_usd=price) for d, tvl, price in rows]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_chain_evaluation(
    chain: str,
    product: str,
    *,
    train_end: date = DEFAULT_TRAIN_END,
    forward_window_days: int = DEFAULT_FORWARD_WINDOW_DAYS,
    drawdown_threshold_pct: Decimal = DEFAULT_DRAWDOWN_THRESHOLD_PCT,
    until: date | None = None,
) -> tuple[ClassifierResult, ClassifierResult]:
    """Run the experiment for one (chain, product) pair. Returns (train, test) results."""
    if forward_window_days <= 0:
        raise ValueError("forward_window_days must be a positive integer")
    aligned = load_aligned_series(chain, product, until=until)
    features = engineer_features(aligned, forward_window_days=forward_window_days)
    if not aligned:
        empty = ClassifierResult(
            chain=chain,
            product=product,
            period_start=date(2000, 1, 1),
            period_end=date(2000, 1, 1),
            days_evaluated=0,
            base_rate_pct=Decimal("0"),
            signal_rate_pct=Decimal("0"),
            precision_pct=Decimal("0"),
            recall_pct=Decimal("0"),
            lift=Decimal("0"),
            true_positives=0,
            false_positives=0,
            true_negatives=0,
            false_negatives=0,
        )
        return empty, empty
    series_start = aligned[0].ts
    series_end = aligned[-1].ts
    train = evaluate(
        features,
        chain=chain,
        product=product,
        period_start=series_start,
        period_end=min(train_end - timedelta(days=forward_window_days), series_end),
        drawdown_threshold_pct=drawdown_threshold_pct,
    )
    test = evaluate(
        features,
        chain=chain,
        product=product,
        period_start=train_end + timedelta(days=1),
        period_end=series_end,
        drawdown_threshold_pct=drawdown_threshold_pct,
    )
    return train, test
