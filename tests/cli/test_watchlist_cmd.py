"""Unit tests for the `genkei watchlist` subcommand group (B-044)."""

from __future__ import annotations

import io
import json as json_mod
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from genkei.cli import main
from genkei.cli.watchlist import (
    EXPECTED_ENDPOINTS,
    PRIMARY_TABLES,
    _format_gaps_human,
    _format_health_human,
    _format_list_human,
    _health_status_tag,
)


def _watchlist_path(case: unittest.TestCase) -> Path:
    ctx = TemporaryDirectory()
    case.addCleanup(ctx.cleanup)
    tmp = Path(ctx.name)
    path = tmp / "watchlists.yml"
    path.write_text(
        "crypto:\n"
        "  primary:\n"
        "    - symbol: BTC\n      name: Bitcoin\n      coingecko_id: bitcoin\n"
        "equities:\n"
        "  primary:\n"
        "    - symbol: AAPL\n      name: Apple Inc.\n      cik: \"0000320193\"\n"
        "    - symbol: NOCIK\n      name: Nocik Co.\n"
        "macro_series:\n"
        "  - id: DGS10\n    name: 10Y Treasury\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# `list`
# ---------------------------------------------------------------------------


class ListCommandTests(unittest.TestCase):
    def test_default_lists_all_three_sleeves(self) -> None:
        path = _watchlist_path(self)
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["watchlist", "list", "--config", str(path)])
        self.assertIn(code, (None, 0))
        text = out.getvalue()
        for needle in ("crypto", "equities", "macro", "BTC", "AAPL", "DGS10"):
            self.assertIn(needle, text)

    def test_sleeve_filter_restricts_output(self) -> None:
        path = _watchlist_path(self)
        out = io.StringIO()
        with redirect_stdout(out):
            main(["watchlist", "list", "--sleeve", "crypto", "--config", str(path)])
        text = out.getvalue()
        self.assertIn("BTC", text)
        self.assertNotIn("AAPL", text)
        self.assertNotIn("DGS10", text)

    def test_invalid_sleeve_rejected(self) -> None:
        path = _watchlist_path(self)
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(
                ["watchlist", "list", "--sleeve", "bogus", "--config", str(path)]
            )
        self.assertEqual(code, 2)

    def test_json_mode_emits_dict(self) -> None:
        path = _watchlist_path(self)
        out = io.StringIO()
        with redirect_stdout(out):
            main(["watchlist", "list", "--json", "--config", str(path)])
        parsed = json_mod.loads(out.getvalue())
        self.assertEqual(parsed["crypto"][0]["symbol"], "BTC")
        self.assertEqual(parsed["equities"][0]["cik"], "0000320193")
        self.assertEqual(parsed["macro"][0]["series_id"], "DGS10")


# ---------------------------------------------------------------------------
# `health`
# ---------------------------------------------------------------------------


class HealthStatusTagTests(unittest.TestCase):
    def test_missing_endpoint(self) -> None:
        row = {"status": "missing", "age_hours": None}
        self.assertEqual(_health_status_tag(row, stale_hours=36), "MISSING")

    def test_failed_run(self) -> None:
        row = {"status": "failed", "age_hours": 1.0}
        self.assertEqual(_health_status_tag(row, stale_hours=36), "FAIL")

    def test_stale_run(self) -> None:
        row = {"status": "success", "age_hours": 100.0}
        self.assertEqual(_health_status_tag(row, stale_hours=36), "STALE")

    def test_healthy_run(self) -> None:
        row = {"status": "success", "age_hours": 5.0}
        self.assertEqual(_health_status_tag(row, stale_hours=36), "OK")

    def test_empty_table(self) -> None:
        row = {"table": "coingecko.market_data", "row_count": 0, "error": None}
        self.assertEqual(_health_status_tag(row, stale_hours=36), "EMPTY")

    def test_populated_table(self) -> None:
        row = {"table": "fred.observations", "row_count": 229525, "error": None}
        self.assertEqual(_health_status_tag(row, stale_hours=36), "OK")


class HealthFormatTests(unittest.TestCase):
    def test_format_surfaces_missing_and_empty_loudly(self) -> None:
        rows = [
            {
                "source": "coingecko",
                "endpoint": "collect",
                "status": "missing",
                "last_started_at": None,
                "last_finished_at": None,
                "age_hours": None,
                "error": None,
            },
            {
                "source": "coingecko",
                "table": "coingecko.market_data",
                "row_count": 0,
                "error": None,
            },
        ]
        out = _format_health_human(rows, stale_hours=36)
        self.assertIn("MISSING", out)
        self.assertIn("EMPTY", out)
        self.assertIn("coingecko", out)


