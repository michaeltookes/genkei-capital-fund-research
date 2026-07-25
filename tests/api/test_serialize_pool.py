"""Offline unit tests for the read-API serializer + pool config (B-131).

These need only the ``[api]`` extra (fastapi) — no Docker — so they run
locally whenever fastapi is installed and skip cleanly otherwise. They pin the
two pieces the endpoint contract depends on but that don't require a live DB:
the shared-``json_default`` JSON shape and the small pool ceiling.
"""

from __future__ import annotations

import json
import os
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

try:
    import fastapi  # noqa: F401
    from fastapi.testclient import TestClient

    _FASTAPI_OK = True
except ImportError:
    _FASTAPI_OK = False

_fastapi_required = unittest.skipUnless(
    _FASTAPI_OK, "fastapi ([api] extra) required for read-API serializer tests"
)


@_fastapi_required
class SerializerTests(unittest.TestCase):
    def test_decimal_renders_as_string_like_the_cli(self) -> None:
        from genkei.api.serialize import GenkeiJSONResponse

        raw = GenkeiJSONResponse(content={"price": Decimal("60000.5")}).body
        self.assertEqual(json.loads(raw), {"price": "60000.5"})

    def test_date_renders_iso(self) -> None:
        from genkei.api.serialize import GenkeiJSONResponse

        raw = GenkeiJSONResponse(content={"d": date(2026, 5, 12)}).body
        self.assertEqual(json.loads(raw), {"d": "2026-05-12"})


@_fastapi_required
class SignalRouteSerializerTests(unittest.TestCase):
    def test_signals_response_preserves_decimal_strength(self) -> None:
        from genkei.api import app

        event = SimpleNamespace(
            event_id=7,
            asset="BTC",
            asset_class="crypto",
            horizon="crypto:core",
            ts=datetime(2026, 5, 12, tzinfo=timezone.utc),
            source="relative_strength",
            signal_kind="leader_crossing",
            direction="bullish",
            strength=Decimal("0.12345678901234567890"),
            payload={},
            source_ref="ref-7",
        )
        timeouts: list[int] = []

        @contextmanager
        def guarded(*, timeout_seconds: int):
            timeouts.append(timeout_seconds)
            yield object()

        with (
            patch("genkei.api.app.configure_pool", return_value=None),
            patch("genkei.common.db.readonly_connection", guarded),
            patch("genkei.experiments.signal_store.query_events", return_value=[event]),
            TestClient(app.create_app()) as client,
        ):
            response = client.get("/signals")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["strength"], "0.12345678901234567890")
        self.assertEqual(timeouts, [app.DATA_QUERY_TIMEOUT_SECONDS])


@_fastapi_required
class PoolCeilingTests(unittest.TestCase):
    def test_default_ceiling_is_small(self) -> None:
        from genkei.api import pool

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(pool._ENV_MAX_POOL_SIZE, None)
            self.assertEqual(pool.max_pool_size(), pool.DEFAULT_MAX_POOL_SIZE)
            self.assertLessEqual(pool.DEFAULT_MAX_POOL_SIZE, 4)

    def test_env_override_wins(self) -> None:
        from genkei.api import pool

        with patch.dict(os.environ, {pool._ENV_MAX_POOL_SIZE: "6"}):
            self.assertEqual(pool.max_pool_size(), 6)

    def test_bad_env_falls_back_to_default(self) -> None:
        from genkei.api import pool

        with patch.dict(os.environ, {pool._ENV_MAX_POOL_SIZE: "not-a-number"}):
            self.assertEqual(pool.max_pool_size(), pool.DEFAULT_MAX_POOL_SIZE)


@_fastapi_required
class WatchlistPayloadTests(unittest.TestCase):
    def test_prices_sleeve_exposes_yahoo_benchmarks(self) -> None:
        from genkei.api import app

        body = app._watchlist("prices")

        self.assertIn("benchmarks", body)
        self.assertTrue(any(b["symbol"] == "SPY" for b in body["benchmarks"]))
        self.assertNotIn("equities", body)

    def test_full_watchlist_exposes_yahoo_benchmarks(self) -> None:
        from genkei.api import app

        body = app._watchlist(None)

        self.assertIn("benchmarks", body)
        self.assertTrue(any(b["symbol"] == "QQQ" for b in body["benchmarks"]))


