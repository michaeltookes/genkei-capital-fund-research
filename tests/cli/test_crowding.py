"""Unit tests for `genkei crowding` (B-061)."""

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
from genkei.cli.crowding import (
    _format_human,
    _resolve_ticker_to_cusip,
    _row_to_dict,
    _ticker_for_cusip,
)
from genkei.common.watchlist import EquityEntry, FilerEntry, Watchlist
from genkei.experiments.crowding_monitor import CrowdingRow

# Watchlist YAML used to back end-to-end CLI argument tests. AAPL has
# a CUSIP, NOCUSIP intentionally doesn't (exercises the friendly-error
# path), BTC is crypto (exercises the crypto-redirect path).
WATCHLIST_YAML = (
    "crypto:\n"
    "  primary:\n"
    "    - symbol: BTC\n"
    "      name: Bitcoin\n"
    "      coingecko_id: bitcoin\n"
    "equities:\n"
    "  primary:\n"
    "    - symbol: AAPL\n"
    "      cik: \"0000320193\"\n"
    "      cusip: \"037833100\"\n"
    "      name: Apple Inc.\n"
    "    - symbol: NOCUSIP\n"
    "      cik: \"0001111111\"\n"
    "      name: NoCusip Inc.\n"
    "filers:\n"
    "  primary:\n"
    "    - cik: 1067983\n"
    "      name: Berkshire Hathaway Inc\n"
)


def _watchlist_path(case: unittest.TestCase) -> Path:
    ctx = TemporaryDirectory()
    case.addCleanup(ctx.cleanup)
    tmp = Path(ctx.name)
    path = tmp / "watchlists.yml"
    path.write_text(WATCHLIST_YAML, encoding="utf-8")
    return path


def _sample_row(
    *,
    period: date = date(2025, 3, 31),
    cusip: str = "037833100",
    holder_count: int = 4,
    net_change: int | None = 2,
    prior_holder_count: int | None = 2,
) -> CrowdingRow:
    return CrowdingRow(
        period_of_report=period,
        cusip=cusip,
        issuer_name="APPLE INC",
        holder_count=holder_count,
        holder_ciks=["A", "B", "C", "D"][:holder_count],
        holder_names=[
            "Berkshire Hathaway Inc",
            "ValueAct Capital Management LP",
            "Pershing Square Capital",
            "Tiger Global",
        ][:holder_count],
        total_value_usd=Decimal("42000000000"),
        total_shares=Decimal("200000000"),
        prior_holder_count=prior_holder_count,
        new_entrants=["C", "D"] if net_change else [],
        exits=[],
        net_change=net_change,
    )


class ResolveTickerToCusipTests(unittest.TestCase):
    def _watchlist(self) -> Watchlist:
        return Watchlist(
            crypto=[],
            equities=[
                EquityEntry(
                    symbol="AAPL",
                    name="Apple Inc.",
                    cik="0000320193",
                    tier="primary",
                    cusip="037833100",
                ),
                EquityEntry(
                    symbol="NOCUSIP",
                    name="NoCusip Inc.",
                    cik="0001111111",
                    tier="primary",
                ),
            ],
            macro=[],
            protocols=[],
            filers=[],
        )

    def test_resolves_to_cusip(self) -> None:
        cusip, entry = _resolve_ticker_to_cusip("AAPL", self._watchlist())
        self.assertEqual(cusip, "037833100")
        self.assertEqual(entry.symbol, "AAPL")

    def test_unknown_ticker_raises(self) -> None:
        import typer
        with self.assertRaises(typer.BadParameter):
            _resolve_ticker_to_cusip("XYZ", self._watchlist())

    def test_ticker_without_cusip_raises_actionable_error(self) -> None:
        import typer
        with self.assertRaises(typer.BadParameter) as cm:
            _resolve_ticker_to_cusip("NOCUSIP", self._watchlist())
        # Error guides the user to fix the watchlist.
        self.assertIn("cusip:", str(cm.exception))


