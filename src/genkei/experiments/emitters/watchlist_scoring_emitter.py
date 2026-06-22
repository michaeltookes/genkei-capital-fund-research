"""Watchlist-scoring → signal_events emitter (B-097).

Sixth emitter wired into the cross-source correlator (B-064) and the last
of the B-064 follow-ups. Adapts B-065's composite scoring rubric
(``meta.signals``) into atomic band-crossing events.

**What the signal captures.** The rubric collapses every single-source signal
on an asset into one daily ``composite_score`` (additive, roughly -8..+8 —
see ``watchlist_scoring``). This emitter watches that score per asset and
fires when it *crosses into* a conviction band: the top band (a strong
multi-component bullish read) or the bottom band (multi-component bearish).
It's the synthesis source — where the other emitters surface one dimension
(insider flow, rel-strength, TVL …), a band entry says "the whole rubric
agrees this asset is notably positioned."

**Band model with hysteresis.** A naive single threshold re-fires every time
a score wobbles across the boundary. So the band uses *separate enter/exit
thresholds* (a dead-band): a score enters the bullish band at
``ENTER_BULLISH`` (+4) and only leaves it once it falls back below
``EXIT_BULLISH`` (+2); symmetrically ``ENTER_BEARISH`` (-4) /
``EXIT_BEARISH`` (-2). A three-state machine (bullish / neutral / bearish)
tracks each asset's prior state; only a transition *into* the bullish or
bearish band emits an event. Transitions back to neutral are silent, and a
score oscillating between, say, +3 and +5 stays ``bullish`` the whole time
and emits exactly once. This is the "transition not state" precedent the
tvl_drawdown / relative_strength emitters set, hardened with a dead-band so
boundary noise can't re-emit.

**Thresholds.** ±4 enter / ±2 exit on a -8..+8 additive rubric — ±4 means
"about half the per-side range / several components strongly aligned," which
is selective enough to be a real conviction read. Tunable; the constants
live in this module so a re-tune shows up in git blame here (matching the
precedent of the prior emitters).

**Strength.** ``min(abs(composite_score) / STRENGTH_SATURATION, 1.0)`` with
an 8-point saturation (the rubric's per-side ceiling): a +4 band entry →
0.5, a +8 max-conviction score → 1.0.

Field mapping per event:

* ``asset``         = the ``meta.signals`` asset (equity ticker / crypto
                      coingecko_id), matching the identifiers the other
                      emitters use.
* ``asset_class``   = ``"equity"`` or ``"crypto"`` (carried on the score row).
* ``ts``            = the band-entry date at UTC midnight.
* ``source``        = ``"watchlist_scoring"``.
* ``signal_kind``   = ``"bullish_band_entry"`` or ``"bearish_band_entry"``.
* ``direction``     = ``"bullish"`` / ``"bearish"`` from the band.
* ``strength``      = saturating ramp on ``abs(composite_score)``.
* ``horizon``       = the score row's sleeve rendered as a horizon tag
                      (``equity-core`` → ``equity:core``, ``crypto-tactical``
                      → ``crypto:tactical``).
* ``source_ref``    = ``"<asset>:<rubric_version>:<kind>:<entry_iso>"`` —
                      natural key of the band entry; the UNIQUE constraint on
                      ``(asset, ts, source, signal_kind, source_ref, horizon)``
                      makes re-emission idempotent.

Scope: reads one ``rubric_version`` (default the current ``v1``) so mixed
score scales never compare. Loads the full score history per asset
regardless of ``--since`` (the band state machine needs the prior day's
state); ``--since`` filters the *emission* window after entries are detected,
mirroring the relative_strength emitter.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any

from genkei.cli._helpers import json_default as _json_default
from genkei.cli._helpers import parse_date as _parse_date
from genkei.common import db
from genkei.experiments.signal_store import emit_signals_bulk
from genkei.experiments.watchlist_scoring import RUBRIC_VERSION

EMITTER_SOURCE = "watchlist_scoring"
EMITTER_RUN_TAG = "signal_emitter"
EMITTER_ENDPOINT = "watchlist_scoring"

# Hysteretic band edges on the additive -8..+8 composite rubric. Enter a
# conviction band at ±4; only leave it once the score falls back inside ±2.
# The dead-band between enter and exit is what stops a score oscillating on
# the boundary from re-emitting. Tunable — kept here so a re-tune is visible
# in git blame on this file.
ENTER_BULLISH = Decimal("4")
EXIT_BULLISH = Decimal("2")
ENTER_BEARISH = Decimal("-4")
EXIT_BEARISH = Decimal("-2")

# Strength saturation: abs(score) / 8 clamped to [0, 1]. The rubric's
# per-side ceiling is ~8, so +4 → 0.5 and +8 → 1.0.
STRENGTH_SATURATION = Decimal("8")

STATE_BULLISH = "bullish"
STATE_NEUTRAL = "neutral"
STATE_BEARISH = "bearish"

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScorePoint:
    """One daily composite score for an asset (a ``meta.signals`` row)."""

    asset: str
    asset_class: str
    sleeve: str
    ts: date
    score: Decimal


@dataclass(frozen=True)
class BandEntry:
    """One detected entry into the bullish or bearish conviction band."""

    asset: str
    asset_class: str
    sleeve: str
    ts: date
    kind: str  # "bullish_band_entry" | "bearish_band_entry"
    score: Decimal
    from_state: str


@dataclass(frozen=True)
class EmitResult:
    """Return value of ``emit_recent_band_entries`` for CLI / test inspection."""

    ingest_run_id: int
    entries_emitted: int
    assets_scanned: int


def _next_state(prev_state: str, score: Decimal) -> str:
    """Hysteretic three-state transition for one score observation.

    Once in a band, the score must cross the *inner* exit threshold to leave
    (dead-band), so boundary wobble doesn't toggle the state. A score that
    drops far enough can jump straight from one band to the other.
    """
    if prev_state == STATE_BULLISH:
        if score < EXIT_BULLISH:
            return STATE_BEARISH if score <= ENTER_BEARISH else STATE_NEUTRAL
        return STATE_BULLISH
    if prev_state == STATE_BEARISH:
        if score > EXIT_BEARISH:
            return STATE_BULLISH if score >= ENTER_BULLISH else STATE_NEUTRAL
        return STATE_BEARISH
    # From neutral (or the initial anchor): only a full enter-threshold cross
    # establishes a band.
    if score >= ENTER_BULLISH:
        return STATE_BULLISH
    if score <= ENTER_BEARISH:
        return STATE_BEARISH
    return STATE_NEUTRAL


def detect_band_entries(points: Sequence[ScorePoint]) -> list[BandEntry]:
    """Walk one asset's ts-ascending score series; emit one entry per band onset.

    Pure: no DB. The first point seeds the state machine (its state is
    established but is *not* itself an entry — there's no prior day to cross
    from). Thereafter a transition into ``bullish`` or ``bearish`` emits;
    transitions to ``neutral`` are silent.
    """
    entries: list[BandEntry] = []
    prev_state = STATE_NEUTRAL
    seeded = False
    for point in sorted(points, key=lambda p: p.ts):
        state = _next_state(prev_state, point.score)
        if seeded and state != prev_state and state in (STATE_BULLISH, STATE_BEARISH):
            entries.append(
                BandEntry(
                    asset=point.asset,
                    asset_class=point.asset_class,
                    sleeve=point.sleeve,
                    ts=point.ts,
                    kind=f"{state}_band_entry",
                    score=point.score,
                    from_state=prev_state,
                )
            )
        prev_state = state
        seeded = True
    return entries


def _horizon_for_sleeve(sleeve: str) -> str:
    """``equity-core`` → ``equity:core``; ``crypto-tactical`` → ``crypto:tactical``."""
    return sleeve.replace("-", ":", 1)


def _strength_from_score(score: Decimal) -> Decimal:
    scaled = abs(score) / STRENGTH_SATURATION
    return Decimal("1") if scaled > Decimal("1") else scaled


def _date_ts(d: date) -> datetime:
    return datetime.combine(d, time(0, 0, tzinfo=timezone.utc))


def _build_event(entry: BandEntry, *, rubric_version: str) -> dict[str, Any]:
    direction = STATE_BULLISH if entry.kind == "bullish_band_entry" else STATE_BEARISH
    horizon = _horizon_for_sleeve(entry.sleeve)
    payload: dict[str, Any] = {
        "asset": entry.asset,
        "sleeve": entry.sleeve,
        "rubric_version": rubric_version,
        "composite_score": str(entry.score),
        "from_state": entry.from_state,
        "entry_date": entry.ts.isoformat(),
        "thresholds": {
            "enter_bullish": str(ENTER_BULLISH),
            "exit_bullish": str(EXIT_BULLISH),
            "enter_bearish": str(ENTER_BEARISH),
            "exit_bearish": str(EXIT_BEARISH),
        },
    }
    return {
        "asset": entry.asset,
        "asset_class": entry.asset_class,
        "horizon": horizon,
        "ts": _date_ts(entry.ts),
        "source": EMITTER_SOURCE,
        "signal_kind": entry.kind,
        "direction": direction,
        "strength": _strength_from_score(entry.score),
        "payload": payload,
        "source_ref": f"{entry.asset}:{rubric_version}:{entry.kind}:{entry.ts.isoformat()}",
    }


def _load_score_points(
    *, rubric_version: str, until: date | None = None
) -> list[ScorePoint]:
    """Load ``meta.signals`` rows for one rubric version, ascending by (asset, ts)."""
    sql = (
        "SELECT asset, asset_class, sleeve, ts::date AS d, composite_score::numeric "
        "FROM meta.signals WHERE rubric_version = %s"
    )
    params: list[Any] = [rubric_version]
    if until is not None:
        sql += " AND ts::date <= %s"
        params.append(until)
    sql += " ORDER BY asset ASC, ts ASC"
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        ScorePoint(
            asset=asset,
            asset_class=asset_class,
            sleeve=sleeve,
            ts=d,
            score=Decimal(score),
        )
        for asset, asset_class, sleeve, d, score in rows
    ]


def _group_by_asset(points: Sequence[ScorePoint]) -> dict[str, list[ScorePoint]]:
    out: dict[str, list[ScorePoint]] = {}
    for point in points:
        out.setdefault(point.asset, []).append(point)
    return out


def emit_recent_band_entries(
    *,
    since: date | None = None,
    until: date | None = None,
    rubric_version: str = RUBRIC_VERSION,
) -> EmitResult:
    """Detect composite-score band entries per asset and emit signal events.

    Loads the full per-asset score history (the band state machine needs the
    prior day's state); ``--since`` filters the *emission* window after
    entries are detected. Wrapped in a single ``meta.ingest_runs`` row so the
    emitter is queryable via ``genkei watchlist health`` like any other source.
    """
    with db.ingest_run(
        EMITTER_RUN_TAG,
        endpoint=EMITTER_ENDPOINT,
        metadata={
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "rubric_version": rubric_version,
        },
    ) as run:
        points = _load_score_points(rubric_version=rubric_version, until=until)
        by_asset = _group_by_asset(points)
        events: list[dict[str, Any]] = []
        for asset_points in by_asset.values():
            for entry in detect_band_entries(asset_points):
                if since is not None and entry.ts < since:
                    continue
                if until is not None and entry.ts > until:
                    continue
                events.append(_build_event(entry, rubric_version=rubric_version))
        rows_written = emit_signals_bulk(events, ingest_run_id=run.id)
        run.add_rows(rows_written)
        return EmitResult(
            ingest_run_id=run.id,
            entries_emitted=rows_written,
            assets_scanned=len(by_asset),
        )


def parse_args(argv: list[str]) -> Any:
    import argparse

    def parse_date_arg(label: str) -> Any:
        def parse(raw: str) -> date | None:
            try:
                return _parse_date(raw, label=label)
            except Exception as exc:
                raise argparse.ArgumentTypeError(str(exc)) from exc

        return parse

    parser = argparse.ArgumentParser(
        description="Emit watchlist-scoring band-entry events into meta.signal_events."
    )
    parser.add_argument("--since", type=parse_date_arg("since"), default=None)
    parser.add_argument("--until", type=parse_date_arg("until"), default=None)
    parser.add_argument(
        "--rubric-version",
        default=RUBRIC_VERSION,
        help=f"meta.signals rubric_version to read (default {RUBRIC_VERSION}).",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    import json as _json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv or sys.argv[1:])
    result = emit_recent_band_entries(
        since=args.since, until=args.until, rubric_version=args.rubric_version
    )
    if args.json:
        print(
            _json.dumps(
                {
                    "ingest_run_id": result.ingest_run_id,
                    "entries_emitted": result.entries_emitted,
                    "assets_scanned": result.assets_scanned,
                    "source": EMITTER_SOURCE,
                },
                default=_json_default,
            )
        )
    else:
        print(
            f"watchlist-scoring emitter wrote ingest_run_id={result.ingest_run_id} "
            f"entries={result.entries_emitted} assets_scanned={result.assets_scanned}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
