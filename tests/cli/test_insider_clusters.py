"""Unit tests for ``genkei insider-clusters`` (B-060)."""

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
from genkei.experiments.insider_clusters import Transaction

EQUITY_AND_CRYPTO_YAML = (
    "crypto:\n  primary:\n    - symbol: BTC\n      name: Bitcoin\n      coingecko_id: bitcoin\n"
    "equities:\n  primary:\n    - symbol: JPM\n      name: JPMorgan Chase\n"
    '      cik: "0000019617"\n'
    "    - symbol: AAPL\n      name: Apple Inc.\n"
    '      cik: "0000320193"\n'
    "    - symbol: NOCIK\n      name: Nocik Co.\n"
)


def _watchlist_path(case: unittest.TestCase) -> Path:
    ctx = TemporaryDirectory()
    case.addCleanup(ctx.cleanup)
    tmp = Path(ctx.name)
    path = tmp / "watchlists.yml"
    path.write_text(EQUITY_AND_CRYPTO_YAML, encoding="utf-8")
    return path


class ClusterCommandTests(unittest.TestCase):
    def test_since_after_until_rejected(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(
                [
                    "insider-clusters",
                    "--since",
                    "2026-06-01",
                    "--until",
                    "2026-01-01",
                ]
            )
        self.assertEqual(code, 2)

    def test_crypto_ticker_redirects_to_prices(self) -> None:
        path = _watchlist_path(self)
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(
                ["insider-clusters", "--ticker", "BTC", "--config", str(path)]
            )
        self.assertEqual(code, 2)
        self.assertIn("crypto", err.getvalue().lower())
        self.assertIn("genkei prices", err.getvalue())

    def test_equity_without_cik_rejected(self) -> None:
        path = _watchlist_path(self)
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(
                ["insider-clusters", "--ticker", "NOCIK", "--config", str(path)]
            )
        self.assertEqual(code, 2)
        self.assertIn("CIK", err.getvalue())

    def test_unknown_ticker_rejected(self) -> None:
        path = _watchlist_path(self)
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(
                ["insider-clusters", "--ticker", "ZZZZ", "--config", str(path)]
            )
        self.assertEqual(code, 2)
        self.assertIn("ZZZZ", err.getvalue())

    def test_min_reporters_below_2_rejected_by_typer(self) -> None:
        path = _watchlist_path(self)
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(
                [
                    "insider-clusters",
                    "--min-reporters",
                    "1",
                    "--config",
                    str(path),
                ]
            )
        self.assertEqual(code, 2)

    def test_default_uses_buy_candidate_loader(self) -> None:
        path = _watchlist_path(self)
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.insider_clusters.query_buy_candidates",
                return_value=[],
            ) as buy_mock,
            patch(
                "genkei.cli.insider_clusters.query_sell_candidates",
                return_value=[],
            ) as sell_mock,
            redirect_stdout(out),
        ):
            code = main(["insider-clusters", "--config", str(path)])
        self.assertIn(code, (None, 0))
        buy_mock.assert_called_once()
        sell_mock.assert_not_called()
        self.assertIn("buy clusters", out.getvalue())

    def test_sell_flag_uses_sell_candidate_loader(self) -> None:
        path = _watchlist_path(self)
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.insider_clusters.query_buy_candidates",
                return_value=[],
            ) as buy_mock,
            patch(
                "genkei.cli.insider_clusters.query_sell_candidates",
                return_value=[],
            ) as sell_mock,
            redirect_stdout(out),
        ):
            main(["insider-clusters", "--sell", "--config", str(path)])
        sell_mock.assert_called_once()
        buy_mock.assert_not_called()
        self.assertIn("sell clusters", out.getvalue())

    def test_ticker_scopes_to_one_issuer_cik(self) -> None:
        path = _watchlist_path(self)
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.insider_clusters.query_buy_candidates",
                return_value=[],
            ) as buy_mock,
            redirect_stdout(out),
        ):
            main(["insider-clusters", "--ticker", "JPM", "--config", str(path)])
        # JPM CIK was passed via issuer_ciks=[...] keyword
        self.assertEqual(buy_mock.call_args.kwargs["issuer_ciks"], ["0000019617"])

    def test_no_ticker_means_no_issuer_filter(self) -> None:
        path = _watchlist_path(self)
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.insider_clusters.query_buy_candidates",
                return_value=[],
            ) as buy_mock,
            redirect_stdout(out),
        ):
            main(["insider-clusters", "--config", str(path)])
        self.assertIsNone(buy_mock.call_args.kwargs["issuer_ciks"])

    def test_human_output_renders_cluster_with_ticker_lookup(self) -> None:
        path = _watchlist_path(self)
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.insider_clusters.query_sell_candidates",
                return_value=_sample_candidates(),
            ),
            redirect_stdout(out),
        ):
            main(["insider-clusters", "--sell", "--config", str(path)])
        text = out.getvalue()
        # JPM ticker derived from 0000019617 via the watchlist
        self.assertIn("JPM", text)
        # date range + reporter count
        self.assertIn("2026-05-15", text)

    def test_json_output_serializes_decimals_as_strings(self) -> None:
        path = _watchlist_path(self)
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.insider_clusters.query_sell_candidates",
                return_value=_sample_candidates(),
            ),
            redirect_stdout(out),
        ):
            main(
                ["insider-clusters", "--sell", "--json", "--config", str(path)]
            )
        parsed = json_mod.loads(out.getvalue())
        self.assertEqual(len(parsed), 1)
        c = parsed[0]
        self.assertEqual(c["issuer_ticker"], "JPM")
        self.assertEqual(c["direction"], "sell")
        # Decimals serialized as strings to preserve precision (matches
        # the project convention from filings --json + insiders --json).
        self.assertEqual(c["total_shares"], "13075")
        self.assertEqual(c["window_start"], "2026-05-15")
        self.assertEqual(c["reporter_count"], 2)

    def test_empty_result_hints_at_health_check(self) -> None:
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.insider_clusters.query_buy_candidates",
                return_value=[],
            ),
            redirect_stdout(out),
        ):
            main(["insider-clusters"])
        self.assertIn("No clusters found", out.getvalue())
        self.assertIn("genkei watchlist health", out.getvalue())


def _sample_candidates() -> list[Transaction]:
    """Two JPM officers selling same-day — the canonical cluster shape."""
    return [
        Transaction(
            issuer_cik="0000019617",
            reporter_cik="0001111111",
            reporter_name="Erdoes Mary E.",
            transaction_date=date(2026, 5, 15),
            transaction_code="S",
            acquired_disposed="D",
            shares=Decimal(6648),
            price_usd=Decimal("298.36"),
            accession_number="acc-1",
            is_officer=True,
            officer_title="CEO Asset & Wealth Management",
        ),
        Transaction(
            issuer_cik="0000019617",
            reporter_cik="0002222222",
            reporter_name="Lake Marianne",
            transaction_date=date(2026, 5, 15),
            transaction_code="S",
            acquired_disposed="D",
            shares=Decimal(6427),
            price_usd=Decimal("298.36"),
            accession_number="acc-2",
            is_officer=True,
            officer_title="CEO CCB",
        ),
    ]


if __name__ == "__main__":
    unittest.main()
