"""Per-series rolling anomaly detection (B-069).

A pure, DB-free statistics layer: give it a numeric series and it returns the
observations that are statistical outliers against their own recent history.
The runner (``emitters/anomaly_emitter.py``) wires this to the lake's price
series and persists the flags into ``meta.anomalies``; ``genkei anomalies``
reads them back.

Why MAD, not a plain z-score. The classic z-score ``(x - mean) / std`` is
itself wrecked by the very thing we're hunting — a couple of big spikes
inflate the mean *and* the std, so the window that contains an outlier
under-reports the next one (masking). The robust alternative is the
**modified z-score** (Iglewicz & Hoaglin): ``0.6745 * (x - median) / MAD``,
where ``MAD`` is the median absolute deviation from the median. Median and
MAD have a ~50% breakdown point, so a handful of outliers in the trailing
window barely move them. The 0.6745 constant makes the modified z-score
comparable in scale to a standard z on normal data (it's ``1 / Φ⁻¹(0.75)``),
so the conventional 3.5 cutoff carries over.

The one degenerate case: a window flat enough that ``MAD == 0`` (e.g. a run
of identical closes → identical zero returns). The modified z-score divides
by zero there, so we fall back to the classic mean/std z-score for that point
and tag it ``method="zscore"``; if the window is *perfectly* flat
(``std == 0`` too) there is nothing to be anomalous against and the point is
skipped.

The window is the ``window`` observations **strictly before** each point — a
point is judged against its past, never against itself — and a point needs at
least ``min_window`` prior observations before it's eligible at all (so the
first stretch of any series, where the estimate is unstable, produces no
flags).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

# Iglewicz–Hoaglin scale constant: 0.6745 = Φ⁻¹(0.75), which puts the
# MAD-based modified z-score on the same scale as a standard z-score on
# normally distributed data.
MODIFIED_ZSCORE_SCALE = Decimal("0.6745")

# Iglewicz–Hoaglin's recommended modified-z outlier cutoff.
DEFAULT_THRESHOLD = Decimal("3.5")
DEFAULT_WINDOW = 90
DEFAULT_MIN_WINDOW = 30


@dataclass(frozen=True)
class SeriesPoint:
    """One ``(date, value)`` observation of a numeric series."""

    ts: date
    value: Decimal


@dataclass(frozen=True)
class Anomaly:
    """One flagged outlier, with the rolling stats that judged it."""

    ts: date
    value: Decimal
    score: Decimal  # signed modified-z (or z fallback); sign gives direction
    method: str  # 'modified_zscore' | 'zscore'
    direction: str  # 'spike_up' | 'spike_down'
    window: int
    threshold: Decimal
    median: Decimal | None
    mad: Decimal | None


def to_returns(points: list[SeriesPoint]) -> list[SeriesPoint]:
    """Convert a level series to simple daily returns ``(p_t - p_{t-1}) / p_{t-1}``.

    Anomaly detection on *levels* mostly rediscovers the trend (a price
    marching up is not an anomaly); the meaningful "unusual day" signal lives
    in the returns. A non-positive prior price is skipped (can't form a
    return) rather than producing a spurious spike.
    """
    out: list[SeriesPoint] = []
    # noqa B905: paired slices are the same length by construction; strict= is
    # unavailable on the project's Python 3.9 floor.
    for prev, cur in zip(points, points[1:]):  # noqa: B905
        if prev.value <= 0:
            continue
        out.append(SeriesPoint(ts=cur.ts, value=(cur.value - prev.value) / prev.value))
    return out


def _median(values: list[Decimal]) -> Decimal:
    """Median of a non-empty list (no numpy — keep the module Decimal-pure)."""
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / Decimal("2")


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal("0")) / Decimal(len(values))


def _pstdev(values: list[Decimal], mean: Decimal) -> Decimal:
    """Population standard deviation (Decimal ``sqrt`` via the default context)."""
    variance = sum(((v - mean) ** 2 for v in values), Decimal("0")) / Decimal(
        len(values)
    )
    return variance.sqrt()


def _score_point(
    value: Decimal, window_values: list[Decimal]
) -> tuple[Decimal, str, Decimal | None, Decimal | None] | None:
    """Score one value against its trailing window.

    Returns ``(signed_score, method, median, mad)`` or ``None`` when the
    window is perfectly flat (nothing to be anomalous against).
    """
    median = _median(window_values)
    mad = _median([abs(v - median) for v in window_values])
    if mad > 0:
        score = MODIFIED_ZSCORE_SCALE * (value - median) / mad
        return score, "modified_zscore", median, mad
    # MAD degenerated (flat-ish window) → classic z-score fallback.
    mean = _mean(window_values)
    stdev = _pstdev(window_values, mean)
    if stdev > 0:
        return (value - mean) / stdev, "zscore", None, None
    return None


def detect_anomalies(
    points: list[SeriesPoint],
    *,
    window: int = DEFAULT_WINDOW,
    threshold: Decimal = DEFAULT_THRESHOLD,
    min_window: int = DEFAULT_MIN_WINDOW,
) -> list[Anomaly]:
    """Flag observations whose rolling robust outlier score breaches ``threshold``.

    Each point is scored against the up-to-``window`` observations strictly
    before it; points with fewer than ``min_window`` predecessors are not yet
    eligible. Chronological order is assumed (the runner sorts before
    calling).
    """
    out: list[Anomaly] = []
    for i, point in enumerate(points):
        if i < min_window:
            continue
        window_values = [p.value for p in points[max(0, i - window) : i]]
        scored = _score_point(point.value, window_values)
        if scored is None:
            continue
        score, method, median, mad = scored
        if abs(score) < threshold:
            continue
        out.append(
            Anomaly(
                ts=point.ts,
                value=point.value,
                score=score,
                method=method,
                direction="spike_up" if score > 0 else "spike_down",
                window=window,
                threshold=threshold,
                median=median,
                mad=mad,
            )
        )
    return out
