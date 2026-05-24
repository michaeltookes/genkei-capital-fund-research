"""Unit tests for the eight-k-impact CLI wrapper."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import typer

from genkei.cli.eight_k_impact import _stratum_to_dict, eight_k_impact_cmd
from genkei.experiments.eight_k_impact import (
    DEFAULT_HORIZON,
    EventReturns,
    FilingEvent,
    StratumStats,
)


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

    def test_json_dedupes_global_aggregates_but_keeps_ticker_views(self) -> None:
        rows = [
            EventReturns(
                event=FilingEvent(
                    ticker="GOOG",
                    cik="0001652044",
                    filed_at=date(2024, 1, 2),
                    accession_number="acc",
                    item_codes=("8.01",),
                ),
                windows={"same_day": Decimal("1")},
                regime="risk_on",
            ),
            EventReturns(
                event=FilingEvent(
                    ticker="GOOGL",
                    cik="0001652044",
                    filed_at=date(2024, 1, 2),
                    accession_number="acc",
                    item_codes=("8.01",),
                ),
                windows={"same_day": Decimal("3")},
                regime="risk_on",
            ),
        ]
        out = io.StringIO()
        with (
            patch("genkei.cli.eight_k_impact.run_event_study", return_value=rows),
            redirect_stdout(out),
        ):
            eight_k_impact_cmd(json_output=True)

        payload = json.loads(out.getvalue())
        self.assertEqual(payload["overall"]["n_events"], 1)
        self.assertEqual(payload["overall"]["mean_pct"]["same_day"], "2")
        self.assertEqual(payload["by_item_code"][0]["n_events"], 1)
        self.assertEqual(
            {row["stratum_key"]: row["n_events"] for row in payload["by_ticker"]},
            {"GOOG": 1, "GOOGL": 1},
        )


if __name__ == "__main__":
    unittest.main()
