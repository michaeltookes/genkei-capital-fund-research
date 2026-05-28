"""Atomic signal-event store + cross-source correlator (B-064).

Two halves to this module:

  * **Store** — ``emit_signal`` / ``emit_signals_bulk`` write rows into
    ``meta.signal_events``; ``query_events`` reads them. The store is
    intentionally thin — it doesn't know what a signal *means*, only
    how to persist it idempotently (UNIQUE conflict on
    ``(asset, ts, source, signal_kind, source_ref)`` so a re-emission
    updates the latest payload rather than blowing up).

  * **Correlator** — pure function ``detect_stacks`` walks events
    against rule configs and surfaces co-occurring signals on the
    same asset within a window. A "stack" is multiple signals firing
    on the same asset in the same direction within ``window_days`` —
    the institutional analogue of "many sources agreeing." Scoring is
    weighted sum of component-strength contributions; rules require
    a minimum score *and* a minimum number of distinct sources so a
    single noisy emitter can't fake a multi-source stack.

Why the pure correlator lives next to the store: the rule engine
needs the events as input but doesn't otherwise care about the DB.
Keeping it pure makes the algorithm testable on synthetic events and
keeps the SQL layer thin.

Naming:
  * ``meta.signal_events``     — this module writes here.
  * ``meta.signals``           — B-065's composite scoring rubric
                                  (different shape, different consumer,
                                  not touched here).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from psycopg.types.json import Jsonb

from genkei.common import db


# ---------------------------------------------------------------------------
# Allowed enum values (must match the migration's CHECK constraints).
# ---------------------------------------------------------------------------

ASSET_CLASSES = frozenset({"equity", "crypto", "protocol"})
DIRECTIONS = frozenset({"bullish", "bearish", "neutral"})


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignalEvent:
    """One atomic signal event read out of ``meta.signal_events``."""

    asset: str
    asset_class: str
    ts: datetime
    source: str
    signal_kind: str
    direction: str
    strength: Decimal | None
    payload: dict[str, Any]
    source_ref: str | None
    event_id: int | None = None  # populated by DB on insert; None for in-memory


@dataclass(frozen=True)
class RuleComponent:
    """One component contributing to a correlation rule's score.

    ``signal_kind=None`` matches *any* kind from the source — useful
    when an emitter has several closely-related kinds (e.g. crowding's
    ``crowding_add`` and ``crowding_jump``) that should all count.
    """

    source: str
    signal_kind: str | None
    weight: Decimal


@dataclass(frozen=True)
class CorrelationRule:
    """A rule definition. Matches events with the given direction across
    components and scores them.

    ``min_distinct_sources`` defaults to 2 — that's the load-bearing
    constraint that makes a "stack" actually multi-source rather than
    one noisy emitter firing twice.
    """

    name: str
    description: str
    direction: str
    components: list[RuleComponent]
    window_days: int = 7
    min_score: Decimal = Decimal("1.5")
    min_distinct_sources: int = 2


@dataclass(frozen=True)
class Stack:
    """A detected co-occurrence of signals on one asset within a window."""

    rule_name: str
    asset: str
    asset_class: str
    direction: str
    window_start: datetime
    window_end: datetime
    score: Decimal
    distinct_sources: int
    event_count: int
    events: list[SignalEvent] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pure correlator
# ---------------------------------------------------------------------------


DEFAULT_STRENGTH_WHEN_NULL = Decimal("1.0")


def detect_stacks(
    events: list[SignalEvent],
    rules: list[CorrelationRule],
) -> list[Stack]:
    """Walk every rule against the event stream; return matching stacks.

    Algorithm per rule:
      1. Filter events to those whose direction matches the rule and
         whose (source, signal_kind) is a component of the rule.
      2. Group by asset.
      3. Sort each asset's events chronologically.
      4. Greedy window scan — for each event, see if any subsequent
         events within ``window_days`` push the total weighted-strength
         score over ``min_score`` AND distinct-source count over
         ``min_distinct_sources``. Emit and advance past the window.

    Stacks are returned sorted by ``window_end DESC, score DESC`` — the
    "most-recent, strongest" default the CLI wants.
    """
    if not events:
        return []
    out: list[Stack] = []
    for rule in rules:
        out.extend(_detect_for_rule(events, rule))
    out.sort(
        key=lambda s: (
            -s.window_end.timestamp(),
            -float(s.score),
            s.asset,
            s.rule_name,
        )
    )
    return out


def _detect_for_rule(events: list[SignalEvent], rule: CorrelationRule) -> list[Stack]:
    matching = _filter_by_rule(events, rule)
    if not matching:
        return []

    by_asset: dict[str, list[SignalEvent]] = defaultdict(list)
    for ev in matching:
        by_asset[ev.asset].append(ev)

    stacks: list[Stack] = []
    for asset, asset_events in by_asset.items():
        asset_events.sort(key=lambda e: (e.ts, e.source, e.signal_kind))
        stacks.extend(_scan_asset_events(asset, asset_events, rule))
    return stacks


def _filter_by_rule(
    events: list[SignalEvent], rule: CorrelationRule
) -> list[SignalEvent]:
    """Keep only events whose direction + (source, signal_kind) match the rule."""
    by_source: dict[str, list[RuleComponent]] = defaultdict(list)
    for c in rule.components:
        by_source[c.source].append(c)
    out: list[SignalEvent] = []
    for ev in events:
        if ev.direction != rule.direction:
            continue
        components_for_source = by_source.get(ev.source)
        if components_for_source is None:
            continue
        # Match either an exact-kind component or a kind=None wildcard.
        if any(
            c.signal_kind is None or c.signal_kind == ev.signal_kind
            for c in components_for_source
        ):
            out.append(ev)
    return out


def _scan_asset_events(
    asset: str,
    events: list[SignalEvent],
    rule: CorrelationRule,
) -> list[Stack]:
    """Slide a window over one asset's events; emit stacks greedily."""
    window_seconds = rule.window_days * 86400
    i = 0
    n = len(events)
    out: list[Stack] = []
    while i < n:
        anchor_ts = events[i].ts
        # Collect every event whose ts falls inside the window.
        j = i
        window: list[SignalEvent] = []
        while j < n and (events[j].ts - anchor_ts).total_seconds() <= window_seconds:
            window.append(events[j])
            j += 1
        score, distinct_sources = _score_window(window, rule)
        if score >= rule.min_score and distinct_sources >= rule.min_distinct_sources:
            out.append(
                Stack(
                    rule_name=rule.name,
                    asset=asset,
                    asset_class=window[0].asset_class,
                    direction=rule.direction,
                    window_start=window[0].ts,
                    window_end=window[-1].ts,
                    score=score,
                    distinct_sources=distinct_sources,
                    event_count=len(window),
                    events=list(window),
                )
            )
            # Greedy advance — skip past the window so a long burst of
            # events doesn't emit dozens of overlapping stacks.
            i = j
        else:
            i += 1
    return out


