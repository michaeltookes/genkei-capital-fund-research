"""Macro regime classifier (B-059).

Phase 5 experiment: bucket every business day into one of five regime
labels — ``risk_on`` / ``risk_off`` / ``easing`` / ``tightening_stress``
/ ``mixed`` — derived from four FRED daily series:

  * **DGS10** — 10y Treasury yield (rate regime)
  * **BAMLH0A0HYM2** — ICE BofA HY OAS (credit-spread regime)
  * **VIXCLS** — VIX (equity-vol regime)
  * **DTWEXBGS** — Broad USD index (FX regime)

Two roles in this module:

  * **Pure function** (``classify``) takes the four current values + the
    30d-prior DGS10 / HY / USD values and returns a ``RegimeResult``.
    Same thresholds as the SQL view in
    ``migrations/versions/20260522_create_analytics_macro_regime.py``,
    so a cross-check test can verify the Python and SQL agree on real
    rows from the lake.
  * **Lake-loading helper** (``load_regimes``) queries the
    ``analytics.macro_regime_per_date`` view directly. The CLI
    composes the loader's output into human + JSON formats.

Design choices:

  * **Rule-based, not ML.** The acceptance criteria says "logistic or
    simple ML baseline"; we ship a deterministic threshold classifier
    instead. Same outputs (regime label per date), same evaluation
    surface (compare against later asset returns to score the
    classifier), no scientific-Python dependency, fully testable.
  * **Priority-ordered labels.** Regimes are mutually exclusive. A
    date that satisfies both "rates falling fast" and "HY tight" gets
    the more-actionable label (``tightening_stress`` >
    ``risk_off`` > ``easing`` > ``risk_on`` > ``mixed``). Priority
    encodes "if this signal is firing, it dominates anything else."
  * **Available-input degradation.** Pre-2023 data has no HY OAS;
    pre-2006 has no USD index. With <3 of 4 inputs, the classifier
    returns ``mixed`` rather than guessing — better to be honest
    about the missing context than to extrapolate from too little.

Thresholds (kept aligned with B-065's ``score_macro_regime`` for
interpretability — same conceptual buckets, just labeled rather than
summed):

  * HY OAS < 3.5%  → tight       (bull)
  * HY OAS > 5.0%  → wide        (bear)
  * VIX < 18       → benign      (bull)
  * VIX > 25       → elevated    (bear)
  * DGS10 +0.30pp over 30d → rising
  * DGS10 -0.30pp over 30d → falling moderately
  * DGS10 -0.50pp over 30d → falling significantly (easing)
  * USD -1.0 over 30d → weakening (bull-for-risk)
  * USD +1.0 over 30d → strengthening (bear-for-risk)
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from genkei.common import db

# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


REGIME_LABELS: tuple[str, ...] = (
    "tightening_stress",
    "risk_off",
    "easing",
    "risk_on",
    "mixed",
)


@dataclass(frozen=True)
class RegimeInputs:
    """One row of the wide macro-input grid that feeds the classifier.

    Values may be ``None`` when a series doesn't have coverage on the
    given date (BAMLH0A0HYM2 pre-2023, DTWEXBGS pre-2006). The
    classifier handles missing inputs by degrading to ``mixed`` rather
    than extrapolating.
    """

    ts: date
    dgs10: Optional[Decimal]
    hy_oas: Optional[Decimal]
    vix: Optional[Decimal]
    usd_index: Optional[Decimal]
    dgs10_30d_ago: Optional[Decimal]
    hy_oas_30d_ago: Optional[Decimal]
    usd_index_30d_ago: Optional[Decimal]


@dataclass(frozen=True)
class RegimeResult:
    """The classifier's output for one date."""

    ts: date
    regime: str
    available_inputs: int
    # The same shape the SQL view emits, so a Python-vs-SQL parity
    # test can compare row-by-row.
    dgs10: Optional[Decimal]
    dgs10_30d_change: Optional[Decimal]
    hy_oas: Optional[Decimal]
    hy_oas_30d_change: Optional[Decimal]
    vix: Optional[Decimal]
    usd_index: Optional[Decimal]
    usd_index_30d_change: Optional[Decimal]


# ---------------------------------------------------------------------------
# Pure classifier
# ---------------------------------------------------------------------------


def _delta(now: Optional[Decimal], prior: Optional[Decimal]) -> Optional[Decimal]:
    if now is None or prior is None:
        return None
    return now - prior


