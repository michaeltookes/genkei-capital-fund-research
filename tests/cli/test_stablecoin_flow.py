"""Unit tests for the ``genkei stablecoin-flow`` CLI (B-108).

Pins the pure helpers — chain alias resolution, horizon tag, row
tagging, value coercion, format renderers. The DB-touching query
paths exercise dedupe-on-ingest_run_id logic that's worth integration-
testing separately when the DB fixture lands; the format / alias /
horizon helpers are pure functions covered here.
"""

import unittest

import typer

from genkei.cli.stablecoin_flow import (
    _CHAIN_ALIASES,
    DEFAULT_MIN_SUPPLY_B,
    DEFAULT_TRAJECTORY_DAYS,
    _fmt_b,
    _format_all_chains_human,
    _format_by_stablecoin_human,
    _format_chain_list_human,
    _format_trajectory_human,
    _horizon_tag,
    _resolve_chain,
    _tag_rows,
    _to_float,
)


class ResolveChainTests(unittest.TestCase):
    def test_alias_resolution_case_insensitive(self) -> None:
        self.assertEqual(_resolve_chain("eth"), "Ethereum")
        self.assertEqual(_resolve_chain("ETH"), "Ethereum")
        self.assertEqual(_resolve_chain("Ethereum"), "Ethereum")
        self.assertEqual(_resolve_chain("ethereum"), "Ethereum")
        self.assertEqual(_resolve_chain("sol"), "Solana")
        self.assertEqual(_resolve_chain("SOLANA"), "Solana")
        self.assertEqual(_resolve_chain("tron"), "Tron")
        self.assertEqual(_resolve_chain("trx"), "Tron")
        self.assertEqual(_resolve_chain("bsc"), "BSC")
        self.assertEqual(_resolve_chain("bnb"), "BSC")
        self.assertEqual(_resolve_chain("arb"), "Arbitrum")
        self.assertEqual(_resolve_chain("matic"), "Polygon")

    def test_strips_whitespace(self) -> None:
        self.assertEqual(_resolve_chain("  eth  "), "Ethereum")

    def test_unmapped_chain_falls_through_title_cased(self) -> None:
        # Optimism isn't aliased today, but the CLI should still accept
        # it — the SQL just returns 0 rows if the chain doesn't exist.
        self.assertEqual(_resolve_chain("optimism"), "Optimism")
        # Multi-word title-casing is best-effort; users hitting a
        # mismatch can drop the canonical name via --chain "Hyperliquid L1".
        self.assertEqual(_resolve_chain("hyperliquid"), "Hyperliquid L1")  # aliased

    def test_empty_raises(self) -> None:
        with self.assertRaises(typer.BadParameter):
            _resolve_chain("")

    def test_alias_keys_are_lowercase(self) -> None:
        # Defensive pin: ``_resolve_chain`` lowercases the input before
        # lookup, so any alias key with mixed case would silently never
        # match. Pin to prevent that regression.
        for key in _CHAIN_ALIASES:
            self.assertEqual(key, key.lower(), f"alias key {key!r} must be lowercase")


class HorizonTagTests(unittest.TestCase):
    def test_ethereum_tag(self) -> None:
        self.assertEqual(_horizon_tag("Ethereum"), "stablecoin:ethereum")

    def test_multi_word_chain_collapses_whitespace(self) -> None:
        # "Hyperliquid L1" should produce a horizon tag without spaces
        # so downstream string parsing isn't ambiguous.
        self.assertEqual(_horizon_tag("Hyperliquid L1"), "stablecoin:hyperliquid_l1")


class TagRowsTests(unittest.TestCase):
    def test_appends_horizon_tag_to_every_row(self) -> None:
        rows = [{"day": "2026-06-04", "supply_usd_b": 159.31}]
        tagged = _tag_rows(rows, "stablecoin:ethereum")
        self.assertEqual(tagged[0]["horizon_tag"], "stablecoin:ethereum")
        self.assertEqual(tagged[0]["supply_usd_b"], 159.31)

    def test_does_not_mutate_input(self) -> None:
        rows = [{"day": "2026-06-04", "supply_usd_b": 159.31}]
        _tag_rows(rows, "stablecoin:ethereum")
        self.assertNotIn("horizon_tag", rows[0])

    def test_empty_input(self) -> None:
        self.assertEqual(_tag_rows([], "stablecoin:ethereum"), [])


class ToFloatTests(unittest.TestCase):
    def test_passes_through_int(self) -> None:
        self.assertEqual(_to_float(42), 42.0)

    def test_passes_through_float(self) -> None:
        self.assertEqual(_to_float(3.14), 3.14)

    def test_none_returns_none(self) -> None:
        self.assertIsNone(_to_float(None))

    def test_decimal_coerces(self) -> None:
        from decimal import Decimal

        # defillama supply_usd is NUMERIC → Python Decimal. Must coerce
        # cleanly for JSON serialization.
        self.assertEqual(_to_float(Decimal("159.3053")), 159.3053)


class FmtBTests(unittest.TestCase):
    def test_basic_format(self) -> None:
        out = _fmt_b(159.31, 11)
        self.assertEqual(out.strip(), "159.31")
        self.assertEqual(len(out), 11)

    def test_signed_format(self) -> None:
        out_pos = _fmt_b(0.10, 10, sign=True)
        out_neg = _fmt_b(-3.21, 10, sign=True)
        self.assertIn("+0.10", out_pos)
        self.assertIn("-3.21", out_neg)

    def test_none_renders_dash(self) -> None:
        self.assertEqual(_fmt_b(None, 10).strip(), "-")

    def test_thousands_separator(self) -> None:
        out = _fmt_b(89_932.5, 14)
        self.assertIn("89,932.50", out)


