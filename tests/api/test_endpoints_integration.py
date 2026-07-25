"""Endpoint contract tests for the FastAPI read layer (B-131).

Doubly-gated, exactly as the coordinator's spec requires:

* ``@postgres_required`` (inherited via :class:`PostgresTestCase`) skips the
  whole class when Docker / testcontainers isn't available — the endpoints hit
  a live TimescaleDB harness with the migration chain applied.
* ``@_fastapi_required`` skips when ``fastapi`` (the ``[api]`` extra) isn't
  importable, so the local 3.9 suite without the extra still passes.

Net: these run in CI (which installs ``.[dev,api]`` and ships Docker) and skip
cleanly anywhere either half is missing. Rows are seeded through
``genkei.common.db`` (which the base class wires to the harness), then each
endpoint is hit with FastAPI's ``TestClient`` and its JSON contract asserted.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from tests._postgres import PostgresTestCase

try:
    import fastapi  # noqa: F401
    from fastapi.testclient import TestClient

    _FASTAPI_OK = True
except ImportError:
    _FASTAPI_OK = False


_fastapi_required = unittest.skipUnless(
    _FASTAPI_OK, "fastapi ([api] extra) required for read-API endpoint tests"
)


def _new_ingest_run() -> int:
    """Insert a minimal meta.ingest_runs row and return its id (FK target)."""
    from genkei.common import db

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO meta.ingest_runs (source, endpoint, status) "
            "VALUES ('test', 'seed', 'success') RETURNING id"
        )
        return int(cur.fetchone()[0])


def _seed_bitcoin_prices() -> None:
    """Seed coingecko.coins + market_data for BTC (coingecko_id 'bitcoin')."""
    from genkei.common import db

    run_id = _new_ingest_run()
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO coingecko.coins (coingecko_id, symbol, name, source_endpoint, "
            "ingest_run_id) VALUES ('bitcoin', 'btc', 'Bitcoin', 'test', %s) "
            "ON CONFLICT (coingecko_id) DO NOTHING",
            [run_id],
        )
        for day, price in ((10, "60000.5"), (11, "61000.25"), (12, "62000.0")):
            cur.execute(
                "INSERT INTO coingecko.market_data (coingecko_id, ts, price_usd, "
                "market_cap_usd, volume_usd, source_endpoint, ingest_run_id) "
                "VALUES ('bitcoin', %s, %s, %s, %s, 'test', %s)",
                [
                    datetime(2026, 5, day, tzinfo=timezone.utc),
                    price,
                    "1200000000000",
                    "30000000000",
                    run_id,
                ],
            )


def _seed_signal_event() -> None:
    """Seed one meta.signal_events row for BTC."""
    from genkei.common import db

    run_id = _new_ingest_run()
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO meta.signal_events (asset, asset_class, horizon, ts, source, "
            "signal_kind, direction, strength, payload, source_ref, ingest_run_id) "
            "VALUES ('BTC', 'crypto', 'crypto:core', %s, 'tvl_drawdown', 'drawdown', "
            "'bearish', 2.5, '{}'::jsonb, 'ref-1', %s)",
            [datetime(2026, 5, 12, tzinfo=timezone.utc), run_id],
        )


@_fastapi_required
class ReadApiEndpointTests(PostgresTestCase):
    """Hit every endpoint against seeded harness data and assert the contract."""

    def setUp(self) -> None:
        super().setUp()
        from genkei.api.app import create_app

        self.client = TestClient(create_app())

    # -- /health ----------------------------------------------------------

    def test_health_reports_db_reachable(self) -> None:
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["service"], "genkei-api")
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["database"], "reachable")

    # -- /watchlist -------------------------------------------------------

    def test_watchlist_returns_all_sleeves(self) -> None:
        resp = self.client.get("/watchlist")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("crypto", body)
        self.assertIn("equities", body)
        self.assertTrue(any(c["symbol"] == "BTC" for c in body["crypto"]))

    def test_watchlist_sleeve_filter(self) -> None:
        resp = self.client.get("/watchlist", params={"sleeve": "crypto"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("crypto", body)
        self.assertNotIn("equities", body)

    def test_watchlist_rejects_bad_sleeve(self) -> None:
        resp = self.client.get("/watchlist", params={"sleeve": "bogus"})
        self.assertEqual(resp.status_code, 400)

    # -- /prices/{ticker} -------------------------------------------------

    def test_prices_returns_seeded_series(self) -> None:
        _seed_bitcoin_prices()
        resp = self.client.get("/prices/BTC")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["ticker"], "BTC")
        self.assertEqual(body["source"], "coingecko")
        self.assertEqual(len(body["rows"]), 3)
        # Decimal price serialized as a JSON number via the reader's float()
        # cast; newest row (ts DESC) is 2026-05-12.
        self.assertTrue(body["rows"][0]["ts"].startswith("2026-05-12"))

    def test_prices_limit_is_capped(self) -> None:
        _seed_bitcoin_prices()
        # Ask for more than exist; response is bounded by data, and an
        # over-max request is clamped rather than honored.
        resp = self.client.get("/prices/BTC", params={"limit": 999999})
        self.assertEqual(resp.status_code, 200)
        self.assertLessEqual(len(resp.json()["rows"]), 1000)

    def test_prices_unknown_ticker_404(self) -> None:
        resp = self.client.get("/prices/NOTATICKER")
        self.assertEqual(resp.status_code, 404)

    def test_prices_bad_source_400(self) -> None:
        resp = self.client.get("/prices/BTC", params={"source": "nasdaq"})
        self.assertEqual(resp.status_code, 400)

    def test_prices_bad_date_400(self) -> None:
        resp = self.client.get("/prices/BTC", params={"since": "not-a-date"})
        self.assertEqual(resp.status_code, 400)

    # -- /signals ---------------------------------------------------------

    def test_signals_returns_seeded_event(self) -> None:
        _seed_signal_event()
        resp = self.client.get("/signals")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["asset"], "BTC")
        self.assertEqual(body[0]["signal_kind"], "drawdown")
        self.assertEqual(body[0]["horizon_tag"], "crypto:core")

    def test_signals_asset_filter(self) -> None:
        _seed_signal_event()
        resp = self.client.get("/signals", params={"asset": "ETH"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_signals_bad_direction_400(self) -> None:
        resp = self.client.get("/signals", params={"direction": "sideways"})
        self.assertEqual(resp.status_code, 400)

    # -- /lake/health -----------------------------------------------------

    def test_lake_health_returns_rows_with_status(self) -> None:
        resp = self.client.get("/lake/health")
        self.assertEqual(resp.status_code, 200)
        rows = resp.json()
        self.assertIsInstance(rows, list)
        self.assertTrue(rows)
        # Every row carries the computed health_status tag.
        self.assertTrue(all("health_status" in r for r in rows))

    # -- /digest/weekly ---------------------------------------------------

    def test_digest_weekly_serves_latest_markdown(self) -> None:
        resp = self.client.get("/digest/weekly")
        # The repo ships weekly-*.md digests under reports/signals/, so this
        # serves the newest one; if the dir were empty it would be a clean 404.
        self.assertIn(resp.status_code, (200, 404))
        if resp.status_code == 200:
            body = resp.json()
            self.assertTrue(body["filename"].startswith("weekly-"))
            self.assertIsInstance(body["markdown"], str)

    # -- /research/decisions ----------------------------------------------

    def test_research_decisions_returns_frontmatter_index(self) -> None:
        resp = self.client.get("/research/decisions")
        self.assertEqual(resp.status_code, 200)
        rows = resp.json()
        self.assertIsInstance(rows, list)
        self.assertTrue(rows, "repo ships real decision files")
        first = rows[0]
        # Frontmatter contract keys (tests/test_research_decisions.py) plus the
        # file name the endpoint adds.
        self.assertIn("file", first)
        for key in ("date", "asset", "sleeve", "status"):
            self.assertIn(key, first)
        # Skip files are excluded.
        self.assertFalse(any(r["file"] in {"_template.md", "README.md"} for r in rows))


if __name__ == "__main__":
    unittest.main()
