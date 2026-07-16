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
experiments layer for one webhook POST, and so the single network call is
trivially monkeypatched in tests.
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


def build_embed(alerts: list[Alert]) -> dict[str, Any]:
    """Build the Discord embed payload summarizing ``alerts``.

    Title reflects the loudest severity present; body lists one line per alert
    (asset, direction, rule, score), truncated to Discord's field limits.
    """
    severity = _loudest_severity(alerts)
    emoji = _SEVERITY_EMOJI.get(severity, "🔔")
    title = f"{emoji} {len(alerts)} new signal alert(s) — {severity}"
    lines = [
        f"**{a.asset}** {a.direction} · `{a.alert_rule}` "
        f"(via `{a.correlation_rule}`, score {float(a.score):.2f}, "
        f"{a.distinct_sources} sources) — {a.triggered_at.date().isoformat()}"
        for a in alerts
    ]
    description = "\n".join(lines)
    return {
        "title": title[:_TITLE_MAX],
        "description": description[:_DESC_MAX],
        "color": _SEVERITY_COLOR.get(severity, _DEFAULT_COLOR),
    }


def _loudest_severity(alerts: list[Alert]) -> str:
    order = {"critical": 3, "warning": 2, "info": 1}
    return max(
        (a.severity for a in alerts),
        key=lambda s: order.get(s, 0),
        default="info",
    )


def post_alerts(alerts: list[Alert], *, webhook_url: str | None) -> bool:
    """Post ``alerts`` to the Discord webhook. Return ``True`` on a 2xx delivery.

    Returns ``False`` (never raises) when the webhook URL is empty or the POST
    fails — the caller treats a failed ping as non-fatal because the alert rows
    are already persisted.
    """
    if not alerts:
        return False
    if not webhook_url:
        LOGGER.info(
            "DISCORD_WEBHOOK_URL not set — skipping Discord notification "
            "(%s alert(s) still persisted to meta.alerts).",
            len(alerts),
        )
        return False
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
