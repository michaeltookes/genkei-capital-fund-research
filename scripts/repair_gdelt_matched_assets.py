"""One-time ops repair: recompute ``gdelt.gkg.matched_assets`` in place (B-147).

The B-147 matcher fix (word-boundary matching + curated PYTH/RENDER
``gdelt_terms``) changes only FUTURE ingests. Rows already stored in
``gdelt.gkg`` still carry the OLD, substring-era tags — most importantly the
bogus ``RENDER`` label sprayed onto every "rendering"/"surrender"/standalone-
"render" article, plus any other substring false positives. This script
repairs the historical rows without re-fetching GDELT: ``matched_assets`` is a
pure function of columns the table already stores (``themes``, ``persons``,
``organizations``, ``document_identifier``), so it recomputes each row with the
current watchlist + matcher and rewrites the column.

Run this ONCE against the lake after the B-147 fix merges. It cannot run from
a worktree without ``.env`` / lake reach — run it from the repo root on a host
that can see the Beelink Postgres:

    python -m scripts.repair_gdelt_matched_assets            # apply
    python -m scripts.repair_gdelt_matched_assets --dry-run  # report only

Behavior:
- A row whose recomputed labels differ from the stored ones is UPDATEd.
- A row that now matches ZERO watchlist terms is DELETEd — the ingester drops
  zero-match rows at parse time, so such a row only exists because a removed
  substring false positive (e.g. "surrender" → RENDER) put it there; keeping
  it would violate the "every row matches >=1 asset" table invariant.
- Rows that recompute identically are left untouched. The script is
  idempotent: a second run reports zero changes.

It is safe to re-run and safe to interrupt (writes commit per batch).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

from genkei.common import db
from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    load_watchlist,
)
from genkei.ingest.gdelt import build_match_terms, match_article

LOGGER = logging.getLogger("repair_gdelt_matched_assets")

READ_BATCH = 5000


def _iter_rows(batch: int):
    """Keyset-paginate the whole table by (published_at, gkg_record_id).

    Reads in a separate short-lived connection per page so the repair's writes
    don't run inside an open read cursor.
    """
    last_ts: Any = None
    last_id: str | None = None
    while True:
        with db.connection() as conn, conn.cursor() as cur:
            if last_ts is None:
                cur.execute(
                    "SELECT published_at, gkg_record_id, themes, persons, "
                    "organizations, document_identifier, matched_assets "
                    "FROM gdelt.gkg "
                    "ORDER BY published_at, gkg_record_id LIMIT %s",
                    [batch],
                )
            else:
                cur.execute(
                    "SELECT published_at, gkg_record_id, themes, persons, "
                    "organizations, document_identifier, matched_assets "
                    "FROM gdelt.gkg "
                    "WHERE (published_at, gkg_record_id) > (%s, %s) "
                    "ORDER BY published_at, gkg_record_id LIMIT %s",
                    [last_ts, last_id, batch],
                )
            rows = cur.fetchall()
        if not rows:
            return
        yield from rows
        last_ts, last_id = rows[-1][0], rows[-1][1]
        if len(rows) < batch:
            return


def repair(*, watchlist_path: Path | None, dry_run: bool) -> dict[str, int]:
    terms = build_match_terms(load_watchlist(watchlist_path or DEFAULT_WATCHLIST_PATH))
    if not terms:
        raise SystemExit("match-term list is empty — refusing to blank the table.")

    scanned = updated = deleted = 0
    updates: list[tuple[list[str], Any, str]] = []
    deletes: list[tuple[Any, str]] = []

    def flush() -> None:
        nonlocal updated, deleted
        if dry_run:
            updated += len(updates)
            deleted += len(deletes)
            updates.clear()
            deletes.clear()
            return
        with db.connection() as conn, conn.cursor() as cur:
            if updates:
                cur.executemany(
                    "UPDATE gdelt.gkg SET matched_assets = %s "
                    "WHERE published_at = %s AND gkg_record_id = %s",
                    updates,
                )
                updated += len(updates)
            if deletes:
                cur.executemany(
                    "DELETE FROM gdelt.gkg "
                    "WHERE published_at = %s AND gkg_record_id = %s",
                    deletes,
                )
                deleted += len(deletes)
        updates.clear()
        deletes.clear()

    for ts, rec_id, themes, persons, orgs, doc, stored in _iter_rows(READ_BATCH):
        scanned += 1
        recomputed = match_article(
            themes=list(themes or []),
            persons=list(persons or []),
            organizations=list(orgs or []),
            document_identifier=doc or "",
            terms=terms,
        )
        old = sorted(stored or [])
        if recomputed == old:
            continue
        if recomputed:
            updates.append((recomputed, ts, rec_id))
        else:
            deletes.append((ts, rec_id))
        if len(updates) + len(deletes) >= READ_BATCH:
            flush()
        if scanned % 50000 == 0:
            LOGGER.info("scanned %d rows...", scanned)
    flush()
    return {"scanned": scanned, "updated": updated, "deleted": deleted}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the change counts without writing anything.",
    )
    parser.add_argument("--watchlist", help="Override path to watchlists.yml.")
    args = parser.parse_args(argv)

    stats = repair(
        watchlist_path=Path(args.watchlist) if args.watchlist else None,
        dry_run=args.dry_run,
    )
    verb = "would update / delete" if args.dry_run else "updated / deleted"
    LOGGER.info(
        "gdelt.gkg matched_assets repair: scanned=%d %s = %d / %d",
        stats["scanned"],
        verb,
        stats["updated"],
        stats["deleted"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
