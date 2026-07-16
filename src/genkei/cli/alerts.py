"""``genkei alerts`` — threshold-based alert log (B-068).

Reads ``meta.alerts``, the rows landed by the alert engine
(``python -m genkei.experiments.alert_engine``): cross-source signal stacks
(B-064) that cleared a configured threshold in ``alert_rules.yml`` and are
therefore worth paging about. Read-only, mirroring how ``genkei anomalies``
reads ``meta.anomalies`` — the engine writes, the CLI surfaces.

The default view is the most-recent alerts across every asset; filter by
``--asset`` / ``--severity`` / ``--status`` / ``--since`` / ``--until``.

Usage:
  genkei alerts                              most-recent alerts, all assets
  genkei alerts --severity critical          only the loud ones
  genkei alerts --asset NVDA                  one asset's alert history
  genkei alerts --status open --json         open alerts, machine-readable
"""

import json
from datetime import date, datetime, time, timezone
from typing import Annotated, Any, Optional

import typer

from genkei.cli._helpers import json_default as _json_default
from genkei.cli._helpers import parse_date as _parse_date
from genkei.experiments.alert_engine import Alert, query_alerts

_VALID_SEVERITIES = {"info", "warning", "critical"}
_VALID_STATUSES = {"open", "acknowledged", "resolved"}


def _date_to_dt(d: Optional[date], *, end_of_day: bool = False) -> Optional[datetime]:
    if d is None:
        return None
    day_time = (
        time(23, 59, 59, 999999, tzinfo=timezone.utc)
        if end_of_day
        else time(0, 0, tzinfo=timezone.utc)
    )
    return datetime.combine(d, day_time)


def _alert_to_dict(alert: Alert) -> dict[str, Any]:
    return {
        "alert_id": alert.alert_id,
        "alert_rule": alert.alert_rule,
        "correlation_rule": alert.correlation_rule,
        "asset": alert.asset,
        "asset_class": alert.asset_class,
        "horizon_tag": alert.horizon,
        "direction": alert.direction,
        "severity": alert.severity,
        "score": alert.score,
        "distinct_sources": alert.distinct_sources,
        "triggered_at": alert.triggered_at.isoformat(),
        "status": alert.status,
        "notified_at": alert.notified_at.isoformat() if alert.notified_at else None,
        "fingerprint": alert.fingerprint,
        "payload": alert.payload,
    }


def _format_human(alerts: list[Alert]) -> str:
    if not alerts:
        return (
            "No alerts. Either no stack has cleared a threshold in "
            "alert_rules.yml, or the engine hasn't run yet "
            "(`python -m genkei.experiments.alert_engine --since <date>`)."
        )
    header = f"Threshold alerts ({len(alerts)} found)"
    lines = [header, "-" * len(header)]
    lines.append(
        f"  {'triggered':<12} {'sev':<9} {'asset':<8} {'dir':<8} "
        f"{'alert_rule':<22} {'via_rule':<22} {'score':>6} {'src':>4} {'status':<12} notified"
    )
    for a in alerts:
        d = a.triggered_at.date().isoformat()
        score = f"{float(a.score):>6.2f}"
        notified = a.notified_at.date().isoformat() if a.notified_at else "-"
        lines.append(
            f"  {d:<12} {a.severity:<9} {a.asset:<8} {a.direction:<8} "
            f"{a.alert_rule:<22} {a.correlation_rule:<22} {score} "
            f"{a.distinct_sources:>4} {a.status:<12} {notified}"
        )
    return "\n".join(lines)


def alerts_cmd(
    asset: Annotated[
        Optional[str],
        typer.Option("--asset", "-a", help="Limit to one asset (as stored on the alert)."),
    ] = None,
    severity: Annotated[
        Optional[str],
        typer.Option("--severity", help="Filter to info / warning / critical."),
    ] = None,
    status: Annotated[
        Optional[str],
        typer.Option("--status", help="Filter to open / acknowledged / resolved."),
    ] = None,
    since: Annotated[
        Optional[str],
        typer.Option("--since", help="Earliest trigger date (YYYY-MM-DD)."),
    ] = None,
    until: Annotated[
        Optional[str],
        typer.Option("--until", help="Latest trigger date (YYYY-MM-DD)."),
    ] = None,
    top: Annotated[
        int,
        typer.Option("--top", help="Max rows.", min=1),
    ] = 30,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Show threshold-based alerts from the alert engine (B-068)."""
    since_d = _parse_date(since, label="since")
    until_d = _parse_date(until, label="until")
    if since_d is not None and until_d is not None and since_d > until_d:
        raise typer.BadParameter("--since must be on or before --until.")
    if severity is not None and severity not in _VALID_SEVERITIES:
        raise typer.BadParameter(
            f"--severity must be one of {', '.join(sorted(_VALID_SEVERITIES))}."
        )
    if status is not None and status not in _VALID_STATUSES:
        raise typer.BadParameter(
            f"--status must be one of {', '.join(sorted(_VALID_STATUSES))}."
        )

    alerts = query_alerts(
        asset=asset,
        severity=severity,
        status=status,
        since=_date_to_dt(since_d),
        until=_date_to_dt(until_d, end_of_day=True),
        limit=top,
    )
    if json_out:
        typer.echo(
            json.dumps([_alert_to_dict(a) for a in alerts], indent=2, default=_json_default)
        )
    else:
        typer.echo(_format_human(alerts))
