"""Unit tests for the ``genkei etf-flows`` CLI (B-105).

DB-touching aggregation paths are exercised separately when the
integration suite runs; this module pins the pure helpers (asset
alias resolution, format renderers, horizon tag) so a renaming /
constant-flip surfaces in CI rather than at the next research session.
"""

import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

import typer

from genkei.cli.etf_flows import (
    _ASSET_ALIASES,
    _format_aggregate_human,
    _format_per_ticker_human,
    _horizon_tag,
    _query_asset_aggregate,
    _query_per_ticker,
    _resolve_asset,
    _tag_rows,
)


class ResolveAssetTests(unittest.TestCase):
    """Validate asset alias handling for the ETF flows command."""

    def test_btc_aliases(self) -> None:
        """BTC spellings and whitespace normalize to the BTC asset code."""
        self.assertEqual(_resolve_asset("BTC"), "BTC")
        self.assertEqual(_resolve_asset("btc"), "BTC")
        self.assertEqual(_resolve_asset("Bitcoin"), "BTC")
        self.assertEqual(_resolve_asset("  bitcoin  "), "BTC")

    def test_eth_aliases(self) -> None:
        """ETH spellings normalize to the ETH asset code."""
        self.assertEqual(_resolve_asset("ETH"), "ETH")
        self.assertEqual(_resolve_asset("eth"), "ETH")
        self.assertEqual(_resolve_asset("Ethereum"), "ETH")
        self.assertEqual(_resolve_asset("ether"), "ETH")

    def test_unknown_asset_raises_bad_param(self) -> None:
        """Unsupported and blank assets raise the CLI-friendly Typer error."""
        with self.assertRaises(typer.BadParameter):
            _resolve_asset("DOGE")
        with self.assertRaises(typer.BadParameter):
            _resolve_asset("")

    def test_alias_set_is_finite_and_lowercase(self) -> None:
        """Alias table keys stay lowercase and mapped only to BTC/ETH."""
        # Defensive pin: aliases must be lowercase so _resolve_asset's
        # `.strip().lower()` lookup works. A future contributor adding
        # `"Bitcoin": "BTC"` (mixed case) would silently break.
        for key in _ASSET_ALIASES:
            self.assertEqual(key, key.lower(), f"alias key {key!r} must be lowercase")
        # Pin the v1 supported targets
        self.assertEqual(set(_ASSET_ALIASES.values()), {"BTC", "ETH"})


class HorizonTagTests(unittest.TestCase):
    """Validate ETF horizon tag generation."""

    def test_btc_tag(self) -> None:
        """BTC maps to the ETF crypto BTC horizon tag."""
        self.assertEqual(_horizon_tag("BTC"), "etf:crypto:btc")

    def test_eth_tag(self) -> None:
        """ETH maps to the ETF crypto ETH horizon tag."""
        self.assertEqual(_horizon_tag("ETH"), "etf:crypto:eth")


class TagRowsTests(unittest.TestCase):
    """Validate horizon tag attachment for output rows."""

    def test_appends_horizon_tag_to_every_row(self) -> None:
        """Tagged rows include the horizon tag and preserve original fields."""
        rows = [{"flow_date": "2025-01-02", "dollar_volume_usd_mm": 100.0}]
        tagged = _tag_rows(rows, "etf:crypto:btc")
        self.assertEqual(tagged[0]["horizon_tag"], "etf:crypto:btc")
        # Original row keys preserved
        self.assertEqual(tagged[0]["flow_date"], "2025-01-02")
        self.assertEqual(tagged[0]["dollar_volume_usd_mm"], 100.0)

    def test_does_not_mutate_input_rows(self) -> None:
        """Tagging returns copied rows rather than mutating the caller input."""
        rows = [{"flow_date": "2025-01-02", "dollar_volume_usd_mm": 100.0}]
        _tag_rows(rows, "etf:crypto:btc")
        self.assertNotIn("horizon_tag", rows[0])


class QueryDateBoundsTests(unittest.TestCase):
    """Validate SQL date bounds use UTC-aware timestamps."""

    def _capture_params(self, query_func) -> list[object]:
        captured: dict[str, list[object]] = {}

        class FakeCursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, _sql, params):
                captured["params"] = list(params)

            def fetchall(self):
                return []

        class FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return FakeCursor()

        with patch("genkei.cli.etf_flows.db.connection", return_value=FakeConn()):
            query_func(
                "BTC",
                ["IBIT"],
                since=date(2025, 1, 2),
                until=date(2025, 1, 3),
                limit=10,
            )
        return captured["params"]

    def test_aggregate_query_uses_utc_aware_date_bounds(self) -> None:
        """Aggregate date filters bind UTC timestamps for TIMESTAMPTZ comparisons."""
        params = self._capture_params(_query_asset_aggregate)
        self.assertEqual(params[1], datetime(2025, 1, 2, tzinfo=timezone.utc))
        self.assertEqual(
            params[2],
            datetime(2025, 1, 3, 23, 59, 59, 999999, tzinfo=timezone.utc),
        )

    def test_per_ticker_query_uses_utc_aware_date_bounds(self) -> None:
        """Per-ticker date filters bind UTC timestamps for TIMESTAMPTZ comparisons."""
        params = self._capture_params(_query_per_ticker)
        self.assertEqual(params[1], datetime(2025, 1, 2, tzinfo=timezone.utc))
        self.assertEqual(
            params[2],
            datetime(2025, 1, 3, 23, 59, 59, 999999, tzinfo=timezone.utc),
        )


class FormatAggregateHumanTests(unittest.TestCase):
    """Validate human-readable aggregate ETF activity output."""

    def test_empty_rows_renders_helpful_hint(self) -> None:
        """Empty aggregate output points users toward the Yahoo collector."""
        out = _format_aggregate_human("BTC", [], "etf:crypto:btc")
        self.assertIn("No yahoo.candles rows", out)
        self.assertIn("yahoo collector", out)

    def test_populated_rows_render_header_and_disclaimer(self) -> None:
        """Aggregate output includes labels, horizon, numbers, and disclaimer."""
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
        """Null aggregate columns render placeholder dashes instead of errors."""
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
        # Three null columns should render as placeholder dashes on the row.
        self.assertRegex(out, r"2025-01-02\s+-\s+-\s+-")


class FormatPerTickerHumanTests(unittest.TestCase):
    """Validate human-readable per-ticker ETF activity output."""

    def test_empty_rows_renders_short_message(self) -> None:
        """Empty per-ticker output renders a concise no-data message."""
        out = _format_per_ticker_human("BTC", [], "etf:crypto:btc")
        self.assertIn("No yahoo.candles rows", out)

    def test_populated_rows_carry_ticker_column(self) -> None:
        """Per-ticker output preserves ticker labels and close formatting."""
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
