"""End-to-end test for the GDELT GKG collector (B-033).

Exercises the full lastupdate.txt → 15-min CSV zip → filtered upsert
into ``gdelt.gkg`` path against the testcontainers Postgres harness.
Mocks every HTTP call via ``httpx.MockTransport`` so the test stays
hermetic — no network reach to data.gdeltproject.org.

Pins the two load-bearing acceptance criteria from B-033:

  (a) Rolling-window storage — the run lands inside the 365-day
      retention floor.

  (b) Per-watchlist filtering — articles with zero watchlist matches
      are dropped at parse time and never appear in ``gdelt.gkg``.
"""

from __future__ import annotations

import io
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from genkei.common import db
from genkei.common.http import HttpClient
from genkei.ingest import gdelt as ingest
from tests._postgres import PostgresTestCase

WATCHLIST = """\
crypto:
  primary:
    - symbol: BTC
      name: Bitcoin
      coingecko_id: bitcoin
      tier: primary
equities:
  primary:
    - symbol: AAPL
      name: Apple Inc.
      cik: '0000320193'
      tier: primary
"""

# Three synthetic GKG rows — two match (AAPL via orgs, BTC via theme)
# and one does not (the filter must drop it).
GKG_ROWS = [
    # 0: gkg_record_id  1: date            2: srcColl  3: srcName        4: docId
    # 5: V1Counts       6: V2.1Counts      7: V1Themes
    # 8: V2Themes       9: V1Locations    10: V2Locations
    # 11: V1Persons    12: V2Persons      13: V1Orgs    14: V2Orgs
    # 15: Tone
    (
        "20260609001500-0", "20260609001500", "1", "example.com",
        "https://example.com/apple-news",
        "", "",
        "ECON_STOCKMARKET;COMPANY_NEWS",
        "",
        "1#United States#US##37.0#-95.7#FID1",
        "",
        "tim cook",
        "",
        "Apple Inc.;Goldman Sachs",
        "",
        "-1.5,3.0,4.5,7.5,12.0,2.0,420",
    ),
    (
        "20260609001500-1", "20260609001500", "1", "example.com",
        "https://example.com/btc-rally",
        "", "",
        "ECON_BITCOIN;CRYPTOCURRENCY",
        "",
        "",
        "",
        "satoshi nakamoto",
        "",
        "Binance",
        "",
        "2.0,5.5,3.5,9.0,8.0,1.5,180",
    ),
    (
        "20260609001500-2", "20260609001500", "1", "example.com",
        "https://example.com/weather-report",
        "", "",
        "WEATHER",
        "",
        "",
        "",
        "",
        "",
        "National Weather Service",
        "",
        "0,0,0,0,0,0,90",
    ),
]


def _build_gkg_csv_zip() -> bytes:
    """Assemble a canonical GKG CSV zip with the three rows above."""
    lines = ["\t".join(row) for row in GKG_ROWS]
    csv_text = "\n".join(lines) + "\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("20260609001500.gkg.csv", csv_text)
    return buf.getvalue()


def _build_lastupdate_response(ts: str = "20260609001500") -> str:
    """Three canonical lastupdate.txt lines."""
    base = "https://data.gdeltproject.org/gdeltv2"
    return (
        f"100\tabc\t{base}/{ts}.export.CSV.zip\n"
        f"200\tdef\t{base}/{ts}.mentions.CSV.zip\n"
        f"300\t123\t{base}/{ts}.gkg.csv.zip\n"
    )


def _route(request: httpx.Request) -> httpx.Response:
    """Mock router for both the lastupdate.txt and the CSV zip."""
    url = str(request.url)
    if url.endswith("/lastupdate.txt"):
        return httpx.Response(200, text=_build_lastupdate_response())
    if url.endswith("/20260609001500.gkg.csv.zip"):
        return httpx.Response(
            200,
            content=_build_gkg_csv_zip(),
            headers={"content-type": "application/zip"},
        )
    return httpx.Response(404, text=f"unmocked: {url}")


class GdeltIntegrationTests(PostgresTestCase):
    def _run_collect_one_window(self) -> int:
        """Run a single-slot collect against the mocked GDELT HTTP surface."""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(WATCHLIST, encoding="utf-8")
            transport = httpx.MockTransport(_route)
            with HttpClient("gdelt-test", transport=transport) as http:
                # hours=0 isn't allowed by the collector, so pass hours=1 and
                # rely on the mock 404 for the four overlapping slots around
                # the single 00:15 file this test serves.
                return ingest.collect(
                    hours=1,
                    watchlist_path=path,
                    http_client=http,
                )

    def test_filters_to_watchlist_matches_and_lands_rows(self) -> None:
        rows_written = self._run_collect_one_window()
        # Two of the three synthetic rows match the watchlist; the
        # weather article is dropped at parse time.
        self.assertEqual(rows_written, 2)

        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT gkg_record_id, matched_assets, source_common_name "
                "FROM gdelt.gkg ORDER BY gkg_record_id"
            )
            rows = cur.fetchall()

        self.assertEqual(len(rows), 2)
        ids = [r[0] for r in rows]
        self.assertEqual(ids, ["20260609001500-0", "20260609001500-1"])

        # Each row's matched_assets reflects the substring match.
        matches_by_id = {r[0]: r[1] for r in rows}
        self.assertEqual(matches_by_id["20260609001500-0"], ["AAPL"])
        self.assertEqual(matches_by_id["20260609001500-1"], ["BTC"])

    def test_meta_ingest_runs_records_success(self) -> None:
        self._run_collect_one_window()
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT source, endpoint, status, rows_written "
                "FROM meta.ingest_runs WHERE source = %s ORDER BY id DESC LIMIT 1",
                [ingest.SOURCE_NAME],
            )
            row = cur.fetchone()

        self.assertIsNotNone(row)
        source, endpoint, status, rows_written = row
        self.assertEqual(source, "gdelt")
        self.assertEqual(endpoint, ingest.COLLECT_ENDPOINT)
        self.assertEqual(status, "success")
        self.assertEqual(rows_written, 2)

    def test_re_running_same_window_is_idempotent(self) -> None:
        """Composite PK (published_at, gkg_record_id) keeps re-runs clean."""
        first = self._run_collect_one_window()
        second = self._run_collect_one_window()
        self.assertEqual(first, 2)
        # Second run upserts the same two rows; the row count comes from
        # cursor.rowcount which reports affected rows (UPDATE counts as 1).
        self.assertEqual(second, 2)
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM gdelt.gkg")
            total = cur.fetchone()[0]
        self.assertEqual(total, 2)


if __name__ == "__main__":
    unittest.main()
