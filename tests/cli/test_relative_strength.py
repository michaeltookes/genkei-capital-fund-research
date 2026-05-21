"""Unit tests for ``genkei relative-strength`` (B-090)."""

from __future__ import annotations

import io
import json as json_mod
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from genkei.cli import main
from genkei.experiments.relative_strength import RelativeStrengthRow

CRYPTO_YAML = """
crypto:
  primary:
    - symbol: BTC
      name: Bitcoin
      coingecko_id: bitcoin
    - symbol: ETH
      name: Ethereum
      coingecko_id: ethereum
    - symbol: SOL
      name: Solana
      coingecko_id: solana
    - symbol: SUI
      name: Sui
      coingecko_id: sui
"""


def _watchlist_path(case: unittest.TestCase, body: str = CRYPTO_YAML) -> Path:
    ctx = TemporaryDirectory()
    case.addCleanup(ctx.cleanup)
    tmp = Path(ctx.name)
    path = tmp / "watchlists.yml"
    path.write_text(body, encoding="utf-8")
    return path


def _row(
    asset: str,
    peer: str,
    window_days: int,
    asset_ret: float | None,
    peer_ret: float | None,
    rel: float | None,
) -> RelativeStrengthRow:
    """Build a synthetic RelativeStrengthRow with realistic-enough fields."""
    return RelativeStrengthRow(
        asset=asset,
        peer=peer,
        window_days=window_days,
        asset_latest_ts=date(2026, 5, 21),
        asset_lookback_ts=date(2026, 5, 21 - window_days)
        if window_days <= 21
        else date(2026, 1, 1),
        asset_latest_price=Decimal("1"),
        asset_lookback_price=Decimal("1"),
        asset_return_pct=Decimal(str(asset_ret)) if asset_ret is not None else None,
        peer_latest_ts=date(2026, 5, 21),
        peer_lookback_ts=date(2026, 5, 21 - window_days)
        if window_days <= 21
        else date(2026, 1, 1),
        peer_latest_price=Decimal("1"),
        peer_lookback_price=Decimal("1"),
        peer_return_pct=Decimal(str(peer_ret)) if peer_ret is not None else None,
        relative_strength_pct=Decimal(str(rel)) if rel is not None else None,
    )


class DefaultModeTests(unittest.TestCase):
    def test_default_mode_filters_to_btc_30d(self) -> None:
        # Default: all watchlist crypto vs BTC at 30d window.
        # Verify load_relative_strength is invoked with peer='bitcoin',
        # window_days=30, asset=None.
        path = _watchlist_path(self)
        fake_rows = [
            _row("sui", "bitcoin", 30, asset_ret=17.0, peer_ret=5.0, rel=12.0),
            _row("solana", "bitcoin", 30, asset_ret=2.0, peer_ret=5.0, rel=-3.0),
        ]
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.relative_strength.load_relative_strength",
                return_value=fake_rows,
            ) as mock_load,
            redirect_stdout(out),
        ):
            code = main(["relative-strength", "--config", str(path)])
        self.assertEqual(code, 0)
        mock_load.assert_called_once_with(
            asset=None, peer="bitcoin", window_days=30, limit=50
        )
        text = out.getvalue()
        self.assertIn("SUI", text)
        self.assertIn("BTC", text)
        self.assertIn("+12.0%", text)
        self.assertIn("-3.0%", text)


