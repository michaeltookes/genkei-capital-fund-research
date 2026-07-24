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
    _components_iter,
    _drift_rows,
    _format_gaps_human,
    _format_health_human,
    _format_list_human,
    _health_status_tag,
    _query_asset_gaps,
    _query_source_health,
)
from genkei.common.schema_drift import DriftIssue


def _watchlist_path(case: unittest.TestCase) -> Path:
    ctx = TemporaryDirectory()
    case.addCleanup(ctx.cleanup)
    tmp = Path(ctx.name)
    path = tmp / "watchlists.yml"
    path.write_text(
        "crypto:\n"
        "  primary:\n"
        "    - symbol: BTC\n      name: Bitcoin\n      coingecko_id: bitcoin\n"
        "crypto_price_targets:\n"
        "  - symbol: LQTY\n    name: Liquity\n    coingecko_id: liquity\n"
        "equities:\n"
        "  primary:\n"
        "    - symbol: AAPL\n      name: Apple Inc.\n      cik: \"0000320193\"\n"
        "    - symbol: NOCIK\n      name: Nocik Co.\n"
        "macro_series:\n"
        "  - id: DGS10\n    name: 10Y Treasury\n"
        "eia:\n"
        "  - series_id: WTI_SPOT\n"
        "    name: Cushing OK WTI spot\n"
        "    route: petroleum/pri/spt\n"
        "    frequency: D\n"
        "    facets:\n"
        "      series: RWTC\n",
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


class DriftRowsTests(unittest.TestCase):
    def test_rows_include_alert_context_fields(self) -> None:
        issues = [
            DriftIssue(
                source="sec",
                endpoint_kind="submissions_<cik>",
                sample_endpoint_name="submissions_0000320193",
                kind="MISSING_REQUIRED_KEY",
                detail="required key 'filings' not in top-level object",
            )
        ]
        with (
            patch("genkei.cli.watchlist.check_recent_blobs", return_value=issues),
            # B-109 added a second drift source; mock to empty so this
            # test stays focused on the blob-shape drift contract.
            patch("genkei.cli.watchlist.check_natural_key_uniqueness", return_value=[]),
        ):
            rows = _drift_rows(max_age_hours=72)

        self.assertEqual(rows[0]["endpoint"], "submissions_<cik>")
        self.assertEqual(rows[0]["error"], "required key 'filings' not in top-level object")
        self.assertEqual(rows[0]["sample_endpoint_name"], "submissions_0000320193")

    def test_no_recent_samples_are_preserved(self) -> None:
        issues = [
            DriftIssue(
                source="fred",
                endpoint_kind="observations_<id>",
                sample_endpoint_name=None,
                kind="NO_RECENT_SAMPLES",
                detail="no raw_blobs rows matching pattern",
            )
        ]
        with (
            patch("genkei.cli.watchlist.check_recent_blobs", return_value=issues),
            patch("genkei.cli.watchlist.check_natural_key_uniqueness", return_value=[]),
        ):
            rows = _drift_rows(max_age_hours=72)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["drift_kind"], "NO_RECENT_SAMPLES")
        self.assertEqual(rows[0]["endpoint"], "observations_<id>")
        self.assertEqual(rows[0]["error"], "no raw_blobs rows matching pattern")

    def test_natural_key_uniqueness_rows_surface_in_drift_output(self) -> None:
        """B-109: a DUPLICATE_NATURAL_KEY DriftIssue propagates to the
        health-row output the same way blob-shape drift does, so a
        regression where the day-align contract breaks surfaces in
        `genkei watchlist health` rather than the next research
        session's manual SQL."""
        nk_issues = [
            DriftIssue(
                source="defillama",
                endpoint_kind="defillama.stablecoins",
                sample_endpoint_name=None,
                kind="DUPLICATE_NATURAL_KEY",
                detail="3 (asset_id + chain, ts::date) group(s) have >1 row in the last 30d",
            )
        ]
        with (
            patch("genkei.cli.watchlist.check_recent_blobs", return_value=[]),
            patch("genkei.cli.watchlist.check_natural_key_uniqueness", return_value=nk_issues),
        ):
            rows = _drift_rows(max_age_hours=72)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "defillama")
        self.assertEqual(rows[0]["endpoint"], "defillama.stablecoins")
        self.assertEqual(rows[0]["drift_kind"], "DUPLICATE_NATURAL_KEY")
        self.assertIn("group(s) have >1 row", rows[0]["error"])


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

    def test_all_known_sources_have_primary_tables(self) -> None:
        # The registry grows as new ingesters land (B-082 added
        # onchain_staking; B-090 added analytics for the
        # crypto_relative_strength view; B-064 added signal_emitter
        # for the cross-source correlation event store; B-107 added
        # ishares for spot crypto ETF daily snapshots; B-088 added
        # sui_staking for per-epoch Sui validator snapshots; B-089
        # added sui_unlocks for per-batch SUI vesting events; B-033
        # added gdelt for GKG news-firehose snapshots). Pin the
        # current shape so an accidental rename / drop is caught.
        self.assertEqual(
            set(PRIMARY_TABLES),
            {
                "defillama",
                "fred",
                "sec",
                "coingecko",
                "onchain_staking",
                "sui_staking",
                "sui_unlocks",
                "eth_whale_flow",
                "analytics",
                "signal_emitter",
                "cftc",
                "ishares",
                "bitwise",
                "zcash_usage",
                "gdelt",
                "bea",
                "treasury",
                "eia",
                "price_momentum",
            },
        )

    def test_sparse_anomaly_flags_are_not_primary_liveness(self) -> None:
        self.assertNotIn("anomaly_detector", PRIMARY_TABLES)
        self.assertEqual(RECURRING_ENDPOINTS["anomaly_detector"], ["anomaly_detection"])

    def test_sparse_alerts_are_heartbeat_not_primary_liveness(self) -> None:
        # B-068 — like the anomaly detector, the alert engine writes sparse
        # rows (only threshold-clearing stacks land), so it's a recurring
        # ingest-run heartbeat on 'evaluate', not a table-liveness source.
        self.assertNotIn("alert_engine", PRIMARY_TABLES)
        self.assertEqual(RECURRING_ENDPOINTS["alert_engine"], ["evaluate"])

    def test_price_momentum_matview_has_both_liveness_and_refresh_heartbeat(
        self,
    ) -> None:
        # The matview is always populated (one row per asset), so — unlike the
        # sparse anomaly flags — liveness is a valid empty/dropped guard, and
        # the daily refresh's ingest_run gives the staleness heartbeat.
        self.assertEqual(PRIMARY_TABLES["price_momentum"], ["analytics.price_momentum"])
        self.assertEqual(RECURRING_ENDPOINTS["price_momentum"], ["refresh"])

    def test_every_source_expects_at_least_a_collect_endpoint(self) -> None:
        """Recurring endpoint coverage includes every source health checks expect."""
        # All classic ingest sources have a `collect` endpoint. Some
        # exceptions: onchain_staking fuses collect+normalize, and
        # signal_emitter's endpoints are per-emitter (insider_clusters,
        # crowding, etc.) rather than the classic collect/normalize pair.
        self.assertEqual(
            set(RECURRING_ENDPOINTS),
            {
                "defillama",
                "fred",
                "sec",
                "coingecko",
                "onchain_staking",
                "sui_staking",
                "sui_unlocks",
                "eth_whale_flow",
                "cftc",
                "ishares",
                "bitwise",
                "sec_etf_shares",
                "zcash_usage",
                "gdelt",
                "bea",
                "treasury",
                "eia",
                "signal_emitter",
                "anomaly_detector",
                "price_momentum",
                "alert_engine",
            },
        )
        # Derived (compute-not-collect) sources whose endpoints aren't the
        # classic 'collect'/'normalize' pair.
        emitter_exempt = {
            "signal_emitter",
            "anomaly_detector",
            "price_momentum",
            "alert_engine",
        }
        for source, eps in RECURRING_ENDPOINTS.items():
            if source in emitter_exempt:
                self.assertTrue(eps, f"{source} should declare at least one emitter")
                continue
            self.assertIn("collect", eps, f"{source} missing collect")
        # The classic raw-blob + normalize ingesters still report both:
        for source in ("defillama", "fred", "sec", "coingecko", "eia"):
            self.assertIn(
                "normalize",
                RECURRING_ENDPOINTS[source],
                f"{source} missing normalize",
            )


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


