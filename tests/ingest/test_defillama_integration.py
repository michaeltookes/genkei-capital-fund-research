"""End-to-end test for genkei.ingest.defillama against real Postgres."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import httpx

from genkei.common import db
from genkei.common.http import HttpClient
from genkei.ingest import defillama as collector
from tests._postgres import PostgresTestCase

CONFIG = {
    "defillama_base_urls": {
        "core": "https://api.llama.fi",
        "coins": "https://coins.llama.fi",
        "stablecoins": "https://stablecoins.llama.fi",
    },
    "target_assets": [{"coingecko_id": "bitcoin"}],
    "chain_focus": ["Ethereum"],
    "collection_endpoints": [
        {"name": "protocols", "base": "core", "path": "/protocols"},
        {"name": "chains", "base": "core", "path": "/v2/chains"},
        {"name": "stablecoins", "base": "stablecoins", "path": "/stablecoins"},
    ],
}

ROUTES: dict[str, Any] = {
    "/prices/current/coingecko:bitcoin": {"coins": {"coingecko:bitcoin": {"price": 64000}}},
    "/protocols": [{"slug": "aave-v3", "name": "Aave V3", "chains": ["Ethereum"]}],
    "/v2/chains": [{"name": "Ethereum", "tvl": 50_000_000_000}],
    "/stablecoins": {"peggedAssets": []},
    "/v2/historicalChainTvl/Ethereum": [{"date": 1_700_000_000, "tvl": 49_000_000_000}],
}


def _route(request: httpx.Request) -> httpx.Response:
    payload = ROUTES.get(request.url.path)
    if payload is None:
        return httpx.Response(404, text=f"unmocked: {request.url}")
    return httpx.Response(200, json=payload)


class CollectorIntegrationTests(PostgresTestCase):
    def test_full_run_lands_one_blob_per_endpoint(self) -> None:
        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(json.dumps(CONFIG))
            transport = httpx.MockTransport(_route)
            with HttpClient("defillama-test", transport=transport) as http:
                run_id = collector.collect(config_path, http=http)

        with db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT status, rows_written FROM meta.ingest_runs WHERE id = %s", [run_id])
            status, rows_written = cur.fetchone()
            cur.execute(
                "SELECT endpoint_name FROM meta.raw_blobs "
                "WHERE ingest_run_id = %s ORDER BY endpoint_name",
                [run_id],
            )
            blob_names = [r[0] for r in cur.fetchall()]

        self.assertEqual(status, "success")
        self.assertEqual(rows_written, 5)
        self.assertEqual(
            blob_names,
            [
                "chain_tvl_history_ethereum",
                "chains",
                "prices_current",
                "protocols",
                "stablecoins",
            ],
        )

    def test_optional_endpoint_failure_records_partial_metadata(self) -> None:
        broken_routes = dict(ROUTES)
        broken_routes["/v2/historicalChainTvl/Ethereum"] = None  # 404

        def route_with_break(request: httpx.Request) -> httpx.Response:
            payload = broken_routes.get(request.url.path, "missing")
            if payload is None:
                return httpx.Response(404)
            if payload == "missing":
                return httpx.Response(404, text=f"unmocked: {request.url}")
            return httpx.Response(200, json=payload)

        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(json.dumps(CONFIG))
            transport = httpx.MockTransport(route_with_break)
            with HttpClient("defillama-test", transport=transport) as http:
                run_id = collector.collect(config_path, http=http)

        with db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT status, metadata FROM meta.ingest_runs WHERE id = %s", [run_id])
            status, metadata = cur.fetchone()
            cur.execute("SELECT count(*) FROM meta.raw_blobs WHERE ingest_run_id = %s", [run_id])
            blob_count = cur.fetchone()[0]

        self.assertEqual(status, "success")
        self.assertEqual(blob_count, 4)
        self.assertIn("partial_endpoints", metadata)
        self.assertEqual(metadata["partial_endpoints"][0]["name"], "chain_tvl_history_ethereum")


if __name__ == "__main__":
    unittest.main()
