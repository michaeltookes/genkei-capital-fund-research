"""End-to-end test for the CoinGecko collector + normalizer (B-034)."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from genkei.common import db
from genkei.common.http import HttpClient
from genkei.ingest import coingecko as ingest
from genkei.normalize import coingecko as normalizer
from tests._postgres import get_harness, postgres_required

WATCHLIST = (
    "crypto:\n  primary:\n    - symbol: BTC\n      name: Bitcoin\n      coingecko_id: bitcoin\n"
)

COIN_PAYLOAD: dict = {
    "id": "bitcoin",
    "symbol": "btc",
    "name": "Bitcoin",
    "market_cap_rank": 1,
    "genesis_date": "2009-01-03",
    "description": {"en": "Bitcoin is the first decentralized cryptocurrency."},
    "links": {"homepage": ["https://bitcoin.org"]},
    "categories": ["Cryptocurrency"],
}

MARKET_CHART_PAYLOAD: dict = {
    "prices": [
        [1_700_000_000_000, 35_000.5],
        [1_700_086_400_000, 35_500.0],
    ],
    "market_caps": [
        [1_700_000_000_000, 700_000_000_000],
        [1_700_086_400_000, 710_000_000_000],
    ],
    "total_volumes": [
        [1_700_000_000_000, 20_000_000_000],
        [1_700_086_400_000, 21_000_000_000],
    ],
}


def _route(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/api/v3/coins/bitcoin":
        return httpx.Response(200, json=COIN_PAYLOAD)
    if path == "/api/v3/coins/bitcoin/market_chart":
        return httpx.Response(200, json=MARKET_CHART_PAYLOAD)
    return httpx.Response(404, text=f"unmocked: {request.url}")


@postgres_required
class CoingeckoIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.harness = get_harness()

    def setUp(self) -> None:
        from psycopg_pool import ConnectionPool

        self.harness.truncate_all()
        db.reset_pool()
        self._pool = ConnectionPool(conninfo=self.harness.url, min_size=1, max_size=2, open=True)
        db.set_pool(self._pool)

    def tearDown(self) -> None:
        db.reset_pool()
        self._pool.close()
        self.harness.truncate_all()

    def _run_collect(self, http: HttpClient) -> int:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(WATCHLIST, encoding="utf-8")
            return ingest.collect(path, http=http, api_key="demo-test-key")

    def test_full_run_writes_coins_and_market_data(self) -> None:
        transport = httpx.MockTransport(_route)
        with HttpClient("coingecko-test", transport=transport) as http:
            run_id = self._run_collect(http)

        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT endpoint_name FROM meta.raw_blobs "
                "WHERE ingest_run_id = %s ORDER BY endpoint_name",
                [run_id],
            )
            blob_names = [r[0] for r in cur.fetchall()]
        self.assertEqual(blob_names, ["coin_bitcoin", "market_chart_bitcoin"])

        normalizer_run = normalizer.normalize(source_run_id=run_id)

        with db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT symbol, name FROM coingecko.coins WHERE coingecko_id = 'bitcoin'")
            symbol, name = cur.fetchone()
            cur.execute(
                "SELECT count(*), max(price_usd) FROM coingecko.market_data "
                "WHERE coingecko_id = 'bitcoin'"
            )
            count, max_price = cur.fetchone()
            cur.execute(
                "SELECT status, metadata->>'source_run_id' FROM meta.ingest_runs WHERE id = %s",
                [normalizer_run],
            )
            status, source_run_str = cur.fetchone()

        self.assertEqual(symbol, "BTC")
        self.assertEqual(name, "Bitcoin")
        self.assertEqual(count, 2)
        self.assertEqual(float(max_price), 35_500.0)
        self.assertEqual(status, "success")
        self.assertEqual(int(source_run_str), run_id)

    def test_renormalize_is_idempotent(self) -> None:
        transport = httpx.MockTransport(_route)
        with HttpClient("coingecko-test", transport=transport) as http:
            run_id = self._run_collect(http)

        normalizer.normalize(source_run_id=run_id)
        normalizer.normalize(source_run_id=run_id)

        with db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM coingecko.coins")
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute("SELECT count(*) FROM coingecko.market_data")
            self.assertEqual(cur.fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