class TickerLookupTests(unittest.TestCase):
    def test_known_cusip_returns_symbol(self) -> None:
        w = Watchlist(
            crypto=[],
            equities=[
                EquityEntry(
                    symbol="AAPL",
                    name="Apple",
                    cik="0000320193",
                    tier="primary",
                    cusip="037833100",
                ),
            ],
            macro=[],
            protocols=[],
            filers=[],
        )
        self.assertEqual(_ticker_for_cusip("037833100", w), "AAPL")

    def test_unknown_cusip_returns_none(self) -> None:
        w = Watchlist(crypto=[], equities=[], macro=[], protocols=[], filers=[])
        self.assertIsNone(_ticker_for_cusip("999999999", w))


class FormatHumanTests(unittest.TestCase):
    def _watchlist(self) -> Watchlist:
        return Watchlist(
            crypto=[],
            equities=[
                EquityEntry(
                    symbol="AAPL",
                    name="Apple",
                    cik="0000320193",
                    tier="primary",
                    cusip="037833100",
                ),
            ],
            macro=[],
            protocols=[],
            filers=[
                FilerEntry(
                    filer_cik="0001067983",
                    name="Berkshire Hathaway Inc",
                    tier="primary",
                ),
            ],
        )

    def test_empty_view_points_at_health_check(self) -> None:
        text = _format_human(
            [],
            watchlist=self._watchlist(),
            period_label="latest period 2025-03-31",
            by_delta=False,
            min_holders=2,
        )
        self.assertIn("No crowded names", text)
        self.assertIn("watchlist health", text)

    def test_renders_delta_marker_and_top_holders(self) -> None:
        row = _sample_row()
        text = _format_human(
            [row],
            watchlist=self._watchlist(),
            period_label="latest period 2025-03-31",
            by_delta=False,
            min_holders=2,
        )
        # Delta marker uses prior(N)→current(M) shorthand
        self.assertIn("+2", text)
        self.assertIn("(2→4)", text)
        # Ticker resolves via watchlist
        self.assertIn("AAPL", text)
        # Dollar formatting includes commas
        self.assertIn("$42,000,000,000", text)
        # Top holders abbreviated; "+1 more" when count > 3
        self.assertIn("Berkshire Hathaway Inc", text)
        self.assertIn("+1 more", text)

    def test_first_period_renders_new_marker(self) -> None:
        row = _sample_row(net_change=None, prior_holder_count=None)
        text = _format_human(
            [row],
            watchlist=self._watchlist(),
            period_label="latest period 2025-03-31",
            by_delta=False,
            min_holders=2,
        )
        # 'new' marker on first-observed period (no prior state).
        self.assertIn("new", text)


class RowToDictTests(unittest.TestCase):
    def test_serializes_all_fields(self) -> None:
        row = _sample_row()
        d = _row_to_dict(row, ticker="AAPL")
        self.assertEqual(d["cusip"], "037833100")
        self.assertEqual(d["ticker"], "AAPL")
        self.assertEqual(d["holder_count"], 4)
        self.assertEqual(d["prior_holder_count"], 2)
        self.assertEqual(d["net_change"], 2)
        self.assertEqual(d["holder_ciks"], ["A", "B", "C", "D"])
        # Decimal stays as Decimal in the dict; _json_default converts at json.dumps time.
        self.assertEqual(d["total_value_usd"], Decimal("42000000000"))


