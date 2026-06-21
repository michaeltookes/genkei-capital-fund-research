"""Macro-regime → signal_events emitter (B-096).

Adapts the B-059 macro-regime classifier (``analytics.macro_regime_per_date``,
loaded via ``macro_regime.load_regimes``) into atomic signal events for the
cross-source correlator. The classifier labels *every* business day with a
regime; the engine wants **transitions**, not continuous daily state, so this
emitter de-dupes within a regime run and fires exactly one event on the
boundary day where the label changes.

Each transition becomes one ``meta.signal_events`` row:

* ``asset``       = ``"MACRO"`` — a market-wide sentinel. A macro regime is
                    not an equity/crypto/protocol, so it gets its own
                    ``asset_class='macro'`` (added in the 20260621 migration)
                    rather than being mislabeled. Equities and crypto are
                    *downstream* of macro (CLAUDE.md), so the regime is a
                    cross-sleeve overlay, not a per-ticker signal.
* ``asset_class`` = ``"macro"``.
* ``ts``          = the transition (boundary) date at UTC midnight.
* ``source``      = ``"macro_regime"``.
* ``signal_kind`` = the *new* regime label (``risk_off`` / ``easing`` / …) so
                    a correlation rule can target a specific regime entry.
* ``direction``   = inferred from the new regime: ``risk_on`` / ``easing`` →
                    bullish; ``risk_off`` / ``tightening_stress`` → bearish;
                    ``mixed`` → neutral.
* ``strength``    = ``None`` — a regime label has no natural 0-1 intensity
                    axis (the correlator defaults missing strength to 1.0).
* ``horizon``     = the classifier's horizon tag (``macro:cross-sleeve:primary``).
* ``source_ref``  = ``"<ts_iso>:<new_regime>"`` — the natural transition key,
                    making re-emission idempotent via the
                    ``(asset, ts, source, signal_kind, source_ref, horizon)``
                    UNIQUE constraint.
* ``payload``     = the from→to labels plus the metric snapshot driving the
                    transition (rates / HY OAS / VIX / USD + their 30d deltas).

Run as one ``meta.ingest_runs`` row tagged ``source='signal_emitter'
endpoint='macro_regime'`` so ``genkei watchlist health`` surfaces staleness
uniformly with the other emitters.

Note on stacks: with only this macro source on the ``MACRO`` sentinel, a
single emitter can't satisfy the correlator's ``min_distinct_sources >= 2``
gate, and a plain rule can't pair macro with per-asset signals (the
correlator groups by asset + filters to one horizon). So macro surfaces as
*context, not a co-signal*: ``signal_macro_overlay.py`` tags each per-asset
stack with the regime in effect at its window_end (corroborates / contradicts
/ neutral) and the digest shows a current-regime header (D-025). Raw events
stay queryable via ``genkei signals --events --asset MACRO``. See
``docs/experiments/cross-source-signals.md``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from genkei.common import db
from genkei.experiments.macro_regime import RegimeResult, load_regimes
from genkei.experiments.signal_store import emit_signals_bulk

EMITTER_SOURCE = "macro_regime"
EMITTER_RUN_TAG = "signal_emitter"
EMITTER_ENDPOINT = "macro_regime"
SENTINEL_ASSET = "MACRO"
ASSET_CLASS = "macro"

# How far before ``since`` to look so a transition landing on the first day of
# the requested window is still detectable (its predecessor row must be loaded
# to compare against). 35 days clears any weekend/holiday gap between two
# business-day regime rows.
BOUNDARY_LOOKBACK_DAYS = 35

# New-regime → directional bias. Regimes absent here map to neutral.
_BULLISH_REGIMES = frozenset({"risk_on", "easing"})
_BEARISH_REGIMES = frozenset({"risk_off", "tightening_stress"})

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RegimeTransition:
    """One regime boundary: the day ``from_regime`` flipped to ``to_regime``."""

    ts: date
    from_regime: str
    to_regime: str
    horizon: str
    result: RegimeResult


@dataclass(frozen=True)
class EmitResult:
    """Return value of ``emit_regime_transitions`` for CLI / test inspection."""

    ingest_run_id: int
    transitions_emitted: int
    days_scanned: int


def direction_for_regime(regime: str) -> str:
    """Map a regime label to a correlator direction.

    Bullish/bearish where the regime carries a clear risk bias; neutral
    for ``mixed`` (and any unknown label) — informational, not directional.
    """
    if regime in _BULLISH_REGIMES:
        return "bullish"
    if regime in _BEARISH_REGIMES:
        return "bearish"
    return "neutral"


def detect_transitions(results_ascending: Sequence[RegimeResult]) -> list[RegimeTransition]:
    """Return one ``RegimeTransition`` per boundary in a ts-ascending series.

    Pure: no DB. A transition is a row whose regime differs from the
    immediately preceding row's. The first row has no predecessor, so it is
    never itself a transition (it anchors the run). Same-regime days emit
    nothing — that's the de-dupe-within-a-run contract.
    """
    transitions: list[RegimeTransition] = []
    prev: RegimeResult | None = None
    for current in results_ascending:
        if prev is not None and current.regime != prev.regime:
            transitions.append(
                RegimeTransition(
                    ts=current.ts,
                    from_regime=prev.regime,
                    to_regime=current.regime,
                    horizon=current.horizon,
                    result=current,
                )
            )
        prev = current
    return transitions


def _ts_to_datetime(d: date) -> datetime:
    """Boundary *date* → UTC-midnight datetime for the events table."""
    return datetime.combine(d, time(0, 0, tzinfo=timezone.utc))


def _build_event(transition: RegimeTransition) -> dict[str, Any]:
    """Map one transition to a single signal-event dict."""
    r = transition.result
    payload: dict[str, Any] = {
        "from_regime": transition.from_regime,
        "to_regime": transition.to_regime,
        "available_inputs": r.available_inputs,
        "dgs10": str(r.dgs10) if r.dgs10 is not None else None,
        "dgs10_30d_change": (
            str(r.dgs10_30d_change) if r.dgs10_30d_change is not None else None
        ),
        "hy_oas": str(r.hy_oas) if r.hy_oas is not None else None,
        "hy_oas_30d_change": (
            str(r.hy_oas_30d_change) if r.hy_oas_30d_change is not None else None
        ),
        "vix": str(r.vix) if r.vix is not None else None,
        "usd_index": str(r.usd_index) if r.usd_index is not None else None,
        "usd_index_30d_change": (
            str(r.usd_index_30d_change) if r.usd_index_30d_change is not None else None
        ),
    }
    return {
        "asset": SENTINEL_ASSET,
        "asset_class": ASSET_CLASS,
        "horizon": transition.horizon,
        "ts": _ts_to_datetime(transition.ts),
        "source": EMITTER_SOURCE,
        "signal_kind": transition.to_regime,
        "direction": direction_for_regime(transition.to_regime),
        "strength": None,
        "payload": payload,
        "source_ref": f"{transition.ts.isoformat()}:{transition.to_regime}",
    }


def emit_regime_transitions(
    *,
    since: date | None = None,
    until: date | None = None,
) -> EmitResult:
    """Detect macro-regime transitions in the date range and emit signal events.

    Loads with a ``BOUNDARY_LOOKBACK_DAYS`` buffer before ``since`` so a
    transition on the first requested day is detectable, then emits only the
    transitions whose date falls within ``[since, until]``. Wrapped in a single
    ``meta.ingest_runs`` row for uniform provenance / health tracking.
    """
    with db.ingest_run(
        EMITTER_RUN_TAG,
        endpoint=EMITTER_ENDPOINT,
        metadata={
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
        },
    ) as run:
        load_since = since - timedelta(days=BOUNDARY_LOOKBACK_DAYS) if since else None
        results = load_regimes(since=load_since, until=until)
        results_ascending = sorted(results, key=lambda r: r.ts)
        transitions = detect_transitions(results_ascending)
        if since is not None:
            transitions = [t for t in transitions if t.ts >= since]
        events = [_build_event(t) for t in transitions]
        rows_written = emit_signals_bulk(events, ingest_run_id=run.id)
        run.add_rows(rows_written)
        return EmitResult(
            ingest_run_id=run.id,
            transitions_emitted=rows_written,
            days_scanned=len(results_ascending),
        )


def parse_args(argv: list[str]) -> Any:
    import argparse

    parser = argparse.ArgumentParser(
        description="Emit macro-regime transition events into meta.signal_events."
    )
    parser.add_argument("--since", type=date.fromisoformat, default=None)
    parser.add_argument("--until", type=date.fromisoformat, default=None)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    import json as _json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv or sys.argv[1:])
    result = emit_regime_transitions(since=args.since, until=args.until)
    if args.json:
        print(
            _json.dumps(
                {
                    "ingest_run_id": result.ingest_run_id,
                    "transitions_emitted": result.transitions_emitted,
                    "days_scanned": result.days_scanned,
                    "source": EMITTER_SOURCE,
                }
            )
        )
    else:
        print(
            f"macro-regime emitter wrote ingest_run_id={result.ingest_run_id} "
            f"transitions={result.transitions_emitted} days_scanned={result.days_scanned}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
