"""Unit tests for the alert-rules loader/validator (B-068)."""

from __future__ import annotations

import unittest
from decimal import Decimal

from genkei.experiments.alert_rules import (
    DEFAULT_ALERT_RULES_PATH,
    load_alert_rules,
    parse_alert_rules,
)


def _doc(**overrides: object) -> dict:
    rule = {
        "name": "critical_equity_exit",
        "description": "test",
        "severity": "critical",
        "match": {"asset_class": ["equity"], "direction": ["bearish"]},
        "min_score": "1.8",
        "min_distinct_sources": 2,
    }
    rule.update(overrides)
    return {"version": 1, "alerts": [rule]}


class LoadTests(unittest.TestCase):
    def test_shipped_config_loads(self) -> None:
        rules = load_alert_rules(DEFAULT_ALERT_RULES_PATH)
        names = {r.name for r in rules}
        self.assertIn("critical_equity_exit", names)
        # Severities are all valid enum values.
        self.assertTrue(all(r.severity in {"info", "warning", "critical"} for r in rules))

    def test_parse_minimal_valid(self) -> None:
        rules = parse_alert_rules(_doc())
        self.assertEqual(len(rules), 1)
        r = rules[0]
        self.assertEqual(r.name, "critical_equity_exit")
        self.assertEqual(r.severity, "critical")
        self.assertEqual(r.match_asset_classes, ("equity",))
        self.assertEqual(r.match_directions, ("bearish",))
        self.assertEqual(r.min_score, Decimal("1.8"))
        self.assertEqual(r.min_distinct_sources, 2)

    def test_defaults_when_thresholds_absent(self) -> None:
        rules = parse_alert_rules(
            {"version": 1, "alerts": [{"name": "x", "severity": "info"}]}
        )
        self.assertEqual(rules[0].min_score, Decimal("0"))
        self.assertEqual(rules[0].min_distinct_sources, 0)
        self.assertEqual(rules[0].match_asset_classes, ())


class ValidationTests(unittest.TestCase):
    def test_bad_version_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported version"):
            parse_alert_rules({"version": 2, "alerts": []})

    def test_alerts_must_be_list(self) -> None:
        with self.assertRaisesRegex(ValueError, "`alerts` must be a list"):
            parse_alert_rules({"version": 1, "alerts": {}})

    def test_missing_name_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing `name`"):
            parse_alert_rules({"version": 1, "alerts": [{"severity": "info"}]})

    def test_invalid_severity_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid severity"):
            parse_alert_rules(_doc(severity="loud"))

    def test_unknown_match_key_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown keys"):
            parse_alert_rules(_doc(match={"asset_klass": ["equity"]}))

    def test_invalid_asset_class_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "match.asset_class has invalid"):
            parse_alert_rules(_doc(match={"asset_class": ["stonks"]}))

    def test_invalid_direction_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "match.direction has invalid"):
            parse_alert_rules(_doc(match={"direction": ["sideways"]}))

    def test_non_string_match_list_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "list of non-empty strings"):
            parse_alert_rules(_doc(match={"rules": [123]}))

    def test_negative_min_score_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "min_score must be >= 0"):
            parse_alert_rules(_doc(min_score="-1"))

    def test_negative_min_distinct_sources_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be >= 0"):
            parse_alert_rules(_doc(min_distinct_sources=-1))

    def test_duplicate_name_rejected(self) -> None:
        doc = _doc()
        doc["alerts"].append(dict(doc["alerts"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate alert name"):
            parse_alert_rules(doc)


if __name__ == "__main__":
    unittest.main()