class CommandValidationTests(unittest.TestCase):
    def test_ticker_and_cusip_mutually_exclusive(self) -> None:
        wpath = _watchlist_path(self)
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = main(
                [
                    "crowding",
                    "--ticker",
                    "AAPL",
                    "--cusip",
                    "037833100",
                    "--config",
                    str(wpath),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("mutually exclusive", buf.getvalue())

    def test_period_and_all_periods_mutually_exclusive(self) -> None:
        wpath = _watchlist_path(self)
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = main(
                [
                    "crowding",
                    "--period",
                    "2025-03-31",
                    "--all-periods",
                    "--config",
                    str(wpath),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("mutually exclusive", buf.getvalue())

    def test_since_after_until_rejected(self) -> None:
        wpath = _watchlist_path(self)
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = main(
                [
                    "crowding",
                    "--since",
                    "2025-12-31",
                    "--until",
                    "2024-12-31",
                    "--config",
                    str(wpath),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("--since", buf.getvalue())

    def test_crypto_ticker_redirects_to_prices(self) -> None:
        wpath = _watchlist_path(self)
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = main(
                [
                    "crowding",
                    "--ticker",
                    "BTC",
                    "--config",
                    str(wpath),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("genkei prices", buf.getvalue())

    def test_ticker_without_cusip_surfaces_actionable_error(self) -> None:
        wpath = _watchlist_path(self)
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = main(
                [
                    "crowding",
                    "--ticker",
                    "NOCUSIP",
                    "--config",
                    str(wpath),
                ]
            )
        self.assertEqual(code, 2)
        # Error tells the user to add `cusip:` to the watchlist entry.
        self.assertIn("cusip", buf.getvalue().lower())


class CommandEndToEndTests(unittest.TestCase):
    """Mock the lake loaders and confirm the typer command path renders."""

    def test_default_run_renders_top_crowded_with_delta(self) -> None:
        wpath = _watchlist_path(self)
        sample_row = _sample_row()
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.crowding.available_periods",
                return_value=[date(2025, 3, 31)],
            ),
            patch(
                "genkei.cli.crowding.load_positions",
                return_value=[],  # detector gets pre-built rows via compute_crowding mock below
            ),
            patch(
                "genkei.cli.crowding.compute_crowding",
                return_value=[sample_row],
            ),
            redirect_stdout(out),
        ):
            code = main(["crowding", "--config", str(wpath)])
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("AAPL", text)
        self.assertIn("(2→4)", text)

    def test_json_mode_serializes_decimal_as_string(self) -> None:
        wpath = _watchlist_path(self)
        sample_row = _sample_row()
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.crowding.available_periods",
                return_value=[date(2025, 3, 31)],
            ),
            patch("genkei.cli.crowding.load_positions", return_value=[]),
            patch("genkei.cli.crowding.compute_crowding", return_value=[sample_row]),
            redirect_stdout(out),
        ):
            code = main(["crowding", "--json", "--config", str(wpath)])
        self.assertEqual(code, 0)
        parsed = json_mod.loads(out.getvalue())
        self.assertEqual(len(parsed), 1)
        # Decimal → string preserves full precision (B-079 era contract).
        self.assertEqual(parsed[0]["total_value_usd"], "42000000000")
        self.assertEqual(parsed[0]["ticker"], "AAPL")
        self.assertEqual(parsed[0]["holder_count"], 4)
        self.assertEqual(parsed[0]["net_change"], 2)

    def test_min_holders_filters_below_threshold(self) -> None:
        wpath = _watchlist_path(self)
        # holder_count=1 should be hidden by default min_holders=2
        small_row = _sample_row(holder_count=1, net_change=-1, prior_holder_count=2)
        big_row = _sample_row(holder_count=3, net_change=1, prior_holder_count=2)
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.crowding.available_periods",
                return_value=[date(2025, 3, 31)],
            ),
            patch("genkei.cli.crowding.load_positions", return_value=[]),
            patch(
                "genkei.cli.crowding.compute_crowding",
                return_value=[small_row, big_row],
            ),
            redirect_stdout(out),
        ):
            code = main(["crowding", "--json", "--config", str(wpath)])
        self.assertEqual(code, 0)
        parsed = json_mod.loads(out.getvalue())
        # small_row filtered out; only big_row remains.
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["holder_count"], 3)

    def test_by_delta_sorts_by_net_change_desc(self) -> None:
        wpath = _watchlist_path(self)
        big_delta = _sample_row(
            cusip="11111A111",
            holder_count=4,
            net_change=3,
            prior_holder_count=1,
        )
        small_delta = _sample_row(
            cusip="22222B222",
            holder_count=10,  # more crowded
            net_change=1,
            prior_holder_count=9,
        )
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.crowding.available_periods",
                return_value=[date(2025, 3, 31)],
            ),
            patch("genkei.cli.crowding.load_positions", return_value=[]),
            patch(
                "genkei.cli.crowding.compute_crowding",
                return_value=[small_delta, big_delta],
            ),
            redirect_stdout(out),
        ):
            code = main(
                ["crowding", "--by-delta", "--json", "--config", str(wpath)]
            )
        self.assertEqual(code, 0)
        parsed = json_mod.loads(out.getvalue())
        # big_delta (+3) comes first under by_delta sort, despite small_delta
        # being more crowded by holder_count.
        self.assertEqual(parsed[0]["cusip"], "11111A111")
        self.assertEqual(parsed[1]["cusip"], "22222B222")


if __name__ == "__main__":
    unittest.main()
