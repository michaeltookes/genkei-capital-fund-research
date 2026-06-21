"""Macro-regime overlay context for per-asset stacks (B-096 follow-up).

The macro-regime emitter (B-096) writes market-wide regime events on a
sentinel ``asset="MACRO"`` under ``horizon="macro:cross-sleeve:primary"``.
The correlator groups events by ``asset`` and filters each rule to a single
``horizon``, so macro events can *never* co-occur with per-asset signals
(AAPL on ``equity:core``, ethereum on ``crypto:core``) the way a stack
requires — a plain correlator rule can't pair them.

So macro is surfaced the way it actually functions in the thesis: as
**context, not a co-signal**. "Macro is the spine; equities and crypto are
downstream of macro" (CLAUDE.md) — the prevailing regime modulates how to
read a per-asset stack, it doesn't fire stacks of its own. A bearish equity
exit during ``risk_off`` is macro-corroborated (tailwind); the same stack
during ``risk_on`` is fighting the tape (headwind). This module computes
that per-stack overlay for the digest.

**Design: presentation layer, not score mutation** — the same choice
``signal_benchmark`` made (B-100). ``detect_stacks`` math stays invariant;
the overlay is an additional column + a current-regime header. The regime is
read *as of each stack's ``window_end``* (the decision point — no lookahead,
consistent with the benchmark column).

Alignment vs the stack's direction:

  * regime bias == stack direction → ``corroborates``
  * regime bias is opposite        → ``contradicts``
  * regime is ``mixed`` (neutral bias) or the stack itself is neutral
                                    → ``neutral``
  * no regime row on-or-before the window (shouldn't happen given coverage)
                                    → ``unknown``

Regime → directional bias reuses the emitter's ``direction_for_regime`` so
the overlay and the emitted events can never disagree on what a regime means.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from genkei.experiments.emitters.macro_regime_emitter import direction_for_regime
from genkei.experiments.macro_regime import RegimeResult, load_regimes
from genkei.experiments.signal_store import Stack

# Days of slack before the earliest stack window so there is always a regime
# row on-or-before the window_end even if it lands on a weekend/holiday gap.
_REGIME_LOOKBACK_DAYS = 10

ALIGNMENT_CORROBORATES = "corroborates"
ALIGNMENT_CONTRADICTS = "contradicts"
ALIGNMENT_NEUTRAL = "neutral"
ALIGNMENT_UNKNOWN = "unknown"


@dataclass(frozen=True)
class StackMacroContext:
    """The macro regime in effect at a stack's window_end + its alignment.

    ``stack_index`` joins back to the parallel input ``stacks`` list so
    callers can render the column without mutating the immutable ``Stack``.
    ``regime`` is None (and ``alignment`` is ``unknown``) when no regime row
    exists on-or-before the window — defensive; current coverage is 2006+.
    """

    stack_index: int
    regime: str | None
    regime_direction: str
    alignment: str
    as_of: date | None


def _dt_to_date(value: datetime) -> date:
    if value.tzinfo is None:
        return value.date()
    return value.astimezone(timezone.utc).date()


def classify_alignment(regime: str | None, stack_direction: str) -> str:
    """Pure: how the regime's bias relates to a stack's direction."""
    if regime is None:
        return ALIGNMENT_UNKNOWN
    regime_direction = direction_for_regime(regime)
    if regime_direction == "neutral" or stack_direction == "neutral":
        return ALIGNMENT_NEUTRAL
    return (
        ALIGNMENT_CORROBORATES
        if regime_direction == stack_direction
        else ALIGNMENT_CONTRADICTS
    )


def _context_for(
    stack_index: int,
    stack: Stack,
    regimes_ascending: Sequence[RegimeResult],
    regime_dates: Sequence[date],
) -> StackMacroContext:
    """Look up the regime as-of ``stack.window_end`` and classify alignment."""
    as_of = _dt_to_date(stack.window_end)
    # Rightmost regime whose ts <= as_of.
    pos = bisect_right(regime_dates, as_of)
    if pos == 0:
        return StackMacroContext(
            stack_index=stack_index,
            regime=None,
            regime_direction="neutral",
            alignment=ALIGNMENT_UNKNOWN,
            as_of=None,
        )
    regime_row = regimes_ascending[pos - 1]
    return StackMacroContext(
        stack_index=stack_index,
        regime=regime_row.regime,
        regime_direction=direction_for_regime(regime_row.regime),
        alignment=classify_alignment(regime_row.regime, stack.direction),
        as_of=regime_row.ts,
    )


def compute_stack_macro_contexts(
    stacks: Sequence[Stack],
) -> list[StackMacroContext]:
    """For each stack, the macro regime in effect at its window_end + alignment.

    Loads the regime series once across the union of stack windows (one DB
    round-trip) and bisects per stack. Returns a list parallel to ``stacks``.
    """
    if not stacks:
        return []
    ends = [_dt_to_date(s.window_end) for s in stacks]
    load_since = min(ends) - timedelta(days=_REGIME_LOOKBACK_DAYS)
    regimes = load_regimes(since=load_since, until=max(ends))
    regimes_ascending = sorted(regimes, key=lambda r: r.ts)
    regime_dates = [r.ts for r in regimes_ascending]
    return [
        _context_for(idx, stack, regimes_ascending, regime_dates)
        for idx, stack in enumerate(stacks)
    ]


def latest_regime() -> RegimeResult | None:
    """The most recent regime row, for the digest's current-regime header."""
    rows = load_regimes(limit=1)
    return rows[0] if rows else None
