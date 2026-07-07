"""Unit tests for the pure correlator + rule loader (B-064)."""

from __future__ import annotations

import textwrap
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from genkei.experiments.signal_rules import load_rules, parse_rules
from genkei.experiments.signal_store import (
    CorrelationRule,
    RuleComponent,
    SignalEvent,
    _filter_by_rule,
    _score_window,
    detect_stacks,
)


def _ev(
    *,
    asset: str = "AAPL",
    asset_class: str = "equity",
    ts: datetime,
    source: str,
    signal_kind: str,
    direction: str = "bullish",
    horizon: str = "equity:core",
    strength: Decimal | None = Decimal("1.0"),
    source_ref: str | None = None,
) -> SignalEvent:
    return SignalEvent(
        asset=asset,
        asset_class=asset_class,
        horizon=horizon,
        ts=ts,
        source=source,
        signal_kind=signal_kind,
        direction=direction,
        strength=strength,
        payload={},
        source_ref=source_ref,
    )


def _dt(day: int, month: int = 5, year: int = 2026) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


_W = Decimal  # ruff line-length squeeze without losing readability
SMART_MONEY_RULE = CorrelationRule(
    name="smart_money_buy",
    description="test rule",
    direction="bullish",
    components=[
        RuleComponent(source="insider_clusters", signal_kind="buy_cluster", weight=_W("1.0")),
        RuleComponent(source="crowding", signal_kind="crowding_add", weight=_W("1.0")),
        RuleComponent(source="eight_k_impact", signal_kind="item_1_01", weight=_W("0.6")),
    ],
    window_days=7,
    min_score=Decimal("1.5"),
    min_distinct_sources=2,
)

# Same two-source shape as SMART_MONEY_RULE but with a 7-day half-life and a
# wide window, so B-099 age-decay actually bites. min_score/min_distinct kept
# permissive so ScoreWindowTests can read the raw decayed score.
DECAY_RULE = CorrelationRule(
    name="decay_rule",
    description="test rule with age-decay",
    direction="bullish",
    components=[
        RuleComponent(source="insider_clusters", signal_kind="buy_cluster", weight=_W("1.0")),
        RuleComponent(source="crowding", signal_kind="crowding_add", weight=_W("1.0")),
    ],
    window_days=90,
    min_score=Decimal("0.1"),
    min_distinct_sources=1,
    decay_half_life_days=Decimal("7"),
)


class FilterByRuleTests(unittest.TestCase):
    def test_filters_to_components_and_direction(self) -> None:
        events = [
            _ev(ts=_dt(5), source="insider_clusters", signal_kind="buy_cluster"),
            _ev(ts=_dt(5), source="crowding", signal_kind="crowding_add"),
            # Wrong direction — should be filtered out.
            _ev(
                ts=_dt(5),
                source="insider_clusters",
                signal_kind="buy_cluster",
                direction="bearish",
            ),
            # Source not in rule.
            _ev(ts=_dt(5), source="macro_regime", signal_kind="risk_on"),
            # Source in rule but kind not configured.
            _ev(ts=_dt(5), source="insider_clusters", signal_kind="sell_cluster"),
        ]
        filtered = _filter_by_rule(events, SMART_MONEY_RULE)
        kinds = sorted((e.source, e.signal_kind) for e in filtered)
        self.assertEqual(
            kinds,
            [("crowding", "crowding_add"), ("insider_clusters", "buy_cluster")],
        )

    def test_wildcard_signal_kind_matches_any_kind_from_source(self) -> None:
        # A rule with signal_kind=None for `8k_impact` should match every
        # 8-K item code emitted under that source.
        wildcard_rule = CorrelationRule(
            name="any_8k",
            description="",
            direction="bullish",
            components=[
                RuleComponent(source="eight_k_impact", signal_kind=None, weight=Decimal("0.5")),
            ],
            window_days=7,
            min_score=Decimal("0.4"),
            min_distinct_sources=1,
        )
        events = [
            _ev(ts=_dt(5), source="eight_k_impact", signal_kind="item_2_02"),
            _ev(ts=_dt(5), source="eight_k_impact", signal_kind="item_8_01"),
        ]
        filtered = _filter_by_rule(events, wildcard_rule)
        self.assertEqual(len(filtered), 2)

    def test_filters_to_rule_horizon(self) -> None:
        events = [
            _ev(ts=_dt(5), source="insider_clusters", signal_kind="buy_cluster"),
            _ev(
                ts=_dt(5),
                source="crowding",
                signal_kind="crowding_add",
                horizon="equity:tactical",
            ),
        ]
        filtered = _filter_by_rule(events, SMART_MONEY_RULE)
        self.assertEqual(
            [(e.source, e.signal_kind) for e in filtered],
            [("insider_clusters", "buy_cluster")],
        )

    def test_different_horizons_dont_combine(self) -> None:
        events = [
            _ev(ts=_dt(5), source="insider_clusters", signal_kind="buy_cluster"),
            _ev(
                ts=_dt(6),
                source="crowding",
                signal_kind="crowding_add",
                horizon="equity:tactical",
            ),
        ]
        self.assertEqual(detect_stacks(events, [SMART_MONEY_RULE]), [])


