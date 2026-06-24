"""End-to-end test for the Coinbase candles ``--backfill`` path (B-121).

The backfill is the recovery mechanism when daily ingest misses days, and it
had no automated coverage. Mocks the Coinbase candles HTTP route and runs
``coinbase.backfill`` against the testcontainers harness, asserting the chunked
window walk lands one raw blob per (product, window) and the run records
success. Uses the shared :class:`PostgresTestCase` harness base.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from genkei.common import db
from genkei.common.http import HttpClient
from genkei.ingest import coinbase as ingest
from tests._postgres import PostgresTestCase

WATCHLIST = """\
version: 1
crypto:
  primary:
    - symbol: BTC
      name: Bitcoin
      coingecko_id: bitcoin
      coinbase_product: BTC-USD
"""


def _route(request: httpx.Request) -> httpx.Response:
    """Return a one-row candle array for the BTC-USD candles endpoint."""
    if request.url.path == "/products/BTC-USD/candles":
        # Coinbase candle row shape: [time, low, high, open, close, volume].
        return httpx.Response(200, json=[[1_700_000_000, 1.0, 2.0, 1.5, 1.8, 100.0]])
    return httpx.Response(404, text=f"unmocked: {request.url}")


class CoinbaseBackfillIntegrationTests(PostgresTestCase):
    def _run_backfill(self, *, since: date, until: date, http: HttpClient) -> int:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(WATCHLIST, encoding="utf-8")
            return ingest.backfill(path, since=since, until=until, http=http)

    def _blob_names(self, run_id: int) -> list[str]:
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT endpoint_name FROM meta.raw_blobs "
                "WHERE ingest_run_id = %s ORDER BY endpoint_name",
                [run_id],
            )
            return [row[0] for row in cur.fetchall()]

    def _run_status(self, run_id: int) -> str:
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT status FROM meta.ingest_runs WHERE id = %s", [run_id])
            return cur.fetchone()[0]

    def test_single_window_writes_one_candle_blob(self) -> None:
        until = date(2024, 6, 30)
        since = until - timedelta(days=2)  # well under the 280-day chunk size
        transport = httpx.MockTransport(_route)

        with HttpClient("coinbase-test", transport=transport) as http:
            run_id = self._run_backfill(since=since, until=until, http=http)

        self.assertEqual(self._run_status(run_id), "success")
        names = self._blob_names(run_id)
        self.assertEqual(len(names), 1)
        self.assertTrue(names[0].startswith("candles_BTC-USD_"))

    def test_long_range_walks_multiple_chunk_windows(self) -> None:
        until = date(2024, 6, 30)
        since = until - timedelta(days=300)  # 300 days → two 280-day windows
        transport = httpx.MockTransport(_route)

        with HttpClient("coinbase-test", transport=transport) as http:
            run_id = self._run_backfill(since=since, until=until, http=http)

        self.assertEqual(self._run_status(run_id), "success")
        # One blob per window; the chunk walk should produce exactly two.
        self.assertEqual(len(self._blob_names(run_id)), 2)