def classify(inputs: RegimeInputs) -> RegimeResult:
    """Apply the rule-based classifier to one ``RegimeInputs`` row."""
    dgs10_chg = _delta(inputs.dgs10, inputs.dgs10_30d_ago)
    hy_chg = _delta(inputs.hy_oas, inputs.hy_oas_30d_ago)
    usd_chg = _delta(inputs.usd_index, inputs.usd_index_30d_ago)

    available = sum(
        1
        for v in (inputs.dgs10, inputs.hy_oas, inputs.vix, inputs.usd_index)
        if v is not None
    )

    regime: str
    if available < 3:
        regime = "mixed"
    elif (
        dgs10_chg is not None
        and dgs10_chg > Decimal("0.3")
        and hy_chg is not None
        and hy_chg > Decimal("0.3")
        and inputs.vix is not None
        and inputs.vix > Decimal("25")
    ):
        regime = "tightening_stress"
    elif (inputs.hy_oas is not None and inputs.hy_oas > Decimal("5.0")) or (
        inputs.vix is not None and inputs.vix > Decimal("25")
    ):
        regime = "risk_off"
    elif dgs10_chg is not None and dgs10_chg < Decimal("-0.5"):
        regime = "easing"
    else:
        # risk_on composite: ≥ 2 bullish inputs present
        bullish_count = 0
        if inputs.hy_oas is not None and inputs.hy_oas < Decimal("3.5"):
            bullish_count += 1
        if inputs.vix is not None and inputs.vix < Decimal("18"):
            bullish_count += 1
        if usd_chg is not None and usd_chg < Decimal("-1"):
            bullish_count += 1
        if dgs10_chg is not None and dgs10_chg < Decimal("-0.3"):
            bullish_count += 1
        regime = "risk_on" if bullish_count >= 2 else "mixed"

    return RegimeResult(
        ts=inputs.ts,
        regime=regime,
        available_inputs=available,
        dgs10=inputs.dgs10,
        dgs10_30d_change=dgs10_chg,
        hy_oas=inputs.hy_oas,
        hy_oas_30d_change=hy_chg,
        vix=inputs.vix,
        usd_index=inputs.usd_index,
        usd_index_30d_change=usd_chg,
    )


# ---------------------------------------------------------------------------
# Lake loader
# ---------------------------------------------------------------------------


def load_regimes(
    *,
    since: Optional[date] = None,
    until: Optional[date] = None,
    limit: Optional[int] = None,
) -> list[RegimeResult]:
    """Query ``analytics.macro_regime_per_date`` for a date range.

    Default returns the full available history; ``since`` / ``until``
    bound it; ``limit`` caps row count (ordered ``ts DESC``).
    """
    where_clauses: list[str] = []
    params: list[Any] = []
    if since is not None:
        where_clauses.append("ts >= %s")
        params.append(since)
    if until is not None:
        where_clauses.append("ts <= %s")
        params.append(until)
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    sql = f"""
        SELECT ts, regime, available_inputs,
               dgs10, dgs10_30d_change,
               hy_oas, hy_oas_30d_change,
               vix,
               usd_index, usd_index_30d_change
        FROM analytics.macro_regime_per_date
        {where_sql}
        ORDER BY ts DESC
    """
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    return [
        RegimeResult(
            ts=ts,
            regime=regime,
            available_inputs=avail,
            dgs10=dgs10,
            dgs10_30d_change=dgs10_chg,
            hy_oas=hy,
            hy_oas_30d_change=hy_chg,
            vix=vix,
            usd_index=usd,
            usd_index_30d_change=usd_chg,
        )
        for (
            ts,
            regime,
            avail,
            dgs10,
            dgs10_chg,
            hy,
            hy_chg,
            vix,
            usd,
            usd_chg,
        ) in rows
    ]


# ---------------------------------------------------------------------------
# Convenience: regime distribution summary
# ---------------------------------------------------------------------------


def summarize(results: Sequence[RegimeResult]) -> dict[str, int]:
    """Return ``{regime_label: count}`` over the given range.

    Useful for ``genkei macro-regime --summary`` and for the doc's
    "what does the distribution look like" question. Always emits all
    five known labels with zero-counts so consumers don't need to
    handle missing keys.
    """
    counts: dict[str, int] = dict.fromkeys(REGIME_LABELS, 0)
    for r in results:
        counts[r.regime] = counts.get(r.regime, 0) + 1
    return counts