class ScoreWindowTests(unittest.TestCase):
    def test_score_sums_weight_times_strength(self) -> None:
        events = [
            _ev(
                ts=_dt(5),
                source="insider_clusters",
                signal_kind="buy_cluster",
                strength=Decimal("0.8"),
            ),
            _ev(
                ts=_dt(6),
                source="crowding",
                signal_kind="crowding_add",
                strength=Decimal("1.0"),
            ),
        ]
        score, sources = _score_window(events, SMART_MONEY_RULE)
        # 1.0 * 0.8 + 1.0 * 1.0 = 1.8; two distinct sources.
        self.assertEqual(score, Decimal("1.8"))
        self.assertEqual(sources, 2)

    def test_null_strength_defaults_to_one(self) -> None:
        events = [
            _ev(ts=_dt(5), source="insider_clusters", signal_kind="buy_cluster", strength=None),
        ]
        score, sources = _score_window(events, SMART_MONEY_RULE)
        # Null strength defaults to 1.0 so weight contributes in full.
        self.assertEqual(score, Decimal("1.0"))
        self.assertEqual(sources, 1)

    def test_exact_kind_wins_over_wildcard(self) -> None:
        # A rule with both an exact-kind component AND a wildcard for the
        # same source should let the exact-kind weight win.
        rule = CorrelationRule(
            name="r",
            description="",
            direction="bullish",
            components=[
                RuleComponent(
                    source="eight_k_impact",
                    signal_kind=None,
                    weight=Decimal("0.3"),
                ),
                RuleComponent(
                    source="eight_k_impact",
                    signal_kind="item_1_01",
                    weight=Decimal("1.0"),
                ),
            ],
            window_days=7,
            min_score=Decimal("0.1"),
            min_distinct_sources=1,
        )
        events = [_ev(ts=_dt(5), source="eight_k_impact", signal_kind="item_1_01")]
        score, _ = _score_window(events, rule)
        self.assertEqual(score, Decimal("1.0"))

    def test_same_source_ref_counts_highest_component_once(self) -> None:
        rule = CorrelationRule(
            name="multi_item_8k",
            description="",
            direction="bullish",
            components=[
                RuleComponent(
                    source="eight_k_impact",
                    signal_kind="item_1_01",
                    weight=Decimal("0.6"),
                ),
                RuleComponent(
                    source="eight_k_impact",
                    signal_kind="item_2_02",
                    weight=Decimal("0.5"),
                ),
            ],
            window_days=7,
            min_score=Decimal("0.1"),
            min_distinct_sources=1,
        )
        events = [
            _ev(
                ts=_dt(5),
                source="eight_k_impact",
                signal_kind="item_1_01",
                source_ref="0000320193-26-000001",
            ),
            _ev(
                ts=_dt(5),
                source="eight_k_impact",
                signal_kind="item_2_02",
                source_ref="0000320193-26-000001",
            ),
        ]
        score, sources = _score_window(events, rule)
        self.assertEqual(score, Decimal("0.6"))
        self.assertEqual(sources, 1)

    def test_different_source_refs_still_sum(self) -> None:
        rule = CorrelationRule(
            name="multi_item_8k",
            description="",
            direction="bullish",
            components=[
                RuleComponent(
                    source="eight_k_impact",
                    signal_kind="item_1_01",
                    weight=Decimal("0.6"),
                ),
                RuleComponent(
                    source="eight_k_impact",
                    signal_kind="item_2_02",
                    weight=Decimal("0.5"),
                ),
            ],
            window_days=7,
            min_score=Decimal("0.1"),
            min_distinct_sources=1,
        )
        events = [
            _ev(
                ts=_dt(5),
                source="eight_k_impact",
                signal_kind="item_1_01",
                source_ref="0000320193-26-000001",
            ),
            _ev(
                ts=_dt(5),
                source="eight_k_impact",
                signal_kind="item_2_02",
                source_ref="0000320193-26-000002",
            ),
        ]
        score, sources = _score_window(events, rule)
        self.assertEqual(score, Decimal("1.1"))
        self.assertEqual(sources, 1)


