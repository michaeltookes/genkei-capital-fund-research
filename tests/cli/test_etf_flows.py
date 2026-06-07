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
    _format_net_flow_human,
    _format_per_ticker_human,
    _horizon_tag,
    _query_asset_aggregate,
    _query_net_flow,
    _query_per_ticker,
    _query_targets,
    _resolve_asset,
    _tag_rows,
)
from genkei.common.watchlist import EtfTickerEntry


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
    """Validate ETF activity SQL uses UTC timestamps and launch clamps."""

    def _capture_query(self, query_func) -> tuple[str, list[object]]:
        captured: dict[str, object] = {}

        class FakeCursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, _sql, params):
                captured["sql"] = _sql
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
                [("IBIT", date(2024, 1, 11))],
                since=date(2025, 1, 2),
                until=date(2025, 1, 3),
                limit=10,
            )
        return str(captured["sql"]), list(captured["params"])

    def test_query_targets_parse_launch_dates(self) -> None:
        """Watchlist ETF entries carry launch dates into query targets."""
        targets = _query_targets(
            [
                EtfTickerEntry(
                    ticker="ETHE",
                    name="Grayscale Ethereum Trust ETF",
                    asset="ETH",
                    issuer="Grayscale",
                    launch_date="2024-07-23",
                ),
                EtfTickerEntry(
                    ticker="ETHB",
                    name="iShares Ethereum Trust ETF",
                    asset="ETH",
                    issuer="BlackRock",
                    launch_date=None,
                ),
            ]
        )
        self.assertEqual(targets, [("ETHE", date(2024, 7, 23)), ("ETHB", None)])

    def test_aggregate_query_uses_utc_aware_date_bounds(self) -> None:
        """Aggregate query binds UTC bounds and clamps to spot ETF launch dates."""
        sql, params = self._capture_query(_query_asset_aggregate)
        self.assertIn("(c.ts AT TIME ZONE 'UTC')::date", sql)
        self.assertIn("target.launch_ts", sql)
        self.assertEqual(params[0], ["IBIT"])
        self.assertEqual(params[1], [datetime(2024, 1, 11, tzinfo=timezone.utc)])
        self.assertEqual(params[2], datetime(2025, 1, 2, tzinfo=timezone.utc))
        self.assertEqual(
            params[3],
            datetime(2025, 1, 3, 23, 59, 59, 999999, tzinfo=timezone.utc),
        )
        self.assertEqual(params[4], 10)

    def test_per_ticker_query_uses_utc_aware_date_bounds(self) -> None:
        """Per-ticker query binds UTC bounds and clamps to spot ETF launch dates."""
        sql, params = self._capture_query(_query_per_ticker)
        self.assertIn("(c.ts AT TIME ZONE 'UTC')::date", sql)
        self.assertIn("target.launch_ts", sql)
        self.assertEqual(params[0], ["IBIT"])
        self.assertEqual(params[1], [datetime(2024, 1, 11, tzinfo=timezone.utc)])
        self.assertEqual(params[2], datetime(2025, 1, 2, tzinfo=timezone.utc))
        self.assertEqual(
            params[3],
            datetime(2025, 1, 3, 23, 59, 59, 999999, tzinfo=timezone.utc),
        )
        self.assertEqual(params[4], 10)


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


class QueryNetFlowTests(unittest.TestCase):
    """Pin the --net-flow SQL shape (B-107).

    The window function is the load-bearing piece: the LAG over a ticker-
    partitioned, date-ordered window must run over the FULL snapshot history,
    not just the rows in the since/until filter, or the first row in the
    filter window loses its predecessor and reports NULL net_flow when the
    predecessor snapshot actually exists.
    """

    def _capture_query(self, **kwargs) -> tuple[str, list[object]]:
        """Run _query_net_flow against a fake cursor and return the captured SQL."""
        captured: dict[str, object] = {}

        class FakeCursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, _sql, params):
                captured["sql"] = _sql
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

        defaults = {"since": date(2026, 1, 1), "until": date(2026, 6, 7), "limit": 10}
        defaults.update(kwargs)
        with patch("genkei.cli.etf_flows.db.connection", return_value=FakeConn()):
            _query_net_flow(
                "BTC",
                [("IBIT", date(2024, 1, 11)), ("FBTC", date(2024, 1, 11))],
                **defaults,
            )
        return str(captured["sql"]), list(captured["params"])

    def test_window_uses_lag_partitioned_by_ticker(self) -> None:
        """LAG window partitions by ticker so net flow is computed within-ETF."""
        sql, _ = self._capture_query()
        self.assertIn("LAG(shares_outstanding)", sql)
        self.assertIn("PARTITION BY ticker ORDER BY snapshot_date", sql)

    def test_filter_applied_outside_window(self) -> None:
        """Since/until are applied to the OUTER query so LAG sees full history.

        If the WHERE were inside the subquery, the LAG window would be
        restricted to the filtered set and the earliest row in the filter
        window would never have a predecessor — net_flow would be NULL for
        rows that legitimately have a known prior snapshot.
        """
        sql, _ = self._capture_query()
        # The filter conditions reference snapshot_date directly (in the
        # outer query), not c.snapshot_date or similar — outer-query shape.
        outer_marker = sql.find("snapshot_date >= %s")
        window_marker = sql.find("LAG(shares_outstanding)")
        # LAG appears before the date filter in the textual SQL.
        self.assertGreater(outer_marker, window_marker)

    def test_params_order(self) -> None:
        """Bound params are [asset, tickers, since, until, limit]."""
        _, params = self._capture_query()
        self.assertEqual(params[0], "BTC")
        self.assertEqual(params[1], ["FBTC", "IBIT"])
        self.assertEqual(params[2], date(2026, 1, 1))
        self.assertEqual(params[3], date(2026, 6, 7))
        self.assertEqual(params[4], 10)

    def test_since_only_binds_since(self) -> None:
        """When only --since is set, no until param is bound."""
        _, params = self._capture_query(until=None)
        # asset, tickers, since, limit only — until omitted
        self.assertEqual(len(params), 4)
        self.assertEqual(params[0], "BTC")
        self.assertEqual(params[1], ["FBTC", "IBIT"])
        self.assertEqual(params[2], date(2026, 1, 1))
        self.assertEqual(params[3], 10)

    def test_filters_by_asset_inside_subquery(self) -> None:
        """The asset filter goes INSIDE the subquery so the window is per-asset.

        Filtering by asset outside would make the LAG window span BTC + ETH
        snapshots ordered by date, which is wrong — an ETHA snapshot would
        end up as the predecessor of an IBIT snapshot.
        """
        sql, _ = self._capture_query()
        # Subquery: "FROM etf.fund_snapshots WHERE asset = %s)" — closes the inner
        self.assertIn("FROM etf.fund_snapshots", sql)
        self.assertIn("asset = %s", sql)

    def test_filters_by_watchlist_tickers_inside_subquery(self) -> None:
        """Ticker filter keeps net-flow rows anchored to configured ETFs."""
        sql, params = self._capture_query()
        self.assertIn("ticker = ANY(%s::text[])", sql)
        self.assertEqual(params[1], ["FBTC", "IBIT"])