class GapsQueryTests(unittest.TestCase):
    def test_query_asset_gaps_includes_eia_series(self) -> None:
        path = _watchlist_path(self)
        wl = watchlist_mod.load_watchlist(path)
        now = datetime.now(timezone.utc)
        fetch_values = {
            "bitcoin": now - timedelta(hours=1),
            "liquity": now - timedelta(hours=2),
            "0000320193": now.date(),
            "DGS10": now - timedelta(hours=3),
            "WTI_SPOT": now - timedelta(hours=4),
        }
        executed_params = []

        class FakeCursor:
            def __init__(self):
                self._last_param = None

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, query, params=None):  # noqa: ANN001, ARG002
                executed_params.append(params)
                self._last_param = params[0] if params else None

            def fetchone(self):
                return [fetch_values[self._last_param]]

        class FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return FakeCursor()

        with patch("genkei.cli.watchlist.db.connection", return_value=FakeConn()):
            rows = _query_asset_gaps(wl)

        eia_rows = [row for row in rows if row["source"] == "eia.observations"]
        self.assertEqual(len(eia_rows), 1)
        self.assertEqual(eia_rows[0]["asset"], "WTI_SPOT")
        crypto_price_rows = [
            row
            for row in rows
            if row["source"] == "coingecko.market_data" and row["asset"] == "LQTY"
        ]
        self.assertEqual(len(crypto_price_rows), 1)
        self.assertEqual(crypto_price_rows[0]["sleeve"], "price")
        self.assertEqual(crypto_price_rows[0]["key"], "liquity")
        self.assertIn(["liquity"], executed_params)
        self.assertIn(["WTI_SPOT"], executed_params)


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
        from genkei.common.watchlist import Watchlist

        empty = Watchlist(crypto=[], equities=[], macro=[], protocols=[], filers=[])
        text = _format_list_human(empty, sleeve="crypto")
        # Sleeve filter returns just the crypto section even if empty.
        self.assertIn("crypto", text)


class ScoreCommandTests(unittest.TestCase):
    def test_components_iter_preserves_names_from_persisted_dict(self) -> None:
        components = {
            "macro_regime": {"score": 1, "detail": "risk-on"},
            "tvl_trend": {"score": -1, "detail": "TVL -8%"},
        }

        rows = _components_iter(components)

        self.assertEqual(
            {(row["name"], row["score"]) for row in rows},
            {("macro_regime", 1), ("tvl_trend", -1)},
        )

    def test_score_rejects_invalid_sleeve(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["watchlist", "score", "--sleeve", "crypto-coree"])

        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