class DecayScoringTests(unittest.TestCase):
    """B-099 — optional exponential age-decay within the scoring window."""

    def test_default_off_preserves_flat_score(self) -> None:
        # SMART_MONEY_RULE has no half-life: two events 7 days apart still
        # each contribute their full weight, regardless of age.
        events = [
            _ev(ts=_dt(5), source="insider_clusters", signal_kind="buy_cluster"),
            _ev(ts=_dt(12), source="crowding", signal_kind="crowding_add"),
        ]
        score, sources = _score_window(events, SMART_MONEY_RULE)
        self.assertEqual(score, Decimal("2.0"))
        self.assertEqual(sources, 2)

    def test_freshest_event_is_undiscounted(self) -> None:
        # A lone event is the window's freshest, so age 0 → full weight even
        # when decay is on.
        events = [_ev(ts=_dt(5), source="insider_clusters", signal_kind="buy_cluster")]
        score, _ = _score_window(events, DECAY_RULE)
        self.assertEqual(score, Decimal("1.0"))

    def test_one_half_life_halves_the_older_leg(self) -> None:
        # crowding@12 is freshest (full 1.0); insider@5 is exactly one
        # half-life (7d) older → 0.5. Flat scoring would give 2.0.
        events = [
            _ev(ts=_dt(5), source="insider_clusters", signal_kind="buy_cluster"),
            _ev(ts=_dt(12), source="crowding", signal_kind="crowding_add"),
        ]
        score, sources = _score_window(events, DECAY_RULE)
        self.assertEqual(score, Decimal("1.5"))
        self.assertEqual(sources, 2)

    def test_reference_is_freshest_regardless_of_call_order(self) -> None:
        # Same events fed newest-first: the reference is still max(ts), so
        # the decayed score is identical to the sorted case.
        events = [
            _ev(ts=_dt(12), source="crowding", signal_kind="crowding_add"),
            _ev(ts=_dt(5), source="insider_clusters", signal_kind="buy_cluster"),
        ]
        score, _ = _score_window(events, DECAY_RULE)
        self.assertEqual(score, Decimal("1.5"))

    def test_three_half_lives_quarters_then_eighths(self) -> None:
        # insider@1 is 21 days (3 half-lives) behind crowding@22 → 0.125.
        events = [
            _ev(ts=_dt(1), source="insider_clusters", signal_kind="buy_cluster"),
            _ev(ts=_dt(22), source="crowding", signal_kind="crowding_add"),
        ]
        score, _ = _score_window(events, DECAY_RULE)
        self.assertEqual(score, Decimal("1.125"))

    def test_decay_can_drop_a_wide_window_stack_below_min_score(self) -> None:
        # The operational point of B-099. A 30-day-stale leg passes a flat
        # rule but decay pulls the total under min_score so no stack emits.
        rule = CorrelationRule(
            name="wide",
            description="",
            direction="bullish",
            components=[
                RuleComponent(
                    source="insider_clusters", signal_kind="buy_cluster", weight=_W("1.0")
                ),
                RuleComponent(source="crowding", signal_kind="crowding_add", weight=_W("1.0")),
            ],
            window_days=90,
            min_score=Decimal("1.6"),
            min_distinct_sources=2,
            decay_half_life_days=Decimal("7"),
        )
        flat = CorrelationRule(
            name="wide_flat",
            description="",
            direction="bullish",
            components=rule.components,
            window_days=90,
            min_score=Decimal("1.6"),
            min_distinct_sources=2,
        )
        events = [
            _ev(ts=_dt(1), source="insider_clusters", signal_kind="buy_cluster"),
            _ev(ts=_dt(22), source="crowding", signal_kind="crowding_add"),
        ]
        # Flat: 1.0 + 1.0 = 2.0 ≥ 1.6 → a stack. Decayed: 1.125 < 1.6 → none.
        self.assertEqual(len(detect_stacks(events, [flat])), 1)
        self.assertEqual(detect_stacks(events, [rule]), [])

    def test_decay_keeps_qualifying_prefix_before_late_weak_event(self) -> None:
        rule = CorrelationRule(
            name="wide",
            description="",
            direction="bullish",
            components=[
                RuleComponent(
                    source="insider_clusters", signal_kind="buy_cluster", weight=_W("1.0")
                ),
                RuleComponent(source="crowding", signal_kind="crowding_add", weight=_W("1.0")),
            ],
            window_days=90,
            min_score=Decimal("1.5"),
            min_distinct_sources=2,
            decay_half_life_days=Decimal("7"),
        )
        events = [
            _ev(ts=_dt(1), source="insider_clusters", signal_kind="buy_cluster"),
            _ev(ts=_dt(8), source="crowding", signal_kind="crowding_add"),
            _ev(
                ts=_dt(29, month=6),
                source="crowding",
                signal_kind="crowding_add",
                strength=Decimal("0.1"),
                source_ref="late",
            ),
        ]
        stacks = detect_stacks(events, [rule])

        self.assertEqual(len(stacks), 1)
        stack = stacks[0]
        self.assertEqual(stack.window_end, _dt(8))
        self.assertEqual(stack.score, Decimal("1.5"))
        self.assertEqual(stack.event_count, 2)
        self.assertNotIn("late", [ev.source_ref for ev in stack.events])

    def test_decay_prefix_emit_skips_rest_of_anchor_window(self) -> None:
        rule = CorrelationRule(
            name="wide",
            description="",
            direction="bullish",
            components=[
                RuleComponent(
                    source="insider_clusters", signal_kind="buy_cluster", weight=_W("1.0")
                ),
                RuleComponent(source="crowding", signal_kind="crowding_add", weight=_W("1.0")),
            ],
            window_days=90,
            min_score=Decimal("1.5"),
            min_distinct_sources=2,
            decay_half_life_days=Decimal("7"),
        )
        events = [
            _ev(ts=_dt(1), source="insider_clusters", signal_kind="buy_cluster"),
            _ev(ts=_dt(8), source="crowding", signal_kind="crowding_add"),
            _ev(
                ts=_dt(9),
                source="insider_clusters",
                signal_kind="buy_cluster",
                source_ref="second-insider",
            ),
            _ev(
                ts=_dt(16),
                source="crowding",
                signal_kind="crowding_add",
                source_ref="second-crowding",
            ),
        ]
        stacks = detect_stacks(events, [rule])

        self.assertEqual(len(stacks), 1)
        self.assertEqual(stacks[0].window_start, _dt(1))
        self.assertEqual(stacks[0].window_end, _dt(8))
        self.assertEqual(stacks[0].event_count, 2)


