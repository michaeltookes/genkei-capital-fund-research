"""Daily rank snapshot replacement against real Postgres."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import psycopg

from genkei.common import db
from genkei.normalize import coingecko
from tests._postgres import PostgresTestCase

FETCHED_AT = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)


def _payload(last_coin="coin-50"):
    return [
        {"id": f"coin-{rank}", "symbol": f"c{rank}", "market_cap_rank": rank}
        for rank in range(1, 50)
    ] + [{"id": last_coin, "symbol": "last", "market_cap_rank": 50}]


class RankSnapshotIntegrationTests(PostgresTestCase):
    def _normalize(self, payload, fetched_at=FETCHED_AT):
        # Only the raw-blob read is stubbed: writes, locking, and transaction
        # rollback run through the production normalizer and real Postgres.
        blobs = {"markets_top50": ("https://example/coins/markets", payload, fetched_at)}
        with patch.object(coingecko, "fetch_raw_blobs", return_value=blobs):
            return coingecko.normalize(source_run_id=1)

    def _snapshot(self, day=FETCHED_AT.date()):
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT coingecko_id, rank, ingest_run_id FROM coingecko.market_rank "
                "WHERE ts = %s ORDER BY rank",
                [day],
            )
            return cur.fetchall()

    def test_same_day_replacement_removes_departed_coin_and_preserves_other_days(self):
        yesterday = FETCHED_AT - timedelta(days=1)
        self._normalize(_payload(), yesterday)
        previous_day = self._snapshot(yesterday.date())
        self._normalize(_payload())
        replacement_run = self._normalize(_payload("new-coin"), FETCHED_AT + timedelta(hours=1))

        rows = self._snapshot()
        self.assertEqual(len(rows), 50)
        self.assertEqual([r[1] for r in rows], list(range(1, 51)))
        self.assertNotIn("coin-50", [r[0] for r in rows])
        self.assertEqual(rows[-1][0], "new-coin")
        self.assertEqual({r[2] for r in rows}, {replacement_run})
        self.assertEqual(self._snapshot(yesterday.date()), previous_day)

    def test_repeated_normalization_is_idempotent(self):
        self._normalize(_payload())
        latest_run = self._normalize(_payload())
        rows = self._snapshot()
        self.assertEqual(len(rows), 50)
        self.assertEqual([r[:2] for r in rows], [(f"coin-{r}", r) for r in range(1, 51)])
        self.assertEqual({r[2] for r in rows}, {latest_run})

    def test_insert_failure_rolls_back_the_entire_replacement(self):
        self._normalize(_payload())
        before = self._snapshot()
        replacement = _payload("new-coin")
        # Fail on the final row, after the DELETE and earlier inserts executed.
        replacement[-1]["market_cap_rank"] = 100_000
        with self.assertRaises(psycopg.errors.NumericValueOutOfRange):
            self._normalize(replacement)
        self.assertEqual(self._snapshot(), before)

    def test_unusable_or_missing_rank_blob_preserves_existing_snapshot(self):
        self._normalize(_payload())
        before = self._snapshot()
        for payload in ([], {"error": "unavailable"}, [{"id": "missing-rank"}]):
            with self.subTest(payload=payload):
                self._normalize(payload)
                self.assertEqual(self._snapshot(), before)
        with patch.object(coingecko, "fetch_raw_blobs", return_value={}):
            coingecko.normalize(source_run_id=1)
        self.assertEqual(self._snapshot(), before)
