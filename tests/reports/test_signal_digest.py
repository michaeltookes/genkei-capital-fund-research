"""Offline tests for the weekly signal digest renderer (D-024 / B-122).

The renderer is pure — these build synthetic ``Stack`` / ``SignalEvent``
objects and health rows, so no Postgres is required.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from genkei.experiments.signal_store import SignalEvent, Stack
from genkei.reports.signal_digest import (
    build_weekly_digest,
    render_weekly_digest,
    write_digest,
)

SINCE = date(2026, 6, 12)
UNTIL = date(2026, 6, 19)
GEN_AT = datetime(2026, 6, 19, 14, 30, 0, tzinfo=timezone.utc)


def _event(source: str, kind: str) -> SignalEvent:
    return SignalEvent(
        asset="solana",
        asset_class="crypto",
        horizon="crypto:core",
        ts=datetime(2026, 6, 18, 0, 0, tzinfo=timezone.utc),
        source=source,
        signal_kind=kind,
        direction="bearish",
        strength=Decimal("0.5"),
        payload={},
        source_ref="solana:2026-06-18",
    )


def _stack(
    *,
    asset: str = "solana",
    asset_class: str = "crypto",
    horizon: str = "crypto:core",
    direction: str = "bearish",
    rule: str = "crypto_tvl_stress_combo",
    score: str = "1.42",
    n_sources: int = 2,
    events: list | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> Stack:
    evs = events if events is not None else [
        _event("tvl_drawdown", "tvl_drawdown_stress"),
        _event("relative_strength", "laggard_crossing"),
    ]
    return Stack(
        rule_name=rule,
        asset=asset,
        asset_class=asset_class,
        direction=direction,
        window_start=window_start or datetime(2026, 6, 14, 0, 0, tzinfo=timezone.utc),
        window_end=window_end or datetime(2026, 6, 18, 0, 0, tzinfo=timezone.utc),
        score=Decimal(score),
        distinct_sources=n_sources,
        event_count=len(evs),
        horizon=horizon,
        events=evs,
    )


class RenderEmptyTests(unittest.TestCase):
    def test_no_stacks_states_the_gap_and_omits_horizon_sections(self):
        md = render_weekly_digest(
            [], since=SINCE, until=UNTIL, generated_at=GEN_AT
        )
        self.assertIn("**0** stack(s)", md)
        self.assertIn("No cross-source stacks qualified this week", md)
        # No horizon section headers when empty
        self.assertNotIn("stack(s)\n\n| window_end", md)
        # Window + generated timestamp render
        self.assertIn("2026-06-12 → 2026-06-19 (7 days)", md)
        self.assertIn("2026-06-19T14:30:00Z", md)


class RenderStacksTests(unittest.TestCase):
    def test_groups_by_horizon_and_counts_direction(self):
        stacks = [
            _stack(direction="bearish", horizon="crypto:core"),
            _stack(
                asset="CRM",
                asset_class="equity",
                horizon="equity:core",
                direction="bullish",
                rule="insider_plus_8k",
                events=[_event("insider_clusters", "buy_cluster")],
                n_sources=2,
            ),
        ]
        md = render_weekly_digest(stacks, since=SINCE, until=UNTIL, generated_at=GEN_AT)
        self.assertIn("**2** stack(s): 1 bullish / 1 bearish", md)
        self.assertIn("## `crypto:core` — 1 stack(s)", md)
        self.assertIn("## `equity:core` — 1 stack(s)", md)
        # Stack rows render the rule + asset + score
        self.assertIn("crypto_tvl_stress_combo", md)
        self.assertIn("| solana |", md)
        self.assertIn("1.42", md)
        self.assertIn("tvl_drawdown/tvl_drawdown_stress", md)

    def test_benchmark_column_present_only_when_contexts_given(self):
        stacks = [_stack()]
        without = render_weekly_digest(
            stacks, since=SINCE, until=UNTIL, generated_at=GEN_AT
        )
        self.assertNotIn("vs_bench", without)

        ctxs = [SimpleNamespace(abnormal_pct=-3.1)]
        with_bench = render_weekly_digest(
            stacks,
            benchmark_contexts=ctxs,
            since=SINCE,
            until=UNTIL,
            generated_at=GEN_AT,
        )
        self.assertIn("vs_bench", with_bench)
        self.assertIn("-3.10pp", with_bench)

    def test_benchmark_none_renders_na(self):
        stacks = [_stack()]
        ctxs = [SimpleNamespace(abnormal_pct=None)]
        md = render_weekly_digest(
            stacks,
            benchmark_contexts=ctxs,
            since=SINCE,
            until=UNTIL,
            generated_at=GEN_AT,
        )
        self.assertIn("n/a", md)


class RenderMacroOverlayTests(unittest.TestCase):
    def test_macro_column_present_only_when_contexts_given(self):
        stacks = [_stack(direction="bearish")]
        without = render_weekly_digest(
            stacks, since=SINCE, until=UNTIL, generated_at=GEN_AT
        )
        self.assertNotIn("| macro |", without)

        ctxs = [SimpleNamespace(regime="risk_off", alignment="corroborates")]
        with_macro = render_weekly_digest(
            stacks,
            macro_contexts=ctxs,
            since=SINCE,
            until=UNTIL,
            generated_at=GEN_AT,
        )
        self.assertIn(" macro |", with_macro)
        self.assertIn("risk_off ✓", with_macro)

    def test_contradicting_regime_renders_cross(self):
        stacks = [_stack(direction="bullish")]
        ctxs = [SimpleNamespace(regime="risk_off", alignment="contradicts")]
        md = render_weekly_digest(
            stacks, macro_contexts=ctxs, since=SINCE, until=UNTIL, generated_at=GEN_AT
        )
        self.assertIn("risk_off ✗", md)

    def test_unknown_macro_renders_na(self):
        stacks = [_stack()]
        ctxs = [SimpleNamespace(regime=None, alignment="unknown")]
        md = render_weekly_digest(
            stacks, macro_contexts=ctxs, since=SINCE, until=UNTIL, generated_at=GEN_AT
        )
        # The macro cell is n/a when there's no regime.
        self.assertIn(" macro |", md)
        self.assertIn("n/a", md)

    def test_current_regime_header_rendered(self):
        regime = SimpleNamespace(regime="risk_on", ts=date(2026, 6, 16))
        md = render_weekly_digest(
            [_stack()],
            current_regime=regime,
            since=SINCE,
            until=UNTIL,
            generated_at=GEN_AT,
        )
        self.assertIn("**Macro regime:** `risk_on` (as of 2026-06-16)", md)

    def test_no_current_regime_omits_header(self):
        md = render_weekly_digest(
            [_stack()], since=SINCE, until=UNTIL, generated_at=GEN_AT
        )
        self.assertNotIn("**Macro regime:**", md)


class RenderHealthTests(unittest.TestCase):
    def test_all_ok_summarized_without_table(self):
        rows = [
            {"source": "defillama", "endpoint": "collect", "age_hours": 2.0, "health_status": "OK"},
            {"source": "yahoo", "endpoint": "collect", "age_hours": 3.0, "health_status": "OK"},
        ]
        md = render_weekly_digest(
            [_stack()], health_rows=rows, since=SINCE, until=UNTIL, generated_at=GEN_AT
        )
        self.assertIn("2 source(s) tracked · 2 OK · 0 needs attention.", md)
        self.assertNotIn("| source | endpoint | age (h) | status |", md)

    def test_non_ok_rows_listed(self):
        rows = [
            {"source": "defillama", "endpoint": "collect", "age_hours": 2.0, "health_status": "OK"},
            {"source": "cftc", "endpoint": "collect", "age_hours": 220.0, "health_status": "STALE"},
        ]
        md = render_weekly_digest(
            [_stack()], health_rows=rows, since=SINCE, until=UNTIL, generated_at=GEN_AT
        )
        self.assertIn("1 OK · 1 needs attention", md)
        self.assertIn("| cftc | collect | 220.0 | STALE |", md)

    def test_missing_health_is_graceful(self):
        md = render_weekly_digest(
            [_stack()], health_rows=None, since=SINCE, until=UNTIL, generated_at=GEN_AT
        )
        self.assertIn("Health snapshot unavailable", md)


class WriteDigestTests(unittest.TestCase):
    def test_writes_dated_file(self):
        md = render_weekly_digest([_stack()], since=SINCE, until=UNTIL, generated_at=GEN_AT)
        with tempfile.TemporaryDirectory() as tmp:
            out = write_digest(md, UNTIL, output_dir=Path(tmp) / "signals")
            self.assertEqual(out.name, "weekly-2026-06-19.md")
            self.assertTrue(out.exists())
            self.assertEqual(out.read_text(encoding="utf-8"), md)


class BuildWeeklyDigestTests(unittest.TestCase):
    def test_fetches_prior_rule_window_but_renders_current_window_stacks(self):
        rule = SimpleNamespace(window_days=90)
        current_stack = _stack(
            asset="CRM",
            asset_class="equity",
            horizon="equity:core",
            window_start=datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 6, 18, 0, 0, tzinfo=timezone.utc),
        )
        old_stack = _stack(
            asset="OLD",
            asset_class="equity",
            horizon="equity:core",
            window_start=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc),
        )
        events = [_event("insider_clusters", "sell_cluster")]

        with (
            patch("genkei.experiments.signal_rules.load_rules", return_value=[rule]),
            patch("genkei.experiments.signal_store.query_events", return_value=events) as query,
            patch(
                "genkei.experiments.signal_store.detect_stacks",
                return_value=[current_stack, old_stack],
            ) as detect,
            patch("genkei.cli.watchlist._query_source_health", return_value=[]),
            patch("genkei.cli.watchlist._with_health_status", return_value=[]),
        ):
            markdown, rendered_until = build_weekly_digest(
                since=SINCE,
                until=UNTIL,
                benchmark=False,
                macro_overlay=False,
            )

        since_dt = datetime.combine(SINCE, datetime.min.time(), tzinfo=timezone.utc)
        until_dt = datetime.combine(UNTIL, datetime.max.time(), tzinfo=timezone.utc)
        query.assert_called_once_with(since=since_dt - timedelta(days=90), until=until_dt)
        detect.assert_called_once_with(events, [rule])
        self.assertEqual(rendered_until, UNTIL)
        self.assertIn("| CRM |", markdown)
        self.assertNotIn("| OLD |", markdown)
        self.assertIn("2026-06-12 → 2026-06-19", markdown)


if __name__ == "__main__":
    unittest.main()