def _score_window(
    window: list[SignalEvent], rule: CorrelationRule
) -> tuple[Decimal, int]:
    """Sum (weight × strength) across components that matched, and count
    the distinct sources that contributed at least one matching event."""
    score = Decimal("0")
    distinct_sources: set[str] = set()
    # For each event, find the matching component and add its contribution.
    # Wildcard (signal_kind=None) loses ties to exact-kind matches so a
    # rule can give a generic baseline weight to "any 8-K" while bumping
    # specific item codes higher.
    components_by_source: dict[str, list[RuleComponent]] = defaultdict(list)
    for c in rule.components:
        components_by_source[c.source].append(c)
    for ev in window:
        comps = components_by_source.get(ev.source, [])
        # Exact-kind component wins over wildcard if both are configured.
        exact = next((c for c in comps if c.signal_kind == ev.signal_kind), None)
        chosen = exact or next((c for c in comps if c.signal_kind is None), None)
        if chosen is None:
            continue
        strength = ev.strength if ev.strength is not None else DEFAULT_STRENGTH_WHEN_NULL
        score += chosen.weight * strength
        distinct_sources.add(ev.source)
    return score, len(distinct_sources)


# ---------------------------------------------------------------------------
# Persistence (thin wrappers around psycopg)
# ---------------------------------------------------------------------------


