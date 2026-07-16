"""Unit tests for the Discord notification hook (B-068)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from genkei.experiments import alert_notify
from genkei.experiments.alert_engine import Alert


def _alert(*, severity: str = "critical", asset: str = "NVDA", score: str = "2.0") -> Alert:
    return Alert(
        alert_rule="critical_equity_exit",
        correlation_rule="broad_exit",
        asset=asset,
        asset_class="equity",
        horizon="equity:core",
        direction="bearish",
        severity=severity,
        score=Decimal(score),
        distinct_sources=2,
        triggered_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
        fingerprint=f"critical_equity_exit:broad_exit:{asset}:equity:core:2026-07-10",
        payload={},
        alert_id=1,
    )


class EmbedTests(unittest.TestCase):
    def test_color_matches_loudest_severity(self) -> None:
        embed = alert_notify.build_embed([_alert(severity="warning"), _alert(severity="critical")])
        self.assertEqual(embed["color"], alert_notify._SEVERITY_COLOR["critical"])
        self.assertIn("critical", embed["title"])

    def test_body_lists_each_alert(self) -> None:
        embed = alert_notify.build_embed([_alert(asset="NVDA"), _alert(asset="AAPL")])
        self.assertIn("NVDA", embed["description"])
        self.assertIn("AAPL", embed["description"])

    def test_info_only_batch_uses_info_color(self) -> None:
        embed = alert_notify.build_embed([_alert(severity="info")])
        self.assertEqual(embed["color"], alert_notify._SEVERITY_COLOR["info"])


class PostTests(unittest.TestCase):
    def test_no_webhook_is_noop(self) -> None:
        self.assertFalse(alert_notify.post_alerts([_alert()], webhook_url=None))
        self.assertFalse(alert_notify.post_alerts([_alert()], webhook_url=""))

    def test_no_alerts_is_noop(self) -> None:
        self.assertFalse(alert_notify.post_alerts([], webhook_url="https://x"))

    def test_successful_post_returns_true(self) -> None:
        resp = MagicMock()
        resp.getcode.return_value = 204
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        with patch.object(alert_notify.urllib.request, "urlopen", return_value=resp) as mock_open:
            ok = alert_notify.post_alerts([_alert()], webhook_url="https://discord/webhook")
        self.assertTrue(ok)
        mock_open.assert_called_once()

    def test_non_2xx_returns_false(self) -> None:
        resp = MagicMock()
        resp.getcode.return_value = 500
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        with patch.object(alert_notify.urllib.request, "urlopen", return_value=resp):
            ok = alert_notify.post_alerts([_alert()], webhook_url="https://discord/webhook")
        self.assertFalse(ok)

    def test_transport_error_returns_false(self) -> None:
        import urllib.error

        with patch.object(
            alert_notify.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("boom"),
        ):
            ok = alert_notify.post_alerts([_alert()], webhook_url="https://discord/webhook")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
