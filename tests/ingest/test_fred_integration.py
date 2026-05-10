"""End-to-end test for the FRED collector + normalizer (B-028).

Mocks every FRED HTTP route and runs collect → normalize against the
testcontainers harness. Verifies blobs land with redacted URLs, two
vintages of the same observation produce two rows, and re-normalising
the same source_run_id is idempotent.
"""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from genkei.common import db
from genkei.common.http import HttpClient
from genkei.ingest import fred as ingest
from genkei.normalize import fred as normalizer
from tests._postgres import get_harness, postgres_required

WATCHLIST = (
    "macro_series:\n"
    "  - id: DGS10\n"
    "    name: 10-Year Treasury Yield\n"
    "  - id: GDPC1\n"
    "    name: Real GDP\n"
)


def _route(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    series_id = request.url.params.get("series_id")
    if path.endswith("/series"):
        return httpx.Response(
            200,
            json={
                "seriess": [
                    {
                        "id": series_id,
                        "title": f"Title for {series_id}",
                        "units": "Percent" if series_id == "DGS10" else "Billions of Dollars",
                        "frequency": "Daily" if series_id == "DGS10" else "Quarterly",
                        "popularity": 90,
                        "observation_start": "1962-01-02",
                        "observation_end": "2026-05-09",
                        "last_updated": "2026-05-09 15:18:01-05",
                    }
                ]
            },
        )
    if path.endswith("/series/observations"):
        if series_id == "GDPC1":
            # Two vintages of the same Q1 observation.
            return httpx.Response(
                200,
                json={
                    "observations": [
                        {
                            "date": "2024-01-01",
                            "realtime_start": "2024-04-25",
                            "realtime_end": "2024-05-29",
                            "value": "27000.0",
                        },
                        {
                            "date": "2024-01-01",
                            "realtime_start": "2024-05-30",
                            "realtime_end": "9999-12-31",
                            "value": "27100.5",
                        },
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "observations": [
                    {
                        "date": "2026-05-09",
                        "realtime_start": "2026-05-09",
                        "realtime_end": "9999-12-31",
                        "value": "4.32",
                    }
                ]
            },
        )
    return httpx.Response(404, text=f"unmocked: {request.url}")


@postgres_required
class FredIntegrationTests(unittest.TestCase):
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
            return ingest.collect(path, http=http, api_key="TESTKEY")

    def test_full_run_writes_series_and_observations(self) -> None:
        transport = httpx.MockTransport(_route)
        with HttpClient("fred-test", transport=transport) as http:
            run_id = self._run_collect(http)

        # Verify raw blobs landed and the API key was redacted.
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT endpoint_name, url FROM meta.raw_blobs "
                "WHERE ingest_run_id = %s ORDER BY endpoint_name",
                [run_id],
            )
            blobs = cur.fetchall()
        names = [name for name, _ in blobs]
        self.assertEqual(
            names,
            ["observations_DGS10", "observations_GDPC1", "series_DGS10", "series_GDPC1"],
        )
        for _name, url in blobs:
            self.assertNotIn("TESTKEY", url)
            self.assertIn("api_key=***", url)

        # Normalize.
        normalizer_run = normalizer.normalize(source_run_id=run_id)

        with db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM fred.series")
            self.assertEqual(cur.fetchone()[0], 2)
            cur.execute("SELECT count(*) FROM fred.observations WHERE series_id = 'GDPC1'")
            self.assertEqual(cur.fetchone()[0], 2)  # both vintages
            cur.execute(
                "SELECT realtime_start, value FROM fred.observations "
                "WHERE series_id = 'GDPC1' ORDER BY realtime_start"
            )
            rows = cur.fetchall()
            self.assertEqual(rows[0][0], date(2024, 4, 25))
            self.assertEqual(float(rows[0][1]), 27000.0)
            self.assertEqual(rows[1][0], date(2024, 5, 30))
            self.assertEqual(float(rows[1][1]), 27100.5)
            cur.execute(
                "SELECT status, metadata->>'source_run_id' FROM meta.ingest_runs WHERE id = %s",
                [normalizer_run],
            )
            status, source_run_str = cur.fetchone()
        self.assertEqual(status, "success")
        self.assertEqual(int(source_run_str), run_id)

    def test_renormalize_is_idempotent(self) -> None:
        transport = httpx.MockTransport(_route)
        with HttpClient("fred-test", transport=transport) as http:
            run_id = self._run_collect(http)

        normalizer.normalize(source_run_id=run_id)
        normalizer.normalize(source_run_id=run_id)

        with db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM fred.series")
            self.assertEqual(cur.fetchone()[0], 2)
            cur.execute("SELECT count(*) FROM fred.observations")
            self.assertEqual(cur.fetchone()[0], 3)  # 2 vintages + 1 daily

    def test_any_fetch_failures_mark_collector_run_failed(self) -> None:
        def failing_route(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/series") and request.url.params.get("series_id") == "DGS10":
                return _route(request)
            raise httpx.HTTPStatusError(
                f"401 unauthorized for {request.url}",
                request=request,
                response=httpx.Response(401, request=request),
            )

        with HttpClient("fred-test", transport=httpx.MockTransport(failing_route)) as http:
            with self.assertRaisesRegex(RuntimeError, "FRED fetch failed for 3 endpoint"):
                self._run_collect(http)

        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT status, rows_written, error, metadata FROM meta.ingest_runs "
                "WHERE source = 'fred' AND endpoint = 'collect'"
            )
            status, rows_written, error, metadata = cur.fetchone()
            cur.execute("SELECT count(*) FROM meta.raw_blobs")
            raw_blob_count = cur.fetchone()[0]

        self.assertEqual(status, "failed")
        self.assertEqual(rows_written, 1)
        self.assertIn("no partial macro snapshot", error)
        self.assertEqual(raw_blob_count, 1)
        self.assertEqual(len(metadata["partial_endpoints"]), 3)
        for failure in metadata["partial_endpoints"]:
            self.assertNotIn("TESTKEY", failure["url"])
            self.assertNotIn("TESTKEY", failure["error"])
            self.assertIn("api_key=***", failure["error"])


if __name__ == "__main__":
    unittest.main()
