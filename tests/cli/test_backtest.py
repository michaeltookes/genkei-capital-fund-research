"""Unit tests for `genkei backtest` (B-101)."""

from __future__ import annotations

import io
import json as json_mod
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from genkei.cli import main
from genkei.cli.backtest import (
    _format_stratum_table,
    _stratum_to_dict,
)
from genkei.experiments.signal_store import Stack
from genkei.experiments.stack_backtest import (
    BaselineStats,
    StackReturns,
    StackStratumStats,
)

EQUITY_CORE = "equity:core"


def _stack(
    *,
    rule: str = "smart_money_buy",
    asset: str = "AAPL",
    direction: str = "bullish",
) -> Stack:
    window_end = datetime(2025, 8, 1, tzinfo=timezone.utc)
    return Stack(
        rule_name=rule,
        asset=asset,
        asset_class="equity",
        horizon=EQUITY_CORE,
        direction=direction,
        window_start=window_end - timedelta(days=7),
        window_end=window_end,
        score=Decimal("2.0"),
        distinct_sources=2,
        event_count=2,
        events=[],
    )


def _stack_returns_for_two_rules() -> list[StackReturns]:
    return [
        StackReturns(
            stack=_stack(rule="smart_money_buy", asset="AAPL"),
            windows={"post_5d": Decimal("2.5"), "post_30d": Decimal("5.0")},
        ),
        StackReturns(
            stack=_stack(rule="smart_money_buy", asset="NVDA"),
            windows={"post_5d": Decimal("3.5"), "post_30d": Decimal("8.0")},
        ),
        StackReturns(
            stack=_stack(rule="broad_exit", asset="AAPL", direction="bearish"),
            windows={"post_5d": Decimal("-1.5"), "post_30d": Decimal("-4.0")},
        ),
    ]


def _baselines() -> dict[str, BaselineStats]:
    return {
        "AAPL": BaselineStats(
            asset="AAPL",
            n_baseline_samples=400,
            mean_pct={"post_5d": Decimal("0.5"), "post_30d": Decimal("2.0")},
            hit_rate_pct={"post_5d": Decimal("55"), "post_30d": Decimal("58")},
        ),
        "NVDA": BaselineStats(
            asset="NVDA",
            n_baseline_samples=400,
            mean_pct={"post_5d": Decimal("1.0"), "post_30d": Decimal("4.0")},
            hit_rate_pct={"post_5d": Decimal("60"), "post_30d": Decimal("62")},
        ),
    }


WINDOW_LABELS = ("post_5d", "post_30d", "post_90d", "post_180d", "post_365d")


def _window_dict(filled: dict[str, Decimal]) -> dict[str, Decimal | None]:
    """Fill the per-window dict, defaulting unset windows to None."""
    return {label: filled.get(label) for label in WINDOW_LABELS}


def _stratum(
    *,
    key: str,
    n_stacks: int,
    n_eval: dict[str, int],
    mean: dict[str, Decimal],
    median: dict[str, Decimal],
    hit: dict[str, Decimal],
    excess: dict[str, Decimal],
) -> StackStratumStats:
    return StackStratumStats(
        stratum_key=key,
        n_stacks=n_stacks,
        horizons=frozenset({EQUITY_CORE}),
        n_evaluable={label: n_eval.get(label, 0) for label in WINDOW_LABELS},
        mean_pct=_window_dict(mean),
        median_pct=_window_dict(median),
        hit_rate_pct=_window_dict(hit),
        mean_excess_pct=_window_dict(excess),
    )


class StratumToDictTests(unittest.TestCase):
    def test_includes_per_window_metrics(self) -> None:
        stats = _stratum(
            key="smart_money_buy",
            n_stacks=2,
            n_eval={"post_5d": 2, "post_30d": 2},
            mean={"post_5d": Decimal("3.0"), "post_30d": Decimal("6.5")},
            median={"post_5d": Decimal("3.0"), "post_30d": Decimal("6.5")},
            hit={"post_5d": Decimal("100"), "post_30d": Decimal("100")},
            excess={"post_5d": Decimal("2.25"), "post_30d": Decimal("3.5")},
        )
        out = _stratum_to_dict(stats)
        self.assertEqual(out["stratum"], "smart_money_buy")
        self.assertEqual(out["horizons"], [EQUITY_CORE])
        self.assertEqual(out["n_stacks"], 2)
        self.assertEqual(out["windows"]["post_5d"]["mean_pct"], Decimal("3.0"))
        self.assertEqual(out["windows"]["post_5d"]["mean_excess_pct"], Decimal("2.25"))
        self.assertIsNone(out["windows"]["post_90d"]["mean_pct"])


