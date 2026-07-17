"""End-to-end tests for the SEC ETF shares-outstanding backfill (B-114).

Pins against a live TimescaleDB container:

  * ``collect`` lands quarter-end checkpoints into etf.fund_snapshots with the
    sec_10q_xbrl source marker.
  * ``DO NOTHING`` on the ``(ticker, snapshot_date)`` PK never overwrites an
    existing daily-feed row (daily wins) and is idempotent on re-run.
  * The net-flow query (``genkei etf-flows --net-flow``) excludes the quarterly
    checkpoints so they never enter the daily-flow LAG.
"""

from __future__ import annotations

import unittest
from datetime import date
from typing import Any

from genkei.cli.etf_flows import _query_net_flow
from genkei.common import db
from genkei.common.watchlist import EtfTickerEntry
from genkei.ingest import sec_etf_shares
from tests._postgres import PostgresTestCase


def _fact(end: str, val: float, *, form: str = "10-Q", filed: str = "2024-05-08") -> dict:
    return {"end": end, "val": val, "form": form, "filed": filed}


def _companyfacts() -> dict:
    return {
        "facts": {
            "us-gaap": {
                "TemporaryEquitySharesOutstanding": {
                    "units": {
                        "shares": [
                            _fact("2024-03-31", 442400000, filed="2024-05-08"),
                            _fact("2024-06-30", 539160000, filed="2024-08-08"),
                        ]
                    }
                },
                "FairValueNetAssetLiability": {
                    "units": {
                        "USD": [
                            _fact("2024-03-31", 17788884882, filed="2024-05-08"),
                            _fact("2024-06-30", 19449621956, filed="2024-08-08"),
                        ]
                    }
                },
            }
        }
    }


class _FakeHttp:
    """Minimal HttpClient stand-in returning a canned companyfacts payload."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.calls: list[str] = []

    def get_json(self, url: str, **_kw: Any) -> dict:
        self.calls.append(url)
        return self._payload

    def close(self) -> None:  # pragma: no cover - not owned here
        pass


def _ibit_entry() -> EtfTickerEntry:
    return EtfTickerEntry(
        ticker="IBIT",
        name="iShares Bitcoin Trust ETF",
        asset="BTC",
        issuer="BlackRock",
        launch_date="2024-01-11",
        cik="0001980994",
    )


class SecEtfSharesIntegrationTests(PostgresTestCase):
    def _one_fund_watchlist(self) -> Any:
        from unittest.mock import patch

        from genkei.common.watchlist import Watchlist

        wl = Watchlist(
            crypto=[], equities=[], macro=[], protocols=[], filers=[],
            etf_tickers=[_ibit_entry()],
        )
        return patch("genkei.ingest.sec_etf_shares.load_watchlist", return_value=wl)

    def test_collect_lands_checkpoints(self) -> None:
        http = _FakeHttp(_companyfacts())
        with self._one_fund_watchlist():
            sec_etf_shares.collect(http=http)
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT snapshot_date, shares_outstanding, source_endpoint "
                "FROM etf.fund_snapshots WHERE ticker = 'IBIT' ORDER BY snapshot_date"
            )
            rows = cur.fetchall()
        self.assertEqual([r[0] for r in rows], [date(2024, 3, 31), date(2024, 6, 30)])
        self.assertTrue(all(r[2] == "sec_10q_xbrl" for r in rows))

    def test_do_nothing_never_clobbers_daily_row(self) -> None:
        # Seed an authoritative daily-feed row on a date the 10-Q also covers.
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO meta.ingest_runs (source, endpoint, status) "
                "VALUES ('daily', 'collect', 'running') RETURNING id"
            )
            run_id = int(cur.fetchone()[0])
            cur.execute(
                """
                INSERT INTO etf.fund_snapshots (
                    ticker, snapshot_date, issuer, asset, nav_per_share_usd,
                    total_net_assets_usd, shares_outstanding, source_endpoint,
                    ingest_run_id
                ) VALUES ('IBIT', '2024-03-31', 'BlackRock', 'BTC', 40.21,
                          17788884882, 442400000, 'daily_feed', %s)
                """,
                [run_id],
            )
            conn.commit()

        http = _FakeHttp(_companyfacts())
        with self._one_fund_watchlist():
            sec_etf_shares.collect(http=http)

        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT source_endpoint FROM etf.fund_snapshots "
                "WHERE ticker = 'IBIT' AND snapshot_date = '2024-03-31'"
            )
            # The daily row survives; the 10-Q did NOT overwrite it.
            self.assertEqual(cur.fetchone()[0], "daily_feed")
            # The non-colliding 2024-06-30 checkpoint still landed.
            cur.execute(
                "SELECT count(*) FROM etf.fund_snapshots "
                "WHERE ticker = 'IBIT' AND source_endpoint = 'sec_10q_xbrl'"
            )
            self.assertEqual(cur.fetchone()[0], 1)

    def test_rerun_is_idempotent(self) -> None:
        http = _FakeHttp(_companyfacts())
        with self._one_fund_watchlist():
            sec_etf_shares.collect(http=http)
            sec_etf_shares.collect(http=_FakeHttp(_companyfacts()))
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM etf.fund_snapshots WHERE ticker = 'IBIT'")
            self.assertEqual(cur.fetchone()[0], 2)

    def test_net_flow_excludes_quarterly_checkpoints(self) -> None:
        http = _FakeHttp(_companyfacts())
        with self._one_fund_watchlist():
            sec_etf_shares.collect(http=http)
        # Only sec_10q rows exist → net-flow (which excludes them) is empty.
        rows = _query_net_flow(
            "BTC",
            [("IBIT", None)],
            since=date(2024, 1, 1),
            until=None,
            limit=100,
        )
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
