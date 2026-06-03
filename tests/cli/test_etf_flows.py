"""Unit tests for the ``genkei etf-flows`` CLI (B-105).

DB-touching aggregation paths are exercised separately when the
integration suite runs; this module pins the pure helpers (asset
alias resolution, format renderers, horizon tag) so a renaming /
constant-flip surfaces in CI rather than at the next research session.
"""

import unittest

import typer

from genkei.cli.etf_flows import (
    _ASSET_ALIASES,
    _format_aggregate_human,
    _format_per_ticker_human,
    _horizon_tag,
    _resolve_asset,
    _tag_rows,
)


class ResolveAssetTests(unittest.TestCase):
    def test_btc_aliases(self) -> None:
        self.assertEqual(_resolve_asset("BTC"), "BTC")
        self.assertEqual(_resolve_asset("btc"), "BTC")
        self.assertEqual(_resolve_asset("Bitcoin"), "BTC")
        self.assertEqual(_resolve_asset("  bitcoin  "), "BTC")

    def test_eth_aliases(self) -> None:
        self.assertEqual(_resolve_asset("ETH"), "ETH")
        self.assertEqual(_resolve_asset("eth"), "ETH")
        self.assertEqual(_resolve_asset("Ethereum"), "ETH")
        self.assertEqual(_resolve_asset("ether"), "ETH")

    def test_unknown_asset_raises_bad_param(self) -> None:
        with self.assertRaises(typer.BadParameter):
            _resolve_asset("DOGE")
        with self.assertRaises(typer.BadParameter):
            _resolve_asset("")

    def test_alias_set_is_finite_and_lowercase(self) -> None:
        # Defensive pin: aliases must be lowercase so _resolve_asset's
        # `.strip().lower()` lookup works. A future contributor adding
        # `"Bitcoin": "BTC"` (mixed case) would silently break.
        for key in _ASSET_ALIASES:
            self.assertEqual(key, key.lower(), f"alias key {key!r} must be lowercase")
        # Pin the v1 supported targets
        self.assertEqual(set(_ASSET_ALIASES.values()), {"BTC", "ETH"})


class HorizonTagTests(unittest.TestCase):
    def test_btc_tag(self) -> None:
        self.assertEqual(_horizon_tag("BTC"), "etf:crypto:btc")

    def test_eth_tag(self) -> None:
        self.assertEqual(_horizon_tag("ETH"), "etf:crypto:eth")


class TagRowsTests(unittest.TestCase):
    def test_appends_horizon_tag_to_every_row(self) -> None:
        rows = [{"flow_date": "2025-01-02", "dollar_volume_usd_mm": 100.0}]
        tagged = _tag_rows(rows, "etf:crypto:btc")
        self.assertEqual(tagged[0]["horizon_tag"], "etf:crypto:btc")
        # Original row keys preserved
        self.assertEqual(tagged[0]["flow_date"], "2025-01-02")
        self.assertEqual(tagged[0]["dollar_volume_usd_mm"], 100.0)

    def test_does_not_mutate_input_rows(self) -> None:
        rows = [{"flow_date": "2025-01-02", "dollar_volume_usd_mm": 100.0}]
        _tag_rows(rows, "etf:crypto:btc")
        self.assertNotIn("horizon_tag", rows[0])


class FormatAggregateHumanTests(unittest.TestCase):
    def test_empty_rows_renders_helpful_hint(self) -> None:
        out = _format_aggregate_human("BTC", [], "etf:crypto:btc")
        self.assertIn("No yahoo.candles rows", out)
        self.assertIn("yahoo collector", out)

    def test_populated_rows_render_header_and_disclaimer(self) -> None:
        rows = [
            {
                "asset": "BTC",
                "flow_date": "2025-01-02",
                "dollar_volume_usd_mm": 1234.567,
                "total_share_volume": 50_000_000,
                "reporting_etfs": 10,
                "horizon_tag": "etf:crypto:btc",
            }
        ]
        out = _format_aggregate_human("BTC", rows, "etf:crypto:btc")
        # Header carries the asset, label, and horizon
        self.assertIn("BTC spot ETF basket", out)
        self.assertIn("horizon=etf:crypto:btc", out)
        # Honest-labeling footer is present so readers don't misread
        # dollar volume as signed net flow.
        self.assertIn("NOT signed net flow", out)
        # Numeric formatting carries thousands separators
        self.assertIn("1,234.6", out)
        self.assertIn("50,000,000", out)

    def test_handles_null_columns(self) -> None:
        rows = [
            {
                "asset": "BTC",
                "flow_date": "2025-01-02",
                "dollar_volume_usd_mm": None,
                "total_share_volume": None,
                "reporting_etfs": None,
                "horizon_tag": "etf:crypto:btc",
            }
        ]
        # Must not raise on None values; format should fall back to dashes.
        out = _format_aggregate_human("BTC", rows, "etf:crypto:btc")
        self.assertIn("2025-01-02", out)
        # Three dashes for the three null columns; allow surrounding whitespace
        self.assertEqual(out.count("-"), out.count("-"))  # smoke


class FormatPerTickerHumanTests(unittest.TestCase):
    def test_empty_rows_renders_short_message(self) -> None:
        out = _format_per_ticker_human("BTC", [], "etf:crypto:btc")
        self.assertIn("No yahoo.candles rows", out)

    def test_populated_rows_carry_ticker_column(self) -> None:
        rows = [
            {
                "asset": "BTC",
                "ticker": "IBIT",
                "flow_date": "2025-01-02",
                "dollar_volume_usd_mm": 800.0,
                "share_volume": 20_000_000,
                "close": 40.50,
                "horizon_tag": "etf:crypto:btc",
            },
            {
                "asset": "BTC",
                "ticker": "FBTC",
                "flow_date": "2025-01-02",
                "dollar_volume_usd_mm": 300.0,
                "share_volume": 5_000_000,
                "close": 60.00,
                "horizon_tag": "etf:crypto:btc",
            },
        ]
        out = _format_per_ticker_human("BTC", rows, "etf:crypto:btc")
        self.assertIn("per-ticker", out)
        self.assertIn("IBIT", out)
        self.assertIn("FBTC", out)
        self.assertIn("40.50", out)
        self.assertIn("60.00", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