class FormatTrajectoryHumanTests(unittest.TestCase):
    def test_empty_rows_renders_hint(self) -> None:
        out = _format_trajectory_human("Ethereum", [], "stablecoin:ethereum")
        self.assertIn("No defillama.stablecoins rows", out)
        self.assertIn("--list-chains", out)

    def test_populated_rows_render_disclaimer(self) -> None:
        rows = [
            {
                "chain": "Ethereum",
                "day": "2026-06-04",
                "supply_usd_b": 159.31,
                "delta_7d_usd_b": -3.21,
                "delta_30d_usd_b": -5.80,
            }
        ]
        out = _format_trajectory_human("Ethereum", rows, "stablecoin:ethereum")
        self.assertIn("Ethereum stablecoin supply", out)
        self.assertIn("horizon=stablecoin:ethereum", out)
        # Signed delta with the right sign (capital leaving = negative)
        self.assertIn("-3.21", out)
        self.assertIn("-5.80", out)
        # Footer explaining the sign convention so a reader doesn't
        # misread the absolute number as "net flow from primary source"
        self.assertIn("capital arriving", out)
        self.assertIn("capital leaving", out)

    def test_null_deltas_render_dashes(self) -> None:
        # First 7 days of a window will have NULL delta_7d if the
        # lookback buffer didn't have data; renderer must not crash.
        rows = [
            {
                "chain": "Ethereum",
                "day": "2026-04-15",
                "supply_usd_b": 166.93,
                "delta_7d_usd_b": None,
                "delta_30d_usd_b": None,
            }
        ]
        out = _format_trajectory_human("Ethereum", rows, "stablecoin:ethereum")
        self.assertIn("166.93", out)
        self.assertIn("-", out)


class FormatAllChainsHumanTests(unittest.TestCase):
    def test_empty_rows_renders_hint(self) -> None:
        out = _format_all_chains_human([])
        self.assertIn("No defillama.stablecoins rows", out)
        self.assertIn("--min-supply-b", out)

    def test_populated_rows_render_chains_sorted(self) -> None:
        rows = [
            {
                "chain": "Ethereum",
                "day": "2026-06-04",
                "supply_usd_b": 159.31,
                "delta_7d_usd_b": -3.21,
                "delta_30d_usd_b": -5.80,
            },
            {
                "chain": "Solana",
                "day": "2026-06-04",
                "supply_usd_b": 15.37,
                "delta_7d_usd_b": 0.10,
                "delta_30d_usd_b": -0.27,
            },
        ]
        out = _format_all_chains_human(rows)
        self.assertIn("Ethereum", out)
        self.assertIn("Solana", out)
        self.assertIn("159.31", out)
        self.assertIn("15.37", out)
        # The footer's "cross-chain rotation" hint is load-bearing for
        # interpretation; pin it so a future refactor doesn't drop it.
        self.assertIn("rotating", out)


class FormatByStablecoinHumanTests(unittest.TestCase):
    def test_empty_rows_renders_short_message(self) -> None:
        out = _format_by_stablecoin_human("Ethereum", [])
        self.assertIn("No defillama.stablecoins rows", out)

    def test_populated_rows_render_per_asset_table(self) -> None:
        rows = [
            {
                "chain": "Ethereum",
                "day": "2026-06-04",
                "asset_id": "1",
                "symbol": "USDT",
                "name": "Tether",
                "peg_type": "peggedUSD",
                "supply_usd_b": 80.03,
            },
            {
                "chain": "Ethereum",
                "day": "2026-06-04",
                "asset_id": "2",
                "symbol": "USDC",
                "name": "USD Coin",
                "peg_type": "peggedUSD",
                "supply_usd_b": 48.76,
            },
        ]
        out = _format_by_stablecoin_human("Ethereum", rows)
        self.assertIn("by-asset", out)
        self.assertIn("USDT", out)
        self.assertIn("USDC", out)
        self.assertIn("80.03", out)
        self.assertIn("48.76", out)


class FormatChainListHumanTests(unittest.TestCase):
    def test_empty_rows_renders_message(self) -> None:
        out = _format_chain_list_human([])
        self.assertIn("No defillama.stablecoins data", out)

    def test_populated_rows_render_with_supply_and_counts(self) -> None:
        rows = [
            {
                "chain": "Ethereum",
                "supply_usd_b": 159.31,
                "n_assets": 157,
                "latest_day": "2026-06-04",
            },
            {
                "chain": "Solana",
                "supply_usd_b": 15.37,
                "n_assets": 50,
                "latest_day": "2026-06-04",
            },
        ]
        out = _format_chain_list_human(rows)
        self.assertIn("Ethereum", out)
        self.assertIn("Solana", out)
        self.assertIn("159.31", out)
        self.assertIn("157", out)
        self.assertIn("50", out)


class ModuleConstantsTests(unittest.TestCase):
    def test_default_trajectory_days(self) -> None:
        # 30 days picks up the rolling 30d delta on day 1 (with the
        # 35-day lookback buffer in the query).
        self.assertEqual(DEFAULT_TRAJECTORY_DAYS, 30)

    def test_default_min_supply_b(self) -> None:
        # $0.5B filters the long tail of chains with sub-billion stables
        # (defillama tracks chains with as little as $10k of supply).
        self.assertEqual(DEFAULT_MIN_SUPPLY_B, 0.5)

    def test_alias_set_includes_research_critical_chains(self) -> None:
        # Pin the cohort the 2026-06-03 research decision called out:
        # if any of these chains lose their alias, comparative
        # research sessions become friction.
        values = set(_CHAIN_ALIASES.values())
        self.assertIn("Ethereum", values)
        self.assertIn("Solana", values)
        self.assertIn("Bitcoin", values)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
