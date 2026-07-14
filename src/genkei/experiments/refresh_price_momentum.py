"""Refresh the trend-aggregation materialized views (B-067).

``analytics.price_momentum`` (and any future trend matview added to
``MATERIALIZED_VIEWS``) is precomputed, so it needs a periodic
``REFRESH MATERIALIZED VIEW`` to pick up new candles. This runs that refresh
**inside a ``meta.ingest_runs`` row** so ``genkei watchlist health`` can flag a
stale matview — the freshness failure mode a materialized view has that the
live ``analytics.*`` views (crypto_relative_strength, macro_regime_per_date) do
not.

``CONCURRENTLY`` (so reads aren't blocked during the refresh) requires (a) a
unique index on the matview — created by the migration — and (b) that the
statement runs **outside** a transaction block, hence the autocommit
connection here rather than the default transactional ``db.connection`` flow.

Run: ``python -m genkei.experiments.refresh_price_momentum`` (daily cron in
``.github/workflows/trend-views-daily.yml``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from psycopg import sql

from genkei.common import db

SOURCE_NAME = "price_momentum"
ENDPOINT = "refresh"

# (schema, matview) pairs refreshed each run. Extend this when a new trend
# aggregation matview lands — the refresh + health wiring then covers it for
# free.
MATERIALIZED_VIEWS: list[tuple[str, str]] = [
    ("analytics", "price_momentum"),
]

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RefreshResult:
    """Return value of :func:`refresh` for CLI / test inspection."""

    ingest_run_id: int
    views_refreshed: int
    total_rows: int


def _refresh_one(schema: str, matview: str) -> int:
    """``REFRESH MATERIALIZED VIEW CONCURRENTLY`` one view; return its row count.

    Uses an autocommit connection because ``REFRESH ... CONCURRENTLY`` cannot
    run inside a transaction block.
    """
    ident = sql.Identifier(schema, matview)
    with db.connection() as conn:
        original_autocommit = conn.autocommit
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("REFRESH MATERIALIZED VIEW CONCURRENTLY {}").format(ident)
                )
                cur.execute(sql.SQL("SELECT count(*) FROM {}").format(ident))
                row = cur.fetchone()
        finally:
            conn.autocommit = original_autocommit
    return int(row[0]) if row else 0


def refresh() -> RefreshResult:
    """Refresh every trend matview, recording one ``meta.ingest_runs`` row."""
    with db.ingest_run(
        SOURCE_NAME,
        endpoint=ENDPOINT,
        metadata={"views": [f"{s}.{m}" for s, m in MATERIALIZED_VIEWS]},
    ) as run:
        total_rows = 0
        for schema, matview in MATERIALIZED_VIEWS:
            count = _refresh_one(schema, matview)
            total_rows += count
            LOGGER.info("refreshed %s.%s (%s rows)", schema, matview, count)
        run.add_rows(total_rows)
        return RefreshResult(
            ingest_run_id=run.id,
            views_refreshed=len(MATERIALIZED_VIEWS),
            total_rows=total_rows,
        )


def main(argv: list[str] | None = None) -> int:
    import json as _json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    json_out = "--json" in (argv if argv is not None else sys.argv[1:])
    result = refresh()
    if json_out:
        print(
            _json.dumps(
                {
                    "ingest_run_id": result.ingest_run_id,
                    "views_refreshed": result.views_refreshed,
                    "total_rows": result.total_rows,
                    "source": SOURCE_NAME,
                }
            )
        )
    else:
        print(
            f"price_momentum refresh wrote ingest_run_id={result.ingest_run_id} "
            f"views={result.views_refreshed} total_rows={result.total_rows}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
