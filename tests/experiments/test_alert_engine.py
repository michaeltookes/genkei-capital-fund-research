"""Unit tests for the pure alert evaluator (B-068)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from genkei.experiments.alert_engine import (
    Alert,
    AlertRule,
    _fingerprint,
    _notify,
    evaluate_alerts,
)
from genkei.experiments.signal_store import SignalEvent, Stack


def _dt(day: int, month: int = 7, year: int = 2026) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def _ev(source: str, kind: str, ts: datetime, direction: str = "bearish") -> SignalEvent:
    return SignalEvent(
        asset="NVDA",
        asset_class="equity",
        horizon="equity:core",
        ts=ts,
        source=source,
        signal_kind=kind,
        direction=direction,
        strength=Decimal("1.0"),
        payload={},
        source_ref=f"{source}:x",
    )


def _stack(
    *,
    rule_name: str = "broad_exit",
    asset: str = "NVDA",
    asset_class: str = "equity",
    direction: str = "bearish",
    horizon: str = "equity:core",
    score: str = "2.0",
    distinct_sources: int = 2,
    end_day: int = 10,
) -> Stack:
    ts = _dt(end_day)
    return Stack(
        rule_name=rule_name,
        asset=asset,
        asset_class=asset_class,
        direction=direction,
        window_start=ts,
        window_end=ts,
        score=Decimal(score),
        distinct_sources=distinct_sources,
        event_count=2,
        horizon=horizon,
        events=[_ev("insider_clusters", "sell_cluster", ts, direction)],
    )


def _alert_row(alert_id: int, asset: str = "NVDA") -> Alert:
    return Alert(
        alert_rule="critical_equity_exit",
        correlation_rule="broad_exit",
        asset=asset,
        asset_class="equity",
        horizon="equity:core",
        direction="bearish",
        severity="critical",
        score=Decimal("2.0"),
        distinct_sources=2,
        triggered_at=_dt(10),
        fingerprint=f"critical_equity_exit:broad_exit:{asset}:equity:core:2026-07-10",
        payload={},
        alert_id=alert_id,
    )


_BEARISH_EQUITY = AlertRule(
    name="critical_equity_exit",
    description="test",
    severity="critical",
    match_asset_classes=("equity",),
    match_directions=("bearish",),
    min_score=Decimal("1.8"),
    min_distinct_sources=2,
)


class MatchTests(unittest.TestCase):
    def test_qualifying_stack_produces_alert(self) -> None:
        alerts = evaluate_alerts([_stack(score="2.0")], [_BEARISH_EQUITY])
        self.assertEqual(len(alerts), 1)
        a = alerts[0]
        self.assertEqual(a.alert_rule, "critical_equity_exit")
        self.assertEqual(a.correlation_rule, "broad_exit")
        self.assertEqual(a.severity, "critical")
        self.assertEqual(a.asset, "NVDA")

    def test_below_min_score_filtered(self) -> None:
        alerts = evaluate_alerts([_stack(score="1.5")], [_BEARISH_EQUITY])
        self.assertEqual(alerts, [])

    def test_below_min_distinct_sources_filtered(self) -> None:
        alerts = evaluate_alerts([_stack(distinct_sources=1)], [_BEARISH_EQUITY])
        self.assertEqual(alerts, [])

    def test_wrong_direction_filtered(self) -> None:
        alerts = evaluate_alerts([_stack(direction="bullish")], [_BEARISH_EQUITY])
        self.assertEqual(alerts, [])

    def test_wrong_asset_class_filtered(self) -> None:
        alerts = evaluate_alerts([_stack(asset_class="crypto")], [_BEARISH_EQUITY])
        self.assertEqual(alerts, [])

    def test_rule_name_filter(self) -> None:
        rule = AlertRule(
            name="only_deterioration",
            description="",
            severity="warning",
            match_rules=("deterioration_stack",),
        )
        matched = evaluate_alerts([_stack(rule_name="deterioration_stack")], [rule])
        self.assertEqual(len(matched), 1)
        missed = evaluate_alerts([_stack(rule_name="broad_exit")], [rule])
        self.assertEqual(missed, [])

    def test_horizon_filter(self) -> None:
        rule = AlertRule(
            name="tactical_only",
            description="",
            severity="info",
            match_horizons=("crypto:tactical",),
        )
        matched = evaluate_alerts(
            [_stack(horizon="crypto:tactical", asset_class="crypto")], [rule]
        )
        self.assertEqual(len(matched), 1)
        missed = evaluate_alerts([_stack(horizon="equity:core")], [rule])
        self.assertEqual(missed, [])

    def test_empty_match_filters_match_everything(self) -> None:
        rule = AlertRule(name="catch_all", description="", severity="info")
        alerts = evaluate_alerts(
            [_stack(direction="bullish", asset_class="crypto")], [rule]
        )
        self.assertEqual(len(alerts), 1)


class MultiRuleTests(unittest.TestCase):
    def test_one_stack_can_trip_multiple_rules(self) -> None:
        catch_all = AlertRule(name="any_bearish", description="", severity="info")
        alerts = evaluate_alerts([_stack(score="2.0")], [_BEARISH_EQUITY, catch_all])
        names = sorted(a.alert_rule for a in alerts)
        self.assertEqual(names, ["any_bearish", "critical_equity_exit"])
        # Distinct fingerprints (different alert_rule component).
        self.assertEqual(len({a.fingerprint for a in alerts}), 2)

    def test_dedup_within_batch_keeps_highest_score(self) -> None:
        # Two stacks, same alert_rule/asset/horizon/day → same fingerprint.
        # The evaluator keeps the higher-scoring one.
        strong = _stack(score="3.0", end_day=10)
        weak = _stack(score="2.0", end_day=10)
        alerts = evaluate_alerts([weak, strong], [_BEARISH_EQUITY])
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].score, Decimal("3.0"))

    def test_sorted_freshest_strongest_first(self) -> None:
        older = _stack(asset="AAPL", score="2.0", end_day=5)
        newer = _stack(asset="NVDA", score="2.0", end_day=12)
        alerts = evaluate_alerts([older, newer], [_BEARISH_EQUITY])
        self.assertEqual([a.asset for a in alerts], ["NVDA", "AAPL"])


class FingerprintTests(unittest.TestCase):
    def test_fingerprint_stable_across_intraday_times(self) -> None:
        morning = datetime(2026, 7, 10, 3, 0, tzinfo=timezone.utc)
        evening = datetime(2026, 7, 10, 21, 30, tzinfo=timezone.utc)
        fp_a = _fingerprint(
            alert_rule="r", correlation_rule="c", asset="NVDA",
            horizon="equity:core", triggered_at=morning,
        )
        fp_b = _fingerprint(
            alert_rule="r", correlation_rule="c", asset="NVDA",
            horizon="equity:core", triggered_at=evening,
        )
        self.assertEqual(fp_a, fp_b)

    def test_fingerprint_differs_by_day(self) -> None:
        fp_a = _fingerprint(
            alert_rule="r", correlation_rule="c", asset="NVDA",
            horizon="equity:core", triggered_at=_dt(10),
        )
        fp_b = _fingerprint(
            alert_rule="r", correlation_rule="c", asset="NVDA",
            horizon="equity:core", triggered_at=_dt(11),
        )
        self.assertNotEqual(fp_a, fp_b)

    def test_payload_captures_stack_events(self) -> None:
        alerts = evaluate_alerts([_stack(score="2.0")], [_BEARISH_EQUITY])
        payload = alerts[0].payload
        self.assertEqual(payload["correlation_rule"], "broad_exit")
        self.assertEqual(payload["distinct_sources"], 2)
        self.assertEqual(len(payload["events"]), 1)
        self.assertEqual(payload["events"][0]["source"], "insider_clusters")


class NotifyTests(unittest.TestCase):
    def test_notify_marks_only_delivered_alert_ids(self) -> None:
        delivered = _alert_row(11, asset="NVDA")
        undelivered = _alert_row(12, asset="AAPL")
        conn = MagicMock()
        cm = MagicMock()
        cm.__enter__.return_value = conn
        cm.__exit__.return_value = False

        with patch(
            "genkei.experiments.alert_notify.post_alert_batches",
            return_value=[delivered],
        ), patch(
            "genkei.experiments.alert_engine.db.connection",
            return_value=cm,
        ), patch(
            "genkei.experiments.alert_engine.mark_notified",
            return_value=1,
        ) as mock_mark:
            notified = _notify(
                [delivered, undelivered],
                webhook_url="https://discord/webhook",
            )

        self.assertEqual(notified, 1)
        mock_mark.assert_called_once_with(conn, [11])
        conn.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