class FormatNetFlowHumanTests(unittest.TestCase):
    """Validate the --net-flow human-readable renderer (B-107)."""

    def test_empty_rows_points_to_collector(self) -> None:
        """When the table is empty, hint that the collector hasn't run yet."""
        out = _format_net_flow_human("BTC", [], "etf:crypto:btc")
        self.assertIn("No etf.fund_snapshots rows", out)
        self.assertIn("ishares", out)

    def test_first_day_marker_for_null_flow(self) -> None:
        """The very first snapshot per ticker has a NULL flow → '(first day)'.

        Rendering '(first day)' instead of '$0.0' is load-bearing: zero
        would lie about the data — we *don't know* the flow, we just don't
        have yesterday's snapshot.
        """
        rows = [
            {
                "asset": "BTC",
                "ticker": "IBIT",
                "snapshot_date": "2026-06-05",
                "issuer": "BlackRock",
                "nav_per_share_usd": 33.81,
                "total_net_assets_usd": 46_211_335_562.0,
                "shares_outstanding": 1_366_960_018.5,
                "net_flow_usd": None,
                "horizon_tag": "etf:crypto:btc",
            }
        ]
        out = _format_net_flow_human("BTC", rows, "etf:crypto:btc")
        self.assertIn("(first day)", out)

    def test_signed_flow_formatting(self) -> None:
        """Positive and negative net flows render with explicit sign markers."""
        rows = [
            {
                "asset": "BTC",
                "ticker": "IBIT",
                "snapshot_date": "2026-06-05",
                "issuer": "BlackRock",
                "nav_per_share_usd": 33.81,
                "total_net_assets_usd": 46_211_335_562.0,
                "shares_outstanding": 1_366_960_018.5,
                "net_flow_usd": 152_400_000.0,  # +$152.4M creations
                "horizon_tag": "etf:crypto:btc",
            },
            {
                "asset": "BTC",
                "ticker": "IBIT",
                "snapshot_date": "2026-06-04",
                "issuer": "BlackRock",
                "nav_per_share_usd": 33.50,
                "total_net_assets_usd": 46_058_935_562.0,
                "shares_outstanding": 1_374_555_540.0,
                "net_flow_usd": -98_500_000.0,  # -$98.5M redemptions
                "horizon_tag": "etf:crypto:btc",
            },
        ]
        out = _format_net_flow_human("BTC", rows, "etf:crypto:btc")
        # Positive flow renders with explicit '+'
        self.assertIn("+152.4", out)
        # Negative flow renders with '-'
        self.assertIn("-98.5", out)
        # Disclaimer pins the signed semantics
        self.assertIn("positive = net creations", out)
        self.assertIn("negative = net redemptions", out)

    def test_carries_horizon_tag_in_header(self) -> None:
        """Output header includes the asset horizon tag for traceability."""
        out = _format_net_flow_human("ETH", [], "etf:crypto:eth")
        # Even on empty output we don't pretend the horizon doesn't matter
        # because the user passed it — but for the empty case the hint
        # message is the user-facing content. Test the populated path:
        rows = [
            {
                "asset": "ETH",
                "ticker": "ETHA",
                "snapshot_date": "2026-06-05",
                "issuer": "BlackRock",
                "nav_per_share_usd": 11.75,
                "total_net_assets_usd": 4_450_501_503.0,
                "shares_outstanding": 378_920_007.0,
                "net_flow_usd": 25_400_000.0,
                "horizon_tag": "etf:crypto:eth",
            }
        ]
        out = _format_net_flow_human("ETH", rows, "etf:crypto:eth")
        self.assertIn("horizon=etf:crypto:eth", out)
        self.assertIn("BlackRock ETF", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
