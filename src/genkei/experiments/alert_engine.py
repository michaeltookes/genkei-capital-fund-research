"""Threshold-based alert engine (B-068).

One layer above the B-064 cross-source correlator. The correlator's rules
decide what a *stack* is (multi-source agreement on an asset); this module's
alert rules decide which stacks are worth *paging about* and land one row per
(alert-rule, stack) in ``meta.alerts``.

Three halves, mirroring ``signal_store``:

  * **Domain types** — :class:`AlertRule` (a threshold config, loaded from
    ``alert_rules.yml`` by :mod:`genkei.experiments.alert_rules`) and
    :class:`Alert` (one row destined for ``meta.alerts``).
  * **Pure evaluator** — :func:`evaluate_alerts` walks detected stacks against
    the alert rules and returns the candidate alerts. No DB, so it's testable
    on synthetic stacks + rules.
  * **Persistence + orchestration** — :func:`persist_alerts` writes candidates
    idempotently (``ON CONFLICT (fingerprint) DO NOTHING``) and returns only
    the *newly-created* rows so the caller knows what to page on;
    :func:`run_alert_engine` ties load → correlate → evaluate → persist →
    (optional) notify together inside an ``ingest_run``.

Why the fingerprint dedup rather than a cooldown query: a stack's
``window_end`` is stable, so the same stack always produces the same
fingerprint. ``DO NOTHING`` on the UNIQUE constraint makes the daily run
replay-safe without a second round-trip, and a genuinely new stack on a later
date naturally gets a fresh fingerprint → a fresh alert.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from genkei.common import db
from genkei.experiments.signal_rules import DEFAULT_RULES_PATH, load_rules
from genkei.experiments.signal_store import Stack, detect_stacks, query_events

LOGGER = logging.getLogger(__name__)

SEVERITIES = ("info", "warning", "critical")
ENGINE_RUN_TAG = "alert_engine"
ENGINE_ENDPOINT = "evaluate"


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AlertRule:
    """A threshold config that escalates matching stacks into ``meta.alerts``.

    All ``match_*`` tuples are *any-of* filters — an empty tuple matches
    everything on that axis. ``min_score`` / ``min_distinct_sources`` are an
    escalated floor layered on top of whatever the correlation rule already
    required (0 = no extra floor beyond the stack existing).
    """

    name: str
    description: str
    severity: str
    match_rules: tuple[str, ...] = ()
    match_asset_classes: tuple[str, ...] = ()
    match_horizons: tuple[str, ...] = ()
    match_directions: tuple[str, ...] = ()
    min_score: Decimal = Decimal("0")
    min_distinct_sources: int = 0


@dataclass(frozen=True)
class Alert:
    """One candidate/persisted row for ``meta.alerts``."""

    alert_rule: str
    correlation_rule: str
    asset: str
    asset_class: str
    horizon: str
    direction: str
    severity: str
    score: Decimal
    distinct_sources: int
    triggered_at: datetime
    fingerprint: str
    payload: dict[str, Any]
    alert_id: int | None = None  # populated by the DB on insert
    status: str = "open"
    notified_at: datetime | None = None
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# Pure evaluator
# ---------------------------------------------------------------------------


def evaluate_alerts(
    stacks: list[Stack],
    alert_rules: list[AlertRule],
) -> list[Alert]:
    """Return one :class:`Alert` per (alert-rule, stack) that clears the rule.

    A single stack can trip several alert rules (a broad ``info`` catch-all and
    a narrow ``critical`` both match) — each yields its own alert with its own
    fingerprint. Candidates are de-duplicated by fingerprint within the batch
    (keeping the highest score) so a caller never hands two colliding rows to
    the same ``executemany``.
    """
    candidates: dict[str, Alert] = {}
    for stack in stacks:
        for rule in alert_rules:
            if not _stack_matches(stack, rule):
                continue
            alert = _alert_from_stack(stack, rule)
            existing = candidates.get(alert.fingerprint)
            if existing is None or alert.score > existing.score:
                candidates[alert.fingerprint] = alert
    out = list(candidates.values())
    # Freshest, strongest first — the order the pager/digest wants.
    out.sort(key=lambda a: (-a.triggered_at.timestamp(), -a.score, a.asset))
    return out


def _stack_matches(stack: Stack, rule: AlertRule) -> bool:
    if rule.match_rules and stack.rule_name not in rule.match_rules:
        return False
    if rule.match_asset_classes and stack.asset_class not in rule.match_asset_classes:
        return False
    if rule.match_horizons and stack.horizon not in rule.match_horizons:
        return False
    if rule.match_directions and stack.direction not in rule.match_directions:
        return False
    if stack.score < rule.min_score:
        return False
    return stack.distinct_sources >= rule.min_distinct_sources


def _alert_from_stack(stack: Stack, rule: AlertRule) -> Alert:
    fingerprint = _fingerprint(
        alert_rule=rule.name,
        correlation_rule=stack.rule_name,
        asset=stack.asset,
        horizon=stack.horizon,
        triggered_at=stack.window_end,
    )
    payload = {
        "correlation_rule": stack.rule_name,
        "window_start": stack.window_start.isoformat(),
        "window_end": stack.window_end.isoformat(),
        "span_days": (stack.window_end - stack.window_start).days,
        "score": str(stack.score),
        "distinct_sources": stack.distinct_sources,
        "event_count": stack.event_count,
        "events": [
            {
                "source": ev.source,
                "signal_kind": ev.signal_kind,
                "ts": ev.ts.isoformat(),
                "direction": ev.direction,
                "strength": str(ev.strength) if ev.strength is not None else None,
                "source_ref": ev.source_ref,
            }
            for ev in stack.events
        ],
    }
    return Alert(
        alert_rule=rule.name,
        correlation_rule=stack.rule_name,
        asset=stack.asset,
        asset_class=stack.asset_class,
        horizon=stack.horizon,
        direction=stack.direction,
        severity=rule.severity,
        score=stack.score,
        distinct_sources=stack.distinct_sources,
        triggered_at=stack.window_end,
        fingerprint=fingerprint,
        payload=payload,
    )


def _fingerprint(
    *,
    alert_rule: str,
    correlation_rule: str,
    asset: str,
    horizon: str,
    triggered_at: datetime,
) -> str:
    """Stable dedup key. ``triggered_at`` collapses to its UTC date so the same
    stack always hashes identically across re-runs on the same day."""
    day = triggered_at.astimezone(timezone.utc).date().isoformat()
    return f"{alert_rule}:{correlation_rule}:{asset}:{horizon}:{day}"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def persist_alerts(
    alerts: list[Alert],
    *,
    ingest_run_id: int,
    conn: Any,
) -> list[Alert]:
    """Insert candidate alerts; return only the rows that were newly created.

    Idempotent via ``ON CONFLICT (fingerprint) DO NOTHING`` — a re-run over the
    same window inserts nothing and returns ``[]``. Per-row (rather than
    ``executemany``) because the volume is tiny and we need each row's
    ``RETURNING`` to tell new from already-seen.
    """
    if not alerts:
        return []
    created: list[Alert] = []
    with conn.cursor() as cur:
        for alert in alerts:
            cur.execute(
                """
                INSERT INTO meta.alerts (
                    alert_rule, correlation_rule, asset, asset_class, horizon,
                    direction, severity, score, distinct_sources, triggered_at,
                    fingerprint, payload, ingest_run_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (fingerprint) DO NOTHING
                RETURNING alert_id
                """,
                [
                    alert.alert_rule,
                    alert.correlation_rule,
                    alert.asset,
                    alert.asset_class,
                    alert.horizon,
                    alert.direction,
                    alert.severity,
                    alert.score,
                    alert.distinct_sources,
                    alert.triggered_at,
                    alert.fingerprint,
                    Jsonb(alert.payload),
                    ingest_run_id,
                ],
            )
            row = cur.fetchone()
            if row is not None:
                created.append(_with_id(alert, int(row[0])))
    return created


def _with_id(alert: Alert, alert_id: int) -> Alert:
    return Alert(
        alert_rule=alert.alert_rule,
        correlation_rule=alert.correlation_rule,
        asset=alert.asset,
        asset_class=alert.asset_class,
        horizon=alert.horizon,
        direction=alert.direction,
        severity=alert.severity,
        score=alert.score,
        distinct_sources=alert.distinct_sources,
        triggered_at=alert.triggered_at,
        fingerprint=alert.fingerprint,
        payload=alert.payload,
        alert_id=alert_id,
        status=alert.status,
    )


def mark_notified(conn: Any, alert_ids: list[int]) -> int:
    """Stamp ``notified_at`` on the given alert rows; return the count updated."""
    if not alert_ids:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE meta.alerts SET notified_at = now() "
            "WHERE alert_id = ANY(%s) AND notified_at IS NULL",
            [alert_ids],
        )
        return max(cur.rowcount or 0, 0)


def query_alerts(
    *,
    asset: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int | None = None,
) -> list[Alert]:
    """Read persisted alerts from ``meta.alerts`` with optional filters."""
    sql = (
        "SELECT alert_id, alert_rule, correlation_rule, asset, asset_class, "
        "       horizon, direction, severity, score, distinct_sources, "
        "       triggered_at, fingerprint, payload, status, notified_at, created_at "
        "FROM meta.alerts WHERE 1 = 1 "
    )
    params: list[Any] = []
    if asset is not None:
        sql += " AND asset = %s"
        params.append(asset)
    if severity is not None:
        sql += " AND severity = %s"
        params.append(severity)
    if status is not None:
        sql += " AND status = %s"
        params.append(status)
    if since is not None:
        sql += " AND triggered_at >= %s"
        params.append(since)
    if until is not None:
        sql += " AND triggered_at <= %s"
        params.append(until)
    sql += " ORDER BY triggered_at DESC, alert_id DESC"
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)

    out: list[Alert] = []
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        for row in cur.fetchall():
            (
                alert_id,
                alert_rule,
                correlation_rule,
                asset_v,
                asset_class,
                horizon,
                direction,
                severity_v,
                score,
                distinct_sources,
                triggered_at,
                fingerprint,
                payload,
                status,
                notified_at,
                created_at,
            ) = row
            out.append(
                Alert(
                    alert_id=int(alert_id),
                    alert_rule=alert_rule,
                    correlation_rule=correlation_rule,
                    asset=asset_v,
                    asset_class=asset_class,
                    horizon=horizon,
                    direction=direction,
                    severity=severity_v,
                    score=score,
                    distinct_sources=int(distinct_sources),
                    triggered_at=triggered_at,
                    fingerprint=fingerprint,
                    payload=payload or {},
                    status=status,
                    notified_at=notified_at,
                    created_at=created_at,
                )
            )
    return out


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AlertRunResult:
    """Return value of :func:`run_alert_engine`."""

    ingest_run_id: int
    stacks_seen: int
    candidates: int
    new_alerts: list[Alert] = field(default_factory=list)
    notified: int = 0


def run_alert_engine(
    *,
    since: date | None = None,
    until: date | None = None,
    rules_path: Path = DEFAULT_RULES_PATH,
    alert_rules_path: Path | None = None,
    notify: bool = False,
    webhook_url: str | None = None,
) -> AlertRunResult:
    """Load rules → correlate → evaluate → persist → (optionally) notify.

    ``notify`` posts newly-created alerts to the Discord webhook and stamps
    ``notified_at``; it no-ops gracefully when no webhook URL is configured
    (the persisted rows are the durable record, Discord is the ping — B-119).
    """
    # Local import so the pure evaluator + persistence don't pull yaml.
    from genkei.experiments.alert_rules import DEFAULT_ALERT_RULES_PATH, load_alert_rules

    if alert_rules_path is None:
        alert_rules_path = DEFAULT_ALERT_RULES_PATH

    rules = load_rules(rules_path)
    alert_rules = load_alert_rules(alert_rules_path)

    since_dt = _date_to_dt(since)
    until_dt = _date_to_dt(until, end_of_day=True)

    with db.ingest_run(
        ENGINE_RUN_TAG,
        endpoint=ENGINE_ENDPOINT,
        metadata={
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
        },
    ) as run:
        events = query_events(since=since_dt, until=until_dt)
        stacks = detect_stacks(events, rules)
        candidates = evaluate_alerts(stacks, alert_rules)
        with db.connection() as conn:
            new_alerts = persist_alerts(candidates, ingest_run_id=run.id, conn=conn)
            conn.commit()
        run.add_rows(len(new_alerts))

        notified = 0
        if notify and new_alerts:
            notified = _notify(new_alerts, webhook_url=webhook_url)

        LOGGER.info(
            "alert engine: %s stacks, %s candidates, %s new alerts, %s notified",
            len(stacks),
            len(candidates),
            len(new_alerts),
            notified,
        )
        return AlertRunResult(
            ingest_run_id=run.id,
            stacks_seen=len(stacks),
            candidates=len(candidates),
            new_alerts=new_alerts,
            notified=notified,
        )


def _notify(new_alerts: list[Alert], *, webhook_url: str | None) -> int:
    """Post new alerts to Discord and stamp ``notified_at`` on success."""
    # Local import keeps urllib off the hot path for callers that never notify.
    from genkei.experiments import alert_notify

    posted = alert_notify.post_alerts(new_alerts, webhook_url=webhook_url)
    if not posted:
        return 0
    ids = [a.alert_id for a in new_alerts if a.alert_id is not None]
    with db.connection() as conn:
        marked = mark_notified(conn, ids)
        conn.commit()
    return marked


def _date_to_dt(d: date | None, *, end_of_day: bool = False) -> datetime | None:
    if d is None:
        return None
    day_time = (
        time(23, 59, 59, 999999, tzinfo=timezone.utc)
        if end_of_day
        else time(0, 0, tzinfo=timezone.utc)
    )
    return datetime.combine(d, day_time)


# ---------------------------------------------------------------------------
# CLI entry point (module runner — the engine writes; `genkei alerts` reads)
# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> Any:
    import argparse

    from genkei.cli._helpers import parse_date as _parse_date

    def parse_date_arg(label: str) -> Any:
        def parse(raw: str) -> date | None:
            try:
                return _parse_date(raw, label=label)
            except Exception as exc:
                raise argparse.ArgumentTypeError(str(exc)) from exc

        return parse

    parser = argparse.ArgumentParser(
        description="Evaluate threshold alert rules against signal stacks (B-068)."
    )
    parser.add_argument("--since", type=parse_date_arg("since"), default=None)
    parser.add_argument("--until", type=parse_date_arg("until"), default=None)
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Post newly-created alerts to the Discord webhook (DISCORD_WEBHOOK_URL).",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    import json as _json
    import os
    import sys

    from genkei.cli._helpers import json_default as _json_default

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv or sys.argv[1:])
    result = run_alert_engine(
        since=args.since,
        until=args.until,
        notify=args.notify,
        webhook_url=os.environ.get("DISCORD_WEBHOOK_URL") if args.notify else None,
    )
    if args.json:
        print(
            _json.dumps(
                {
                    "ingest_run_id": result.ingest_run_id,
                    "stacks_seen": result.stacks_seen,
                    "candidates": result.candidates,
                    "new_alerts": len(result.new_alerts),
                    "notified": result.notified,
                    "alerts": [
                        {
                            "alert_id": a.alert_id,
                            "alert_rule": a.alert_rule,
                            "correlation_rule": a.correlation_rule,
                            "asset": a.asset,
                            "asset_class": a.asset_class,
                            "horizon": a.horizon,
                            "direction": a.direction,
                            "severity": a.severity,
                            "score": a.score,
                            "distinct_sources": a.distinct_sources,
                            "triggered_at": a.triggered_at.isoformat(),
                            "fingerprint": a.fingerprint,
                        }
                        for a in result.new_alerts
                    ],
                },
                default=_json_default,
            )
        )
    else:
        print(
            f"alert engine wrote ingest_run_id={result.ingest_run_id} "
            f"stacks={result.stacks_seen} candidates={result.candidates} "
            f"new_alerts={len(result.new_alerts)} notified={result.notified}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