class FormatStratumTableTests(unittest.TestCase):
    def test_empty_input_yields_actionable_message(self) -> None:
        msg = _format_stratum_table([], "rule")
        self.assertIn("No stacks matched", msg)
        self.assertIn("genkei signals", msg)  # points at the diagnostic command

    def test_human_table_includes_stratum_and_window_labels(self) -> None:
        stats = _stratum(
            key="broad_exit",
            n_stacks=10,
            n_eval={label: 10 for label in ("post_5d", "post_30d")},
            mean={"post_5d": Decimal("-1.5"), "post_30d": Decimal("-4.0")},
            median={"post_5d": Decimal("-1"), "post_30d": Decimal("-3")},
            hit={"post_5d": Decimal("30"), "post_30d": Decimal("25")},
            excess={"post_5d": Decimal("-2"), "post_30d": Decimal("-6")},
        )
        out = _format_stratum_table([stats], "rule")
        self.assertIn("Backtest by rule", out)
        self.assertIn("broad_exit", out)
        self.assertIn(EQUITY_CORE, out)
        self.assertIn("post_5d", out)
        self.assertIn("excess", out)


class CliEndToEndTests(unittest.TestCase):
    def test_default_renders_rule_strata(self) -> None:
        with patch(
            "genkei.cli.backtest.run_backtest",
            return_value=(_stack_returns_for_two_rules(), _baselines()),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["backtest"])
        self.assertEqual(code, 0)
        out = buf.getvalue()
        self.assertIn("smart_money_buy", out)
        self.assertIn("broad_exit", out)
        self.assertIn("post_5d", out)
        # excess for smart_money_buy on post_5d:
        #   stacks: AAPL@2.5%, NVDA@3.5% → mean 3.0
        #   baselines (asset-weighted): AAPL=0.5, NVDA=1.0 → mean 0.75
        #   excess = 2.25
        self.assertIn("2.25", out)

    def test_json_mode_emits_machine_readable_stratum_array(self) -> None:
        with patch(
            "genkei.cli.backtest.run_backtest",
            return_value=(_stack_returns_for_two_rules(), _baselines()),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["backtest", "--json"])
        self.assertEqual(code, 0)
        parsed = json_mod.loads(buf.getvalue())
        self.assertEqual(len(parsed), 2)
        strata = sorted(p["stratum"] for p in parsed)
        self.assertEqual(strata, ["broad_exit", "smart_money_buy"])
        # Decimals serialized via _json_default — JSON shows strings.
        smb = next(p for p in parsed if p["stratum"] == "smart_money_buy")
        self.assertEqual(smb["n_stacks"], 2)
        self.assertEqual(smb["windows"]["post_5d"]["n_evaluable"], 2)

    def test_by_direction_groups_bullish_vs_bearish(self) -> None:
        with patch(
            "genkei.cli.backtest.run_backtest",
            return_value=(_stack_returns_for_two_rules(), _baselines()),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["backtest", "--by", "direction"])
        self.assertEqual(code, 0)
        out = buf.getvalue()
        self.assertIn("Backtest by direction", out)
        self.assertIn("bullish", out)
        self.assertIn("bearish", out)


class CliValidationTests(unittest.TestCase):
    def test_unknown_by_rejected(self) -> None:
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = main(["backtest", "--by", "wat"])
        self.assertEqual(code, 2)
        msg = re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue())
        self.assertIn("Invalid value", msg)
        self.assertIn("--by", msg)

    def test_unknown_direction_rejected(self) -> None:
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = main(["backtest", "--direction", "sideways"])
        self.assertEqual(code, 2)
        msg = re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue())
        self.assertIn("Invalid value", msg)
        self.assertIn("--direction", msg)

    def test_since_after_until_rejected(self) -> None:
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = main(["backtest", "--since", "2025-12-31", "--until", "2025-01-01"])
        self.assertEqual(code, 2)
        msg = re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue())
        self.assertIn("Invalid value", msg)
        self.assertIn("since", msg)
        self.assertIn("on or before", msg)


if __name__ == "__main__":
    unittest.main()
