"""Unit tests for the ``genkei alerts`` CLI (B-068)."""

from __future__ import annotations

import json
import re
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from typer.testing import CliRunner

from genkei.cli import app
from genkei.experiments.alert_engine import Alert

runner = CliRunner()
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _combined_output(result: object) -> str:
    stdout = getattr(result, "stdout", "")
    try:
        stderr = getattr(result, "stderr", "")
    except ValueError:
        return _ANSI_RE.sub("", getattr(result, "output", stdout))
    return _ANSI_RE.sub("", stdout + stderr)


def _alert(*, asset: str = "NVDA", severity: str = "critical", notified: bool = False) -> Alert:
    return Alert(
        alert_id=1,
        alert_rule="critical_equity_exit",
        correlation_rule="broad_exit",
        asset=asset,
        asset_class="equity",
        horizon="equity:core",
        direction="bearish",
        severity=severity,
        score=Decimal("2.5"),
        distinct_sources=2,
        triggered_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
        fingerprint=f"critical_equity_exit:broad_exit:{asset}:equity:core:2026-07-10",
        payload={"correlation_rule": "broad_exit"},
        status="open",
        notified_at=datetime(2026, 7, 10, tzinfo=timezone.utc) if notified else None,
    )


class AlertsCliTests(unittest.TestCase):
    def test_human_output_lists_alert(self) -> None:
        with patch("genkei.cli.alerts.query_alerts", return_value=[_alert()]) as q:
            result = runner.invoke(app, ["alerts"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("NVDA", result.stdout)
        self.assertIn("critical", result.stdout)
        self.assertIn("critical_equity_exit", result.stdout)
        q.assert_called_once()

    def test_empty_message(self) -> None:
        with patch("genkei.cli.alerts.query_alerts", return_value=[]):
            result = runner.invoke(app, ["alerts"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("No alerts", result.stdout)

    def test_json_output(self) -> None:
        with patch("genkei.cli.alerts.query_alerts", return_value=[_alert(notified=True)]):
            result = runner.invoke(app, ["alerts", "--json"])
        self.assertEqual(result.exit_code, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["asset"], "NVDA")
        self.assertEqual(payload[0]["severity"], "critical")
        self.assertEqual(payload[0]["horizon_tag"], "equity:core")
        self.assertIsNotNone(payload[0]["notified_at"])

    def test_severity_filter_passed_through(self) -> None:
        with patch("genkei.cli.alerts.query_alerts", return_value=[]) as q:
            result = runner.invoke(app, ["alerts", "--severity", "warning"])
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(q.call_args.kwargs["severity"], "warning")

    def test_invalid_severity_rejected(self) -> None:
        result = runner.invoke(app, ["alerts", "--severity", "loud"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("--severity must be one of", _combined_output(result))

    def test_invalid_status_rejected(self) -> None:
        result = runner.invoke(app, ["alerts", "--status", "snoozed"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("--status must be one of", _combined_output(result))

    def test_since_after_until_rejected(self) -> None:
        result = runner.invoke(
            app, ["alerts", "--since", "2026-07-10", "--until", "2026-07-01"]
        )
        self.assertNotEqual(result.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
