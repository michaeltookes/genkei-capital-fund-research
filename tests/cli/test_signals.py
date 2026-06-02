"""Unit tests for `genkei signals` (B-064)."""

from __future__ import annotations

import io
import json as json_mod
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from genkei.cli import main
from genkei.cli.signals import (
    _format_events_human,
    _format_stacks_human,
    _stack_to_dict,
)
from genkei.experiments.signal_store import (
    SignalEvent,
    Stack,
)


def _event(
    *,
    asset: str = "AAPL",
    asset_class: str = "equity",
    ts: datetime,
    source: str,
    signal_kind: str,
    direction: str = "bullish",
    strength: Decimal | None = Decimal("1.0"),
    source_ref: str | None = None,
) -> SignalEvent:
    return SignalEvent(
        asset=asset,
        asset_class=asset_class,
        horizon="equity:core",
        ts=ts,
        source=source,
        signal_kind=signal_kind,
        direction=direction,
        strength=strength,
        payload={},
        source_ref=source_ref,
    )


def _dt(day: int) -> datetime:
    return datetime(2026, 5, day, tzinfo=timezone.utc)


def _stack(
    *,
    rule: str = "smart_money_buy",
    asset: str = "AAPL",
    events: list[SignalEvent] | None = None,
) -> Stack:
    evs = events or [
        _event(ts=_dt(5), source="insider_clusters", signal_kind="buy_cluster"),
        _event(ts=_dt(6), source="crowding", signal_kind="crowding_add"),
    ]
    return Stack(
        rule_name=rule,
        asset=asset,
        asset_class="equity",
        horizon="equity:core",
        direction="bullish",
        window_start=evs[0].ts,
        window_end=evs[-1].ts,
        score=Decimal("2.0"),
        distinct_sources=len({e.source for e in evs}),
        event_count=len(evs),
        events=evs,
    )


MINIMAL_RULES_YAML = """\
version: 1
rules:
  - name: smart_money_buy
    direction: bullish
    components:
      - source: insider_clusters
        signal_kind: buy_cluster
        weight: 1.0
      - source: crowding
        signal_kind: crowding_add
        weight: 1.0
    window_days: 7
    min_score: 1.5
    min_distinct_sources: 2
"""


def _rules_path(case: unittest.TestCase) -> Path:
    ctx = TemporaryDirectory()
    case.addCleanup(ctx.cleanup)
    path = Path(ctx.name) / "rules.yml"
    path.write_text(MINIMAL_RULES_YAML, encoding="utf-8")
    return path


class StackToDictTests(unittest.TestCase):
    def test_serializes_all_fields(self) -> None:
        s = _stack()
        d = _stack_to_dict(s)
        self.assertEqual(d["rule"], "smart_money_buy")
        self.assertEqual(d["asset"], "AAPL")
        self.assertEqual(d["horizon_tag"], "equity:core")
        self.assertEqual(d["direction"], "bullish")
        self.assertEqual(d["distinct_sources"], 2)
        self.assertEqual(d["event_count"], 2)
        # Decimal survives until json.dumps (default=_json_default handles it)
        self.assertEqual(d["score"], Decimal("2.0"))
        self.assertEqual(len(d["events"]), 2)


class FormatHumanTests(unittest.TestCase):
    def test_empty_stacks_points_at_emitter(self) -> None:
        text = _format_stacks_human([])
        self.assertIn("No stacks found", text)
        self.assertIn("insider_clusters_emitter", text)

    def test_renders_stack_row(self) -> None:
        text = _format_stacks_human([_stack()])
        self.assertIn("AAPL", text)
        self.assertIn("smart_money_buy", text)
        self.assertIn("equity:core", text)
        self.assertIn("bullish", text)
        self.assertIn("insider_clusters/buy_cluster", text)
        self.assertIn("crowding/crowding_add", text)

    def test_empty_events_points_at_emitter(self) -> None:
        text = _format_events_human([])
        self.assertIn("No signal events", text)
        self.assertIn("insider_clusters_emitter", text)

    def test_renders_events_with_strength(self) -> None:
        evs = [
            _event(ts=_dt(5), source="insider_clusters", signal_kind="buy_cluster"),
            _event(
                ts=_dt(6),
                source="crowding",
                signal_kind="crowding_add",
                strength=None,
            ),
        ]
        text = _format_events_human(evs)
        self.assertIn("AAPL", text)
        self.assertIn("equity:core", text)
        self.assertIn("insider_clusters", text)
        # Null strength renders as n/a
        self.assertIn("n/a", text)