@_fastapi_required
class ArtifactPathTests(unittest.TestCase):
    def test_default_artifact_dirs_are_repo_rooted_not_cwd_relative(self) -> None:
        from genkei.api import app

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(app._ENV_DIGEST_DIR, None)
            os.environ.pop(app._ENV_DECISIONS_DIR, None)
            with TemporaryDirectory() as temp_dir:
                original_cwd = os.getcwd()
                try:
                    os.chdir(temp_dir)
                    self.assertEqual(app._digest_dir(), app._REPO_ROOT / "reports/signals")
                    self.assertEqual(
                        app._decisions_dir(), app._REPO_ROOT / "docs/research/decisions"
                    )
                finally:
                    os.chdir(original_cwd)

    def test_digest_dir_can_be_overridden(self) -> None:
        from genkei.api import app

        with TemporaryDirectory() as temp_dir:
            digest = Path(temp_dir) / "weekly-2026-07-25.md"
            digest.write_text("weekly body", encoding="utf-8")

            with patch.dict(os.environ, {app._ENV_DIGEST_DIR: temp_dir}):
                self.assertEqual(
                    app._latest_digest(),
                    {"filename": digest.name, "markdown": "weekly body"},
                )

    def test_decisions_dir_can_be_overridden(self) -> None:
        from genkei.api import app

        with TemporaryDirectory() as temp_dir:
            decision = Path(temp_dir) / "2026-07-25-test.md"
            decision.write_text(
                "---\n"
                "date: 2026-07-25\n"
                "asset: TEST\n"
                "sleeve: crypto-core\n"
                "status: pending\n"
                "---\n"
                "body\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {app._ENV_DECISIONS_DIR: temp_dir}):
                rows = app._research_decisions()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["file"], decision.name)
        self.assertEqual(rows[0]["asset"], "TEST")


@_fastapi_required
class ReadonlyGuardUsageTests(unittest.TestCase):
    def _guard(self, calls: list[int], conn: object):
        @contextmanager
        def guard(*, timeout_seconds: int):
            calls.append(timeout_seconds)
            yield conn

        return guard

    def test_prices_passes_guarded_connection_to_reader(self) -> None:
        from genkei.api import app

        calls: list[int] = []
        guarded_conn = object()
        with (
            patch("genkei.common.db.readonly_connection", self._guard(calls, guarded_conn)),
            patch("genkei.cli.prices._query_coingecko_market_data", return_value=[]) as query,
        ):
            body = app._prices("BTC", source="coingecko", since=None, until=None, limit=5)

        self.assertEqual(body, {"ticker": "BTC", "source": "coingecko", "rows": []})
        self.assertEqual(calls, [app.DATA_QUERY_TIMEOUT_SECONDS])
        self.assertIs(query.call_args.kwargs["conn"], guarded_conn)

    def test_signals_passes_guarded_connection_to_query_events(self) -> None:
        from genkei.api import app

        calls: list[int] = []
        guarded_conn = object()
        with (
            patch("genkei.common.db.readonly_connection", self._guard(calls, guarded_conn)),
            patch("genkei.experiments.signal_store.query_events", return_value=[]) as query,
        ):
            rows = app._signals(
                asset=None,
                source=None,
                signal_kind=None,
                direction=None,
                since=None,
                until=None,
                limit=5,
            )

        self.assertEqual(rows, [])
        self.assertEqual(calls, [app.DATA_QUERY_TIMEOUT_SECONDS])
        self.assertIs(query.call_args.kwargs["conn"], guarded_conn)

    def test_lake_health_passes_guarded_connection_to_reader(self) -> None:
        from genkei.api import app

        calls: list[int] = []
        guarded_conn = object()
        with (
            patch("genkei.common.db.readonly_connection", self._guard(calls, guarded_conn)),
            patch("genkei.cli.watchlist._query_source_health", return_value=[]) as query,
            patch("genkei.cli.watchlist._with_health_status", return_value=[]) as with_status,
        ):
            rows = app._lake_health(12.0)

        self.assertEqual(rows, [])
        self.assertEqual(calls, [app.DATA_QUERY_TIMEOUT_SECONDS])
        query.assert_called_once_with(conn=guarded_conn)
        with_status.assert_called_once_with([], stale_hours=12.0)


if __name__ == "__main__":
    unittest.main()
