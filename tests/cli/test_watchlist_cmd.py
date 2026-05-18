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
from genkei.cli import watchlist as watchlist_mod
from genkei.cli.watchlist import (
    PRIMARY_TABLES,
    RECURRING_ENDPOINTS,
    _format_gaps_human,
    _format_health_human,
    _format_list_human,
    _health_status_tag,
    _query_source_health,
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

    def test_running_run_is_not_healthy(self) -> None:
        row = {"status": "running", "age_hours": 1.0}
        self.assertEqual(_health_status_tag(row, stale_hours=36), "FAIL")

    def test_partial_run_is_not_healthy(self) -> None:
        row = {"status": "partial", "age_hours": 1.0}
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
        rows = [{"source": "fred", "table": "fred.observations", "has_rows": True}]
        out = io.StringIO()
        with (
            patch("genkei.cli.watchlist._query_source_health", return_value=rows),
            redirect_stdout(out),
        ):
            main(["watchlist", "health", "--json"])
        parsed = json_mod.loads(out.getvalue())
        self.assertTrue(parsed[0]["has_rows"])
        self.assertEqual(parsed[0]["health_status"], "OK")

    def test_health_json_mode_applies_stale_threshold(self) -> None:
        rows = [
            {
                "source": "fred",
                "endpoint": "collect",
                "status": "success",
                "last_started_at": "2026-05-16T00:00:00+00:00",
                "last_finished_at": "2026-05-16T00:00:30+00:00",
                "age_hours": 72.0,
                "error": None,
            }
        ]
        out = io.StringIO()
        with (
            patch("genkei.cli.watchlist._query_source_health", return_value=rows),
            redirect_stdout(out),
        ):
            main(["watchlist", "health", "--json", "--stale-hours", "48"])
        parsed = json_mod.loads(out.getvalue())
        self.assertEqual(parsed[0]["health_status"], "STALE")


class QuerySourceHealthTests(unittest.TestCase):
    def test_primary_table_probe_uses_quoted_exists_query(self) -> None:
        captured: list[object] = []

        class FakeCursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, query, params=None):
                captured.append(query)

            def fetchall(self):
                return []

            def fetchone(self):
                return [True]

        class FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return FakeCursor()

        with (
            patch("genkei.cli.watchlist.db.connection", return_value=FakeConn()),
            patch("genkei.cli.watchlist.RECURRING_ENDPOINTS", {}),
            patch(
                "genkei.cli.watchlist.PRIMARY_TABLES",
                {"fred": ["fred.observations"]},
            ),
        ):
            rows = _query_source_health()

        self.assertIsInstance(captured[1], watchlist_mod.sql.Composed)
        self.assertEqual(rows[0]["table"], "fred.observations")
        self.assertTrue(rows[0]["has_rows"])
        self.assertNotIn("row_count", rows[0])


class ExpectationsRegistryTests(unittest.TestCase):
    """Pin the schema/source registry so a silent drift surfaces in CI."""

    def test_all_four_sources_have_primary_tables(self) -> None:
        self.assertEqual(
            set(PRIMARY_TABLES),
            {"defillama", "fred", "sec", "coingecko"},
        )

    def test_every_source_expects_collect_and_normalize(self) -> None:
        for source, eps in RECURRING_ENDPOINTS.items():
            self.assertIn("collect", eps, f"{source} missing collect")
            self.assertIn("normalize", eps, f"{source} missing normalize")


class OneShotEndpointFilteringTests(unittest.TestCase):
    """`health` must filter out one-shot endpoints (backfill, etc).

    Background: a deliberate one-shot run (e.g. defillama backfill on
    2026-05-10) shouldn't be tagged STALE 159h later just because it
    hasn't run since. STALE/OK/MISSING only apply to recurring crons.
    """

    def test_query_source_health_skips_endpoints_outside_recurring(self) -> None:
        # Fake meta.ingest_runs rows: one recurring (collect) + one
        # one-shot (backfill). Only the recurring one should show up.
        now = datetime.now(timezone.utc)
        run_rows = [
            ("defillama", "collect", "success", now, now, ""),
            ("defillama", "backfill", "success", now - timedelta(hours=159), now, ""),
            ("defillama", "normalize_backfill", "success", now - timedelta(hours=159), now, ""),
        ]

        class FakeCursor:
            def __init__(self):
                self._next = list(run_rows)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=None):  # noqa: ARG002
                pass

            def fetchall(self):
                return list(run_rows)

            def fetchone(self):
                return [True]

        class FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return FakeCursor()

        with (
            patch("genkei.cli.watchlist.db.connection", return_value=FakeConn()),
            patch(
                "genkei.cli.watchlist.RECURRING_ENDPOINTS",
                {"defillama": ["collect", "normalize"]},
            ),
            patch(
                "genkei.cli.watchlist.PRIMARY_TABLES",
                {"defillama": ["defillama.chain_tvl"]},
            ),
        ):
            rows = _query_source_health()
        endpoints_seen = {r.get("endpoint") for r in rows if "endpoint" in r}
        # `collect` is present; `backfill` / `normalize_backfill` filtered;
        # `normalize` surfaced as MISSING (recurring + never seen).
        self.assertIn("collect", endpoints_seen)
        self.assertIn("normalize", endpoints_seen)
        self.assertNotIn("backfill", endpoints_seen)
        self.assertNotIn("normalize_backfill", endpoints_seen)


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
        rows = [
            {"sleeve": "macro", "asset": "DGS10", "last_ts": None, "age_hours": None}
        ]
        out = io.StringIO()
        with (
            patch("genkei.cli.watchlist._query_asset_gaps", return_value=rows),
            redirect_stdout(out),
        ):
            main(["watchlist", "gaps", "--json", "--config", str(path)])
        parsed = json_mod.loads(out.getvalue())
        self.assertEqual(parsed[0]["asset"], "DGS10")
        self.assertEqual(parsed[0]["status"], "NONE")

    def test_gaps_json_mode_applies_threshold(self) -> None:
        path = _watchlist_path(self)
        now = datetime.now(timezone.utc)
        rows = [
            {
                "sleeve": "macro",
                "asset": "DGS10",
                "last_ts": (now - timedelta(hours=72)).isoformat(),
                "age_hours": 72.0,
            }
        ]
        out = io.StringIO()
        with (
            patch("genkei.cli.watchlist._query_asset_gaps", return_value=rows),
            redirect_stdout(out),
        ):
            main(
                [
                    "watchlist",
                    "gaps",
                    "--json",
                    "--threshold-hours",
                    "48",
                    "--config",
                    str(path),
                ]
            )
        parsed = json_mod.loads(out.getvalue())
        self.assertEqual(parsed[0]["status"], "GAP")


# ---------------------------------------------------------------------------
# Tiny coverage on `_format_list_human` directly so it isn't only tested
# through the command surface.
# ---------------------------------------------------------------------------


class FormatListDirectTests(unittest.TestCase):
    def test_no_sleeves_renders_placeholder(self) -> None:
        from genkei.cli._watchlist import Watchlist

        empty = Watchlist(crypto=[], equities=[], macro=[], protocols=[])
        text = _format_list_human(empty, sleeve="crypto")
        # Sleeve filter returns just the crypto section even if empty.
        self.assertIn("crypto", text)


if __name__ == "__main__":
    unittest.main()
