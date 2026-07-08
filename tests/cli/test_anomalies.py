"""Unit tests for the ``genkei anomalies`` CLI (B-069)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from genkei.cli.anomalies import _asset_filter_values, _query
from genkei.common.watchlist import CryptoEntry, EquityEntry, Watchlist


def _watchlist() -> Watchlist:
    return Watchlist(
        crypto=[
            CryptoEntry(
                symbol="BTC",
                name="Bitcoin",
                coingecko_id="bitcoin",
                tier="primary",
            )
        ],
        equities=[
            EquityEntry(
                symbol="AAPL",
                name="Apple Inc.",
                cik="0000320193",
                tier="primary",
            )
        ],
        macro=[],
        protocols=[],
        filers=[],
    )


class AssetFilterValuesTests(unittest.TestCase):
    def test_crypto_symbol_includes_stored_coingecko_id(self) -> None:
        with patch("genkei.cli.anomalies.load_watchlist", return_value=_watchlist()):
            self.assertEqual(_asset_filter_values("BTC"), ["BTC", "bitcoin"])

    def test_lowercase_crypto_symbol_keeps_literal_and_resolves_id(self) -> None:
        with patch("genkei.cli.anomalies.load_watchlist", return_value=_watchlist()):
            self.assertEqual(_asset_filter_values("btc"), ["btc", "BTC", "bitcoin"])

    def test_equity_symbol_includes_canonical_uppercase_ticker(self) -> None:
        with patch("genkei.cli.anomalies.load_watchlist", return_value=_watchlist()):
            self.assertEqual(_asset_filter_values("aapl"), ["aapl", "AAPL"])

    def test_unmatched_or_unavailable_watchlist_uses_literal_filter(self) -> None:
        with patch("genkei.cli.anomalies.load_watchlist", side_effect=FileNotFoundError):
            self.assertEqual(_asset_filter_values("bitcoin"), ["bitcoin"])


class QueryTests(unittest.TestCase):
    def test_asset_filter_queries_literal_and_resolved_ids(self) -> None:
        captured: dict[str, object] = {}

        class FakeCursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params):
                captured["sql"] = sql
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

        with (
            patch("genkei.cli.anomalies.load_watchlist", return_value=_watchlist()),
            patch("genkei.cli.anomalies.db.connection", return_value=FakeConn()),
        ):
            _query(
                asset="BTC",
                asset_class=None,
                direction=None,
                since=None,
                until=None,
                min_score=None,
                limit=25,
            )

        self.assertIn("asset IN (%s, %s)", str(captured["sql"]))
        self.assertEqual(captured["params"], ["BTC", "bitcoin", 25])


if __name__ == "__main__":
    unittest.main()