def emit_signal(
    *,
    asset: str,
    asset_class: str,
    ts: datetime,
    source: str,
    signal_kind: str,
    direction: str,
    strength: Decimal | None,
    payload: dict[str, Any] | None,
    source_ref: str | None,
    ingest_run_id: int,
) -> int:
    """Insert one signal event; upsert on UNIQUE conflict, return event_id.

    The UNIQUE key is ``(asset, ts, source, signal_kind, source_ref)`` —
    re-emitting the same event with a freshened payload updates the
    existing row rather than duplicating it.
    """
    _validate(asset_class=asset_class, direction=direction)
    payload_value = Jsonb(payload or {})
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO meta.signal_events (
                asset, asset_class, ts, source, signal_kind, direction,
                strength, payload, source_ref, ingest_run_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (asset, ts, source, signal_kind, source_ref)
            DO UPDATE SET
                direction     = EXCLUDED.direction,
                strength      = EXCLUDED.strength,
                payload       = EXCLUDED.payload,
                asset_class   = EXCLUDED.asset_class,
                computed_at   = now(),
                ingest_run_id = EXCLUDED.ingest_run_id
            RETURNING event_id
            """,
            [
                asset,
                asset_class,
                ts,
                source,
                signal_kind,
                direction,
                strength,
                payload_value,
                source_ref,
                ingest_run_id,
            ],
        )
        row = cur.fetchone()
    return int(row[0])


def emit_signals_bulk(
    events: list[dict[str, Any]],
    *,
    ingest_run_id: int,
) -> int:
    """Insert many signal events in one round-trip; return inserted/updated count.

    Each ``events`` entry is a dict with the same keys ``emit_signal`` takes
    (minus ``ingest_run_id`` which is shared across the batch).
    """
    if not events:
        return 0
    sql = """
        INSERT INTO meta.signal_events (
            asset, asset_class, ts, source, signal_kind, direction,
            strength, payload, source_ref, ingest_run_id
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (asset, ts, source, signal_kind, source_ref)
        DO UPDATE SET
            direction     = EXCLUDED.direction,
            strength      = EXCLUDED.strength,
            payload       = EXCLUDED.payload,
            asset_class   = EXCLUDED.asset_class,
            computed_at   = now(),
            ingest_run_id = EXCLUDED.ingest_run_id
    """
    rows = []
    for ev in events:
        _validate(asset_class=ev["asset_class"], direction=ev["direction"])
        rows.append(
            (
                ev["asset"],
                ev["asset_class"],
                ev["ts"],
                ev["source"],
                ev["signal_kind"],
                ev["direction"],
                ev.get("strength"),
                Jsonb(ev.get("payload") or {}),
                ev.get("source_ref"),
                ingest_run_id,
            )
        )
    with db.connection() as conn, conn.cursor() as cur:
        cur.executemany(sql, rows)
    return len(rows)


def query_events(
    *,
    asset: str | None = None,
    source: str | None = None,
    signal_kind: str | None = None,
    direction: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int | None = None,
) -> list[SignalEvent]:
    """Read signal events from ``meta.signal_events`` with optional filters."""
    sql = (
        "SELECT event_id, asset, asset_class, ts, source, signal_kind, "
        "       direction, strength, payload, source_ref "
        "FROM meta.signal_events WHERE 1 = 1 "
    )
    params: list[Any] = []
    if asset is not None:
        sql += " AND asset = %s"
        params.append(asset)
    if source is not None:
        sql += " AND source = %s"
        params.append(source)
    if signal_kind is not None:
        sql += " AND signal_kind = %s"
        params.append(signal_kind)
    if direction is not None:
        sql += " AND direction = %s"
        params.append(direction)
    if since is not None:
        sql += " AND ts >= %s"
        params.append(since)
    if until is not None:
        sql += " AND ts <= %s"
        params.append(until)
    sql += " ORDER BY ts DESC, event_id DESC"
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)

    out: list[SignalEvent] = []
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        for row in cur.fetchall():
            (
                event_id,
                asset_v,
                asset_class,
                ts,
                source_v,
                signal_kind_v,
                direction_v,
                strength,
                payload,
                source_ref,
            ) = row
            out.append(
                SignalEvent(
                    event_id=int(event_id),
                    asset=asset_v,
                    asset_class=asset_class,
                    ts=ts,
                    source=source_v,
                    signal_kind=signal_kind_v,
                    direction=direction_v,
                    strength=strength,
                    payload=payload or {},
                    source_ref=source_ref,
                )
            )
    return out


def _validate(*, asset_class: str, direction: str) -> None:
    if asset_class not in ASSET_CLASSES:
        raise ValueError(
            f"asset_class must be one of {sorted(ASSET_CLASSES)}, got {asset_class!r}"
        )
    if direction not in DIRECTIONS:
        raise ValueError(
            f"direction must be one of {sorted(DIRECTIONS)}, got {direction!r}"
        )