class DetectStacksTests(unittest.TestCase):
    """End-to-end correlator behaviour on synthetic streams."""

    def test_two_sources_within_window_emits_stack(self) -> None:
        events = [
            _ev(ts=_dt(5), source="insider_clusters", signal_kind="buy_cluster"),
            _ev(ts=_dt(6), source="crowding", signal_kind="crowding_add"),
        ]
        stacks = detect_stacks(events, [SMART_MONEY_RULE])
        self.assertEqual(len(stacks), 1)
        s = stacks[0]
        self.assertEqual(s.asset, "AAPL")
        self.assertEqual(s.direction, "bullish")
        self.assertEqual(s.distinct_sources, 2)
        self.assertEqual(s.event_count, 2)
        self.assertEqual(s.score, Decimal("2.0"))

    def test_single_source_does_not_qualify(self) -> None:
        # Two events from the same source — min_distinct_sources=2 should
        # block the stack from emitting.
        events = [
            _ev(ts=_dt(5), source="insider_clusters", signal_kind="buy_cluster"),
            _ev(
                ts=_dt(6),
                source="insider_clusters",
                signal_kind="buy_cluster",
                source_ref="b",
            ),
        ]
        self.assertEqual(detect_stacks(events, [SMART_MONEY_RULE]), [])

    def test_outside_window_does_not_qualify(self) -> None:
        # Events spaced 8 days apart — rule window is 7d.
        events = [
            _ev(ts=_dt(1), source="insider_clusters", signal_kind="buy_cluster"),
            _ev(ts=_dt(10), source="crowding", signal_kind="crowding_add"),
        ]
        self.assertEqual(detect_stacks(events, [SMART_MONEY_RULE]), [])

    def test_below_min_score_does_not_qualify(self) -> None:
        # The 8-K component weight is 0.6; even paired with crowding (1.0)
        # the score is 1.6 which clears the default 1.5. Below 1.5 needs
        # the 8-K alone with crowding (1.6).
        # Build a rule with min_score=1.7 to force the case.
        rule = CorrelationRule(
            name="r",
            description="",
            direction="bullish",
            components=SMART_MONEY_RULE.components,
            window_days=7,
            min_score=Decimal("1.7"),
            min_distinct_sources=2,
        )
        events = [
            _ev(ts=_dt(5), source="eight_k_impact", signal_kind="item_1_01"),  # 0.6
            _ev(ts=_dt(6), source="crowding", signal_kind="crowding_add"),    # 1.0
        ]
        # 0.6 + 1.0 = 1.6 < 1.7 — no stack.
        self.assertEqual(detect_stacks(events, [rule]), [])

    def test_different_assets_dont_combine(self) -> None:
        # Two assets, one event each — correlator must group by asset
        # so AAPL alone and MSFT alone never combine into a fake stack.
        events = [
            _ev(asset="AAPL", ts=_dt(5), source="insider_clusters", signal_kind="buy_cluster"),
            _ev(asset="MSFT", ts=_dt(5), source="crowding", signal_kind="crowding_add"),
        ]
        self.assertEqual(detect_stacks(events, [SMART_MONEY_RULE]), [])

    def test_same_asset_string_different_asset_classes_dont_combine(self) -> None:
        events = [
            _ev(
                asset="AAPL",
                asset_class="equity",
                ts=_dt(5),
                source="insider_clusters",
                signal_kind="buy_cluster",
            ),
            _ev(
                asset="AAPL",
                asset_class="crypto",
                ts=_dt(5),
                source="crowding",
                signal_kind="crowding_add",
            ),
        ]
        self.assertEqual(detect_stacks(events, [SMART_MONEY_RULE]), [])

    def test_greedy_advance_prevents_overlapping_stacks(self) -> None:
        # Long burst on the same asset within the window — the detector
        # should emit one stack, not three.
        events = [
            _ev(ts=_dt(5), source="insider_clusters", signal_kind="buy_cluster",
                source_ref="c1"),
            _ev(ts=_dt(5), source="crowding", signal_kind="crowding_add"),
            _ev(ts=_dt(6), source="insider_clusters", signal_kind="buy_cluster",
                source_ref="c2"),
            _ev(ts=_dt(7), source="eight_k_impact", signal_kind="item_1_01"),
        ]
        stacks = detect_stacks(events, [SMART_MONEY_RULE])
        self.assertEqual(len(stacks), 1)

    def test_sort_order_recent_strongest_first(self) -> None:
        events = [
            # Older, weaker
            _ev(asset="MSFT", ts=_dt(1), source="insider_clusters",
                signal_kind="buy_cluster", strength=Decimal("0.4")),
            _ev(asset="MSFT", ts=_dt(2), source="crowding",
                signal_kind="crowding_add", strength=Decimal("0.4")),
            # Newer, stronger
            _ev(asset="AAPL", ts=_dt(10), source="insider_clusters",
                signal_kind="buy_cluster", strength=Decimal("1.0")),
            _ev(asset="AAPL", ts=_dt(11), source="crowding",
                signal_kind="crowding_add", strength=Decimal("1.0")),
        ]
        # MSFT stack: 0.4+0.4 = 0.8 < 1.5 — won't qualify; lower min_score.
        rule = CorrelationRule(
            name="r",
            description="",
            direction="bullish",
            components=SMART_MONEY_RULE.components,
            window_days=7,
            min_score=Decimal("0.5"),
            min_distinct_sources=2,
        )
        stacks = detect_stacks(events, [rule])
        self.assertEqual(len(stacks), 2)
        # Most recent first
        self.assertEqual(stacks[0].asset, "AAPL")
        self.assertEqual(stacks[1].asset, "MSFT")

    def test_bearish_rule_doesnt_match_bullish_events(self) -> None:
        bearish_rule = CorrelationRule(
            name="broad_exit",
            description="",
            direction="bearish",
            components=[
                RuleComponent(
                    source="insider_clusters", signal_kind="sell_cluster",
                    weight=Decimal("1.0"),
                ),
                RuleComponent(
                    source="crowding", signal_kind="crowding_exit",
                    weight=Decimal("1.0"),
                ),
            ],
            window_days=90,
            min_score=Decimal("1.5"),
            min_distinct_sources=2,
        )
        events = [
            _ev(ts=_dt(5), source="insider_clusters", signal_kind="buy_cluster"),
            _ev(ts=_dt(5), source="crowding", signal_kind="crowding_add"),
        ]
        self.assertEqual(detect_stacks(events, [bearish_rule]), [])