class ValidationTests(unittest.TestCase):
    def test_since_after_until_rejected(self) -> None:
        rpath = _rules_path(self)
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = main(
                [
                    "signals",
                    "--since",
                    "2026-06-01",
                    "--until",
                    "2026-01-01",
                    "--rules-path",
                    str(rpath),
                ]
            )
        self.assertEqual(code, 2)
        msg = re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue())
        self.assertIn("Invalid value", msg)
        self.assertIn("since", msg)
        self.assertIn("on or before", msg)
        self.assertIn("until", msg)

    def test_unknown_direction_rejected(self) -> None:
        rpath = _rules_path(self)
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = main(
                [
                    "signals",
                    "--direction",
                    "sideways",
                    "--rules-path",
                    str(rpath),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("bullish", buf.getvalue())

    def test_unknown_rule_rejected(self) -> None:
        rpath = _rules_path(self)
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = main(
                ["signals", "--rule", "nope", "--rules-path", str(rpath)]
            )
        self.assertEqual(code, 2)
        self.assertIn("No rule named", buf.getvalue())


class EndToEndTests(unittest.TestCase):
    """Mock the lake reader and exercise the typer command."""

    def test_default_runs_correlator_and_renders_stack(self) -> None:
        rpath = _rules_path(self)
        sample = [
            _event(ts=_dt(5), source="insider_clusters", signal_kind="buy_cluster"),
            _event(ts=_dt(6), source="crowding", signal_kind="crowding_add"),
        ]
        out = io.StringIO()
        with (
            patch("genkei.cli.signals.query_events", return_value=sample),
            redirect_stdout(out),
        ):
            code = main(["signals", "--no-benchmark", "--rules-path", str(rpath)])
        self.assertEqual(code, 0)
        self.assertIn("AAPL", out.getvalue())
        self.assertIn("smart_money_buy", out.getvalue())

    def test_json_serializes_decimals_as_strings(self) -> None:
        rpath = _rules_path(self)
        sample = [
            _event(ts=_dt(5), source="insider_clusters", signal_kind="buy_cluster"),
            _event(ts=_dt(6), source="crowding", signal_kind="crowding_add"),
        ]
        out = io.StringIO()
        with (
            patch("genkei.cli.signals.query_events", return_value=sample),
            redirect_stdout(out),
        ):
            code = main(
                ["signals", "--no-benchmark", "--json", "--rules-path", str(rpath)]
            )
        self.assertEqual(code, 0)
        data = json_mod.loads(out.getvalue())
        self.assertEqual(len(data), 1)
        # score serialized as string preserves precision; correlator
        # multiplies weight(1.0) × strength(1.0) per component which
        # widens to 1.00 each, so the sum lands as "2.00".
        self.assertEqual(data[0]["score"], "2.00")
        self.assertEqual(data[0]["asset"], "AAPL")
        self.assertEqual(data[0]["horizon_tag"], "equity:core")

    def test_events_mode_bypasses_correlator(self) -> None:
        rpath = _rules_path(self)
        sample = [
            _event(ts=_dt(5), source="insider_clusters", signal_kind="buy_cluster"),
        ]
        out = io.StringIO()
        with (
            patch("genkei.cli.signals.query_events", return_value=sample) as mocked,
            redirect_stdout(out),
        ):
            code = main(["signals", "--events", "--rules-path", str(rpath)])
        self.assertEqual(code, 0)
        self.assertIn("Signal events", out.getvalue())
        # No correlator dependency, just the event reader was called.
        mocked.assert_called_once()

    def test_rule_filter_limits_correlator_input(self) -> None:
        rpath = _rules_path(self)
        sample = [
            _event(ts=_dt(5), source="insider_clusters", signal_kind="buy_cluster"),
            _event(ts=_dt(6), source="crowding", signal_kind="crowding_add"),
        ]
        out = io.StringIO()
        with (
            patch("genkei.cli.signals.query_events", return_value=sample),
            redirect_stdout(out),
        ):
            code = main(
                [
                    "signals",
                    "--no-benchmark",
                    "--rule",
                    "smart_money_buy",
                    "--rules-path",
                    str(rpath),
                ]
            )
        self.assertEqual(code, 0)
        self.assertIn("smart_money_buy", out.getvalue())


class BenchmarkColumnTests(unittest.TestCase):
    """B-100 — benchmark-adjusted column appears with --benchmark (default)."""

    def test_benchmark_default_renders_vs_bench_column(self) -> None:
        rpath = _rules_path(self)
        sample = [
            _event(ts=_dt(5), source="insider_clusters", signal_kind="buy_cluster"),
            _event(ts=_dt(6), source="crowding", signal_kind="crowding_add"),
        ]
        from decimal import Decimal as Dec

        from genkei.experiments.signal_benchmark import StackBenchmarkContext

        fake_ctx = [
            StackBenchmarkContext(
                stack_index=0,
                benchmark_ticker="SPY",
                asset_return_pct=Dec("3.5"),
                benchmark_return_pct=Dec("1.0"),
                abnormal_pct=Dec("2.5"),
            )
        ]
        out = io.StringIO()
        with (
            patch("genkei.cli.signals.query_events", return_value=sample),
            patch(
                "genkei.cli.signals.compute_stack_benchmark_contexts",
                return_value=fake_ctx,
            ),
            redirect_stdout(out),
        ):
            # Default flag is --benchmark, so this should add the column.
            code = main(["signals", "--rules-path", str(rpath)])
        self.assertEqual(code, 0)
        rendered = out.getvalue()
        self.assertIn("vs_bench", rendered)
        self.assertIn("2.50", rendered)
        self.assertIn("asset_return_pct", rendered)  # footer mentions the column

    def test_no_benchmark_flag_suppresses_column(self) -> None:
        rpath = _rules_path(self)
        sample = [
            _event(ts=_dt(5), source="insider_clusters", signal_kind="buy_cluster"),
            _event(ts=_dt(6), source="crowding", signal_kind="crowding_add"),
        ]
        out = io.StringIO()
        with (
            patch("genkei.cli.signals.query_events", return_value=sample),
            patch(
                "genkei.cli.signals.compute_stack_benchmark_contexts",
                side_effect=AssertionError("should not be called with --no-benchmark"),
            ),
            redirect_stdout(out),
        ):
            code = main(["signals", "--no-benchmark", "--rules-path", str(rpath)])
        self.assertEqual(code, 0)
        self.assertNotIn("vs_bench", out.getvalue())

    def test_overrides_propagate_to_compute(self) -> None:
        rpath = _rules_path(self)
        sample = [
            _event(ts=_dt(5), source="insider_clusters", signal_kind="buy_cluster"),
            _event(ts=_dt(6), source="crowding", signal_kind="crowding_add"),
        ]
        out = io.StringIO()
        with (
            patch("genkei.cli.signals.query_events", return_value=sample),
            patch(
                "genkei.cli.signals.compute_stack_benchmark_contexts",
                return_value=[],
            ) as ctx_mock,
            redirect_stdout(out),
        ):
            main(
                [
                    "signals",
                    "--equity-benchmark",
                    "QQQ",
                    "--crypto-benchmark",
                    "ETH",
                    "--rules-path",
                    str(rpath),
                ]
            )
        kwargs = ctx_mock.call_args.kwargs
        self.assertEqual(kwargs["equity_benchmark"], "QQQ")
        self.assertEqual(kwargs["crypto_benchmark"], "ETH")

    def test_json_includes_benchmark_section(self) -> None:
        rpath = _rules_path(self)
        sample = [
            _event(ts=_dt(5), source="insider_clusters", signal_kind="buy_cluster"),
            _event(ts=_dt(6), source="crowding", signal_kind="crowding_add"),
        ]
        from decimal import Decimal as Dec

        from genkei.experiments.signal_benchmark import StackBenchmarkContext

        fake_ctx = [
            StackBenchmarkContext(
                stack_index=0,
                benchmark_ticker="SPY",
                asset_return_pct=Dec("3.5"),
                benchmark_return_pct=Dec("1.0"),
                abnormal_pct=Dec("2.5"),
            )
        ]
        out = io.StringIO()
        with (
            patch("genkei.cli.signals.query_events", return_value=sample),
            patch(
                "genkei.cli.signals.compute_stack_benchmark_contexts",
                return_value=fake_ctx,
            ),
            redirect_stdout(out),
        ):
            code = main(["signals", "--json", "--rules-path", str(rpath)])
        self.assertEqual(code, 0)
        data = json_mod.loads(out.getvalue())
        self.assertEqual(data[0]["benchmark"]["ticker"], "SPY")
        self.assertEqual(data[0]["benchmark"]["abnormal_pct"], "2.5")


if __name__ == "__main__":
    unittest.main()