class TickerPeerFilterTests(unittest.TestCase):
    def test_ticker_and_peer_show_all_windows(self) -> None:
        # With both --ticker and --peer set but no --window, the command
        # surfaces all 5 default windows for that single pair.
        path = _watchlist_path(self)
        fake_rows = [
            _row("sui", "solana", 7, -8.7, -5.0, -3.7),
            _row("sui", "solana", 30, 17.0, 1.4, 15.6),
            _row("sui", "solana", 90, 19.1, 5.1, 14.0),
            _row("sui", "solana", 180, -20.7, -32.7, 12.0),
            _row("sui", "solana", 365, -71.4, -48.6, -22.8),
        ]
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.relative_strength.load_relative_strength",
                return_value=fake_rows,
            ) as mock_load,
            redirect_stdout(out),
        ):
            code = main(
                [
                    "relative-strength",
                    "--ticker",
                    "SUI",
                    "--peer",
                    "SOL",
                    "--config",
                    str(path),
                ]
            )
        self.assertEqual(code, 0)
        # window_days=None when both ticker and peer are set without
        # an explicit --window.
        mock_load.assert_called_once_with(
            asset="sui", peer="solana", window_days=None, limit=50
        )
        text = out.getvalue()
        # SUI vs SOL 365d should show the morning-session number.
        self.assertIn("-22.8%", text)
        self.assertIn("window", text.lower())

    def test_explicit_window_filters_to_one_row(self) -> None:
        path = _watchlist_path(self)
        fake_rows = [_row("sui", "solana", 365, -71.4, -48.6, -22.8)]
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.relative_strength.load_relative_strength",
                return_value=fake_rows,
            ) as mock_load,
            redirect_stdout(out),
        ):
            code = main(
                [
                    "relative-strength",
                    "--ticker",
                    "SUI",
                    "--peer",
                    "SOL",
                    "--window",
                    "365",
                    "--config",
                    str(path),
                ]
            )
        self.assertEqual(code, 0)
        mock_load.assert_called_once_with(
            asset="sui", peer="solana", window_days=365, limit=50
        )

    def test_ticker_only_defaults_peer_to_btc(self) -> None:
        path = _watchlist_path(self)
        fake_rows = [_row("sui", "bitcoin", 30, 17.0, 5.0, 12.0)]
        with (
            patch(
                "genkei.cli.relative_strength.load_relative_strength",
                return_value=fake_rows,
            ) as mock_load,
            redirect_stdout(io.StringIO()),
        ):
            code = main(
                [
                    "relative-strength",
                    "--ticker",
                    "SUI",
                    "--config",
                    str(path),
                ]
            )
        self.assertEqual(code, 0)
        # ticker set without peer → default peer = BTC, default window = 30
        mock_load.assert_called_once_with(
            asset="sui", peer="bitcoin", window_days=30, limit=50
        )


class GuardsTests(unittest.TestCase):
    def test_unknown_ticker_rejected(self) -> None:
        path = _watchlist_path(self)
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(
                [
                    "relative-strength",
                    "--ticker",
                    "ZZZZ",
                    "--config",
                    str(path),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("not found in the crypto watchlist", err.getvalue())

    def test_unknown_peer_rejected(self) -> None:
        path = _watchlist_path(self)
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(
                [
                    "relative-strength",
                    "--peer",
                    "ZZZZ",
                    "--config",
                    str(path),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("not found in the crypto watchlist", err.getvalue())

    def test_window_outside_defaults_rejected(self) -> None:
        # The view only carries 7/30/90/180/365. Requesting 45 should
        # be a loud error, not silently empty.
        path = _watchlist_path(self)
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(
                [
                    "relative-strength",
                    "--window",
                    "45",
                    "--config",
                    str(path),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("window", err.getvalue().lower())


class JsonOutputTests(unittest.TestCase):
    def test_json_payload_shape(self) -> None:
        path = _watchlist_path(self)
        fake_rows = [
            _row("sui", "bitcoin", 30, 17.0, 5.0, 12.0),
        ]
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.relative_strength.load_relative_strength",
                return_value=fake_rows,
            ),
            redirect_stdout(out),
        ):
            code = main(
                [
                    "relative-strength",
                    "--ticker",
                    "SUI",
                    "--config",
                    str(path),
                    "--json",
                ]
            )
        self.assertEqual(code, 0)
        payload = json_mod.loads(out.getvalue())
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["asset"], "sui")
        self.assertEqual(payload[0]["peer"], "bitcoin")
        self.assertEqual(payload[0]["window_days"], 30)
        self.assertIn("relative_strength_pct", payload[0])