# ---------------------------------------------------------------------------
# Rule loader / validator
# ---------------------------------------------------------------------------


GOOD_RULES_YAML = """
version: 1
rules:
  - name: smart_money_buy
    description: Test rule.
    direction: bullish
    window_days: 7
    min_score: 1.5
    components:
      - source: insider_clusters
        signal_kind: buy_cluster
        weight: 1.0
      - source: crowding
        signal_kind: crowding_add
        weight: 1.0
"""


class LoadRulesTests(unittest.TestCase):
    def _write(self, content: str) -> Path:
        ctx = TemporaryDirectory()
        self.addCleanup(ctx.cleanup)
        path = Path(ctx.name) / "rules.yml"
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def test_loads_canonical_rule(self) -> None:
        rules = load_rules(self._write(GOOD_RULES_YAML))
        self.assertEqual(len(rules), 1)
        r = rules[0]
        self.assertEqual(r.name, "smart_money_buy")
        self.assertEqual(r.direction, "bullish")
        self.assertEqual(r.window_days, 7)
        self.assertEqual(r.min_score, Decimal("1.5"))
        self.assertEqual(len(r.components), 2)

    def test_loads_packaged_rules_yaml(self) -> None:
        # Real config file should parse without error and have at least
        # one rule. Pin the test against drift in src/genkei/data/.
        rules = load_rules()
        self.assertGreater(len(rules), 0)
        # Names are unique by contract.
        self.assertEqual(len(rules), len({r.name for r in rules}))

    def test_rejects_unknown_version(self) -> None:
        path = self._write("version: 99\nrules: []\n")
        with self.assertRaises(ValueError):
            load_rules(path)

    def test_rejects_duplicate_rule_names(self) -> None:
        path = self._write(
            """
            version: 1
            rules:
              - name: dupe
                direction: bullish
                components:
                  - source: a
                    weight: 1
              - name: dupe
                direction: bullish
                components:
                  - source: b
                    weight: 1
            """
        )
        with self.assertRaises(ValueError) as cm:
            load_rules(path)
        self.assertIn("duplicate", str(cm.exception))

    def test_rejects_invalid_direction(self) -> None:
        path = self._write(
            """
            version: 1
            rules:
              - name: x
                direction: maybe
                components:
                  - source: s
                    weight: 1
            """
        )
        with self.assertRaises(ValueError):
            load_rules(path)

    def test_rejects_zero_weight(self) -> None:
        path = self._write(
            """
            version: 1
            rules:
              - name: x
                direction: bullish
                components:
                  - source: s
                    weight: 0
            """
        )
        with self.assertRaises(ValueError):
            load_rules(path)

    def test_inline_parse_helper_works(self) -> None:
        rules = parse_rules(
            {
                "version": 1,
                "rules": [
                    {
                        "name": "r",
                        "direction": "neutral",
                        "components": [{"source": "s", "weight": 1}],
                    }
                ],
            }
        )
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].direction, "neutral")

    def test_rejects_boolean_int_fields(self) -> None:
        path = self._write(
            """
            version: 1
            rules:
              - name: x
                direction: bullish
                window_days: true
                components:
                  - source: s
                    weight: 1
            """
        )
        with self.assertRaises(ValueError) as cm:
            load_rules(path)
        self.assertIn("not an integer", str(cm.exception))

    def test_decay_half_life_absent_defaults_to_none(self) -> None:
        # The default-off contract: no decay key → age-blind scoring.
        rules = load_rules(self._write(GOOD_RULES_YAML))
        self.assertIsNone(rules[0].decay_half_life_days)

    def test_decay_half_life_parsed_when_present(self) -> None:
        rules = parse_rules(
            {
                "version": 1,
                "rules": [
                    {
                        "name": "r",
                        "direction": "bullish",
                        "decay_half_life_days": 30,
                        "components": [{"source": "s", "weight": 1}],
                    }
                ],
            }
        )
        self.assertEqual(rules[0].decay_half_life_days, Decimal("30"))

    def test_rejects_non_positive_decay_half_life(self) -> None:
        for bad in (0, -5):
            with self.assertRaises(ValueError) as cm:
                parse_rules(
                    {
                        "version": 1,
                        "rules": [
                            {
                                "name": "r",
                                "direction": "bullish",
                                "decay_half_life_days": bad,
                                "components": [{"source": "s", "weight": 1}],
                            }
                        ],
                    }
                )
            self.assertIn("decay_half_life_days", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
