"""Discord notification hook for the alert engine (B-068 / B-119).

The Python-side twin of ``.github/actions/discord-notify``: posts a compact
embed summarizing newly-created ``meta.alerts`` rows to a Discord incoming
webhook. Same contract as the composite action —

  * **No-ops gracefully** (returns ``False``, logs a notice) when the webhook
    URL is empty, so a run before ``DISCORD_WEBHOOK_URL`` is configured still
    succeeds; the persisted alert rows are the durable record.
  * **A non-2xx / transport error is a warning, not a failure** — the alert is
    already in ``meta.alerts``; Discord is the real-time ping, not the ledger.

Uses ``urllib.request`` (stdlib) rather than pulling ``requests`` into the
experiments layer for webhook POSTs, and so the network calls are trivially
monkeypatched in tests.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from genkei.experiments.alert_engine import Alert

LOGGER = logging.getLogger(__name__)

# Discord embed sidebar colors (decimal ints), by severity.
_SEVERITY_COLOR = {
    "critical": 15158332,  # red
    "warning": 15844367,   # amber
    "info": 3447003,       # blue
}
_DEFAULT_COLOR = _SEVERITY_COLOR["warning"]
_SEVERITY_EMOJI = {"critical": "🔴", "warning": "🟠", "info": "🔵"}

_TITLE_MAX = 256
_DESC_MAX = 4096
_POST_TIMEOUT_S = 10
_LINE_TRUNCATION = "..."


def build_embed(alerts: list[Alert]) -> dict[str, Any]:
    """Build the Discord embed payload summarizing ``alerts``.

    Title reflects the loudest severity present; body lists one line per alert
    (asset, direction, rule, score). Callers batch alert sets to Discord's field
    limits before posting.
    """
    severity = _loudest_severity(alerts)
    emoji = _SEVERITY_EMOJI.get(severity, "🔔")
    title = f"{emoji} {len(alerts)} new signal alert(s) — {severity}"
    lines = [_alert_line(a) for a in alerts]
    description = "\n".join(lines)
    return {
        "title": title[:_TITLE_MAX],
        "description": description,
        "color": _SEVERITY_COLOR.get(severity, _DEFAULT_COLOR),
    }


def _alert_line(alert: Alert) -> str:
    line = (
        f"**{alert.asset}** {alert.direction} · `{alert.alert_rule}` "
        f"(via `{alert.correlation_rule}`, score {float(alert.score):.2f}, "
        f"{alert.distinct_sources} sources) — {alert.triggered_at.date().isoformat()}"
    )
    if len(line) <= _DESC_MAX:
        return line
    return line[: _DESC_MAX - len(_LINE_TRUNCATION)] + _LINE_TRUNCATION


def _chunk_alerts_for_embeds(alerts: list[Alert]) -> list[list[Alert]]:
    batches: list[list[Alert]] = []
    current: list[Alert] = []
    current_len = 0
    for alert in alerts:
        line_len = len(_alert_line(alert))
        separator_len = 1 if current else 0
        if current and current_len + separator_len + line_len > _DESC_MAX:
            batches.append(current)
            current = []
            current_len = 0
            separator_len = 0
        current.append(alert)
        current_len += separator_len + line_len
    if current:
        batches.append(current)
    return batches


def _loudest_severity(alerts: list[Alert]) -> str:
    order = {"critical": 3, "warning": 2, "info": 1}
    return max(
        (a.severity for a in alerts),
        key=lambda s: order.get(s, 0),
        default="info",
    )


def post_alert_batches(alerts: list[Alert], *, webhook_url: str | None) -> list[Alert]:
    """Post ``alerts`` in Discord-sized batches; return the delivered rows.

    The return value lets the engine stamp ``notified_at`` only for rows that
    were actually included in a successful webhook POST.
    """
    if not alerts:
        return []
    if not webhook_url:
        LOGGER.info(
            "DISCORD_WEBHOOK_URL not set — skipping Discord notification "
            "(%s alert(s) still persisted to meta.alerts).",
            len(alerts),
        )
        return []
    delivered: list[Alert] = []
    batches = _chunk_alerts_for_embeds(alerts)
    for idx, batch in enumerate(batches, start=1):
        posted = _post_alert_batch(batch, webhook_url=webhook_url)
        if not posted:
            LOGGER.warning(
                "Discord webhook delivery stopped at batch %s/%s; %s of %s alert(s) "
                "were posted.",
                idx,
                len(batches),
                len(delivered),
                len(alerts),
            )
            break
        delivered.extend(batch)
    return delivered


def post_alerts(alerts: list[Alert], *, webhook_url: str | None) -> bool:
    """Post ``alerts`` to the Discord webhook. Return ``True`` when all deliver.

    Returns ``False`` (never raises) when the webhook URL is empty or any POST
    fails — the caller treats a failed ping as non-fatal because the alert rows
    are already persisted.
    """
    delivered = post_alert_batches(alerts, webhook_url=webhook_url)
    return bool(alerts) and len(delivered) == len(alerts)


def _post_alert_batch(alerts: list[Alert], *, webhook_url: str) -> bool:
    payload = json.dumps({"embeds": [build_embed(alerts)]}).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_POST_TIMEOUT_S) as resp:
            code = resp.getcode()
    except urllib.error.URLError as exc:
        LOGGER.warning(
            "Discord webhook POST failed (%s) — alert(s) still recorded in meta.alerts.",
            exc,
        )
        return False
    if 200 <= code < 300:
        LOGGER.info("Discord webhook responded %s — %s alert(s) delivered.", code, len(alerts))
        return True
    LOGGER.warning(
        "Discord webhook returned %s — alert(s) may not have been delivered "
        "(still recorded in meta.alerts).",
        code,
    )
    return False
