"""Unit tests for the `genkei macro-regime` subcommand (B-059)."""

from __future__ import annotations

import io
import json as json_mod
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from genkei.cli import main
from genkei.cli.macro_regime import _format_human, _result_to_dict
from genkei.experiments.macro_regime import DEFAULT_HORIZON, RegimeResult


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return ANSI_RE.sub("", text)


def _result() -> RegimeResult:
    return RegimeResult(
        ts=date(2024, 6, 1),
        regime="risk_on",
        horizon=DEFAULT_HORIZON,
        available_inputs=4,
        dgs10=Decimal("4.0"),
        dgs10_30d_change=Decimal("-0.4"),
        hy_oas=Decimal("3.0"),
        hy_oas_30d_change=Decimal("-0.1"),
        vix=Decimal("15"),
        usd_index=Decimal("98"),
        usd_index_30d_change=Decimal("-2"),
    )


class FormatTests(unittest.TestCase):
    def test_human_output_includes_horizon(self) -> None:
        text = _format_human([_result()])
        self.assertIn(f"horizon={DEFAULT_HORIZON}", text)

    def test_json_dict_includes_horizon_tag(self) -> None:
        payload = _result_to_dict(_result())
        self.assertEqual(payload["horizon_tag"], DEFAULT_HORIZON)


class MacroRegimeCommandTests(unittest.TestCase):
    def test_invalid_since_uses_single_dash_option_name(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["macro-regime", "--since", "nope"])
        self.assertEqual(code, 2)
        text = _plain(err.getvalue())
        self.assertIn("--since must be YYYY-MM-DD", text)
        self.assertNotIn("----since", text)

    def test_since_after_until_is_rejected(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(
                [
                    "macro-regime",
                    "--since",
                    "2025-01-01",
                    "--until",
                    "2024-01-01",
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("--since must be on or before --until", _plain(err.getvalue()))

    def test_json_mode_emits_horizon_tag(self) -> None:
        out = io.StringIO()
        with (
            patch("genkei.cli.macro_regime.load_regimes", return_value=[_result()]),
            redirect_stdout(out),
        ):
            code = main(["macro-regime", "--json"])
        self.assertEqual(code, 0)
        payload = json_mod.loads(out.getvalue())
        self.assertEqual(payload["results"][0]["horizon_tag"], DEFAULT_HORIZON)


if __name__ == "__main__":
    unittest.main()
