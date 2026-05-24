"""Unit tests for the eight-k-impact CLI wrapper."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from decimal import Decimal
from unittest.mock import patch

import typer

from genkei.cli.eight_k_impact import _stratum_to_dict, eight_k_impact_cmd
from genkei.experiments.eight_k_impact import DEFAULT_HORIZON, StratumStats


def _stats() -> StratumStats:
    return StratumStats(
        stratum_key="ALL",
        n_events=1,
        mean_pct={"same_day": Decimal("1")},
        median_pct={"same_day": Decimal("1")},
        hit_rate_pct={"same_day": Decimal("100")},
    )


class EightKImpactCliTests(unittest.TestCase):
    def test_stratum_json_dict_includes_horizon_tag(self) -> None:
        self.assertEqual(_stratum_to_dict(_stats())["horizon_tag"], DEFAULT_HORIZON)

    def test_json_output_includes_top_level_horizon_tag(self) -> None:
        out = io.StringIO()
        with (
            patch("genkei.cli.eight_k_impact.run_event_study", return_value=[]),
            redirect_stdout(out),
        ):
            eight_k_impact_cmd(json_output=True)

        payload = json.loads(out.getvalue())
        self.assertEqual(payload["horizon_tag"], DEFAULT_HORIZON)
        self.assertEqual(payload["overall"]["horizon_tag"], DEFAULT_HORIZON)

    def test_human_output_includes_horizon_tag(self) -> None:
        out = io.StringIO()
        with (
            patch("genkei.cli.eight_k_impact.run_event_study", return_value=[]),
            redirect_stdout(out),
        ):
            eight_k_impact_cmd()

        self.assertIn(f"horizon={DEFAULT_HORIZON}", out.getvalue())

    def test_since_after_until_is_rejected(self) -> None:
        with self.assertRaises(typer.BadParameter) as ctx:
            eight_k_impact_cmd(since="2024-06-01", until="2024-01-01")

        self.assertIn("--since", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