class HealthCommandTests(unittest.TestCase):
    def test_health_passes_stale_threshold_to_formatter(self) -> None:
        rows = [
            {
                "source": "fred",
                "endpoint": "collect",
                "status": "success",
                "last_started_at": "2026-05-16T00:00:00+00:00",
                "last_finished_at": "2026-05-16T00:00:30+00:00",
                "age_hours": 2.5,
                "error": None,
            }
        ]
        out = io.StringIO()
        with (
            patch("genkei.cli.watchlist._query_source_health", return_value=rows),
            redirect_stdout(out),
        ):
            code = main(["watchlist", "health", "--stale-hours", "48"])
        self.assertIn(code, (None, 0))
        self.assertIn("48", out.getvalue())
        self.assertIn("fred", out.getvalue())

    def test_health_json_mode(self) -> None:
        rows = [{"source": "fred", "table": "fred.observations", "row_count": 229525}]
        out = io.StringIO()
        with (
            patch("genkei.cli.watchlist._query_source_health", return_value=rows),
            redirect_stdout(out),
        ):
            main(["watchlist", "health", "--json"])
        parsed = json_mod.loads(out.getvalue())
        self.assertEqual(parsed[0]["row_count"], 229525)


class ExpectationsRegistryTests(unittest.TestCase):
    """Pin the schema/source registry so a silent drift surfaces in CI."""

    def test_all_four_sources_have_primary_tables(self) -> None:
        self.assertEqual(
            set(PRIMARY_TABLES),
            {"defillama", "fred", "sec", "coingecko"},
        )

    def test_every_source_expects_collect_and_normalize(self) -> None:
        for source, eps in EXPECTED_ENDPOINTS.items():
            self.assertIn("collect", eps, f"{source} missing collect")
            self.assertIn("normalize", eps, f"{source} missing normalize")


# ---------------------------------------------------------------------------
# `gaps`
# ---------------------------------------------------------------------------


class GapsFormatTests(unittest.TestCase):
    def test_format_tags_none_gap_and_ok(self) -> None:
        now = datetime.now(timezone.utc)
        rows = [
            {
                "sleeve": "crypto",
                "asset": "BTC",
                "key": "bitcoin",
                "source": "coingecko.market_data",
                "last_ts": None,
                "age_hours": None,
            },
            {
                "sleeve": "equity",
                "asset": "AAPL",
                "key": "0000320193",
                "source": "sec.filings",
                "last_ts": (now - timedelta(hours=72)).isoformat(),
                "age_hours": 72.0,
            },
            {
                "sleeve": "macro",
                "asset": "DGS10",
                "key": "DGS10",
                "source": "fred.observations",
                "last_ts": (now - timedelta(hours=2)).isoformat(),
                "age_hours": 2.0,
            },
        ]
        out = _format_gaps_human(rows, threshold_hours=36)
        self.assertIn("NONE", out)
        self.assertIn("GAP", out)
        self.assertIn("OK", out)
        self.assertIn("3 assets, 1 GAP, 1 NONE", out)


class GapsCommandTests(unittest.TestCase):
    def test_gaps_command_renders(self) -> None:
        path = _watchlist_path(self)
        now = datetime.now(timezone.utc)
        rows = [
            {
                "sleeve": "macro",
                "asset": "DGS10",
                "key": "DGS10",
                "source": "fred.observations",
                "last_ts": (now - timedelta(hours=10)).isoformat(),
                "age_hours": 10.0,
            }
        ]
        out = io.StringIO()
        with (
            patch("genkei.cli.watchlist._query_asset_gaps", return_value=rows),
            redirect_stdout(out),
        ):
            code = main(["watchlist", "gaps", "--config", str(path)])
        self.assertIn(code, (None, 0))
        self.assertIn("DGS10", out.getvalue())
        self.assertIn("OK", out.getvalue())

    def test_gaps_json_mode(self) -> None:
        path = _watchlist_path(self)
        rows = [{"sleeve": "macro", "asset": "DGS10", "last_ts": None, "age_hours": None}]
        out = io.StringIO()
        with (
            patch("genkei.cli.watchlist._query_asset_gaps", return_value=rows),
            redirect_stdout(out),
        ):
            main(["watchlist", "gaps", "--json", "--config", str(path)])
        parsed = json_mod.loads(out.getvalue())
        self.assertEqual(parsed[0]["asset"], "DGS10")


# ---------------------------------------------------------------------------
# Tiny coverage on `_format_list_human` directly so it isn't only tested
# through the command surface.
# ---------------------------------------------------------------------------


class FormatListDirectTests(unittest.TestCase):
    def test_no_sleeves_renders_placeholder(self) -> None:
        from genkei.cli._watchlist import Watchlist

        empty = Watchlist(crypto=[], equities=[], macro=[])
        text = _format_list_human(empty, sleeve="crypto")
        # Sleeve filter returns just the crypto section even if empty.
        self.assertIn("crypto", text)


if __name__ == "__main__":
    unittest.main()
