"""SEC Form 4 XML collector (B-079).

The submissions index already gives us Form 4 *filings* (one row per
filing in ``sec.filings`` with ``form_type`` in ``('4', '4/A')``). Form 4's structured
payload — the actual insider transactions — lives in a per-filing XML
document that the existing SEC collector doesn't fetch.

This module fills that gap. For every ``sec.filings`` row with
``form_type`` of ``4`` or ``4/A`` that doesn't yet have a ``form4_<accession>`` blob in
``meta.raw_blobs``, fetch the XML and land it as one raw blob.
Downstream ``genkei.normalize.sec_form4`` parses the blobs into
``sec.form4_transactions`` + ``sec.insiders``.

Two modes:

* **Incremental** (default) — pull up to ``--limit`` (default 200)
  uncached Form 4 filings, newest first. ~25s at 8 req/s. Safe for a
  daily cron.
* **Backfill** (``--backfill``) — drop the limit and fetch *every*
  uncached Form 4. At 36 634 historical Form 4s on the watchlist
  today, that's ~76 min at 8 req/s; run once after migration.

URL pattern: the ``sec.filings.primary_document`` column points at the
XSLT-styled HTML viewer (e.g. ``xslF345X06/form4.xml``); the raw XML
lives at the same accession folder under the basename
(``form4.xml``). We strip any leading ``xsl*/`` prefix to get the raw
filename. Endpoint base is ``https://www.sec.gov/Archives/edgar/data``
(note: the public Archives host, not ``data.sec.gov``). Shares the
8 req/s limit + ``SEC_USER_AGENT`` from ``genkei.ingest.sec``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from typing import Any

import httpx

from genkei.common import db
from genkei.common.http import HttpClient
from genkei.ingest.sec import (
    DEFAULT_RATE_LIMIT,
    SOURCE_NAME,
    resolve_user_agent,
)

COLLECT_FORM4_ENDPOINT_LABEL = "collect_form4"
FORM4_BLOB_PREFIX = "form4_"
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
DEFAULT_LIMIT = 200
RAW_BLOBS_INSERT = (
    "INSERT INTO meta.raw_blobs (ingest_run_id, endpoint_name, url, payload) "
    "VALUES (%s, %s, %s, %s::jsonb) "
    "ON CONFLICT (ingest_run_id, endpoint_name) DO NOTHING"
)
LOGGER = logging.getLogger(__name__)


def positive_int(value: str) -> int:
    """Argparse type for positive integer limits."""
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("--limit must be greater than 0")
    return parsed


@dataclass(frozen=True)
class Form4Target:
    """One Form 4 filing to fetch."""

    accession_number: str
    cik: str
    primary_document: str
    filed_at: str  # ISO date as string for stable logging


def select_uncached_form4s(*, limit: int | None) -> list[Form4Target]:
    """Return Form 4 filings not already cached or normalized.

    Newest filings first so the daily cron always catches up on recent
    activity even if older backfill is incomplete.
    """
    sql = """
        SELECT f.accession_number, f.cik, f.primary_document, f.filed_at
        FROM sec.filings f
        WHERE f.form_type IN ('4', '4/A')
          AND f.primary_document IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM meta.raw_blobs r
              WHERE r.endpoint_name = %s || f.accession_number
          )
          AND NOT EXISTS (
              SELECT 1 FROM sec.form4_normalized_filings n
              WHERE n.accession_number = f.accession_number
          )
        ORDER BY f.filed_at DESC, f.accession_number DESC
    """
    params: list[Any] = [FORM4_BLOB_PREFIX]
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        Form4Target(
            accession_number=accn,
            cik=cik,
            primary_document=doc,
            filed_at=filed.isoformat() if filed else "",
        )
        for (accn, cik, doc, filed) in rows
    ]


def strip_xsl_prefix(primary_document: str) -> str:
    """Convert ``xslF345X06/form4.xml`` → ``form4.xml`` (raw XML basename).

    Form 4 ``primary_document`` values are usually the XSLT viewer path;
    stripping any leading ``xsl*/`` directory yields the raw XML at the
    same accession folder. Documents that don't carry the prefix pass
    through unchanged.
    """
    if "/" not in primary_document:
        return primary_document
    head, _, tail = primary_document.partition("/")
    if head.startswith("xsl"):
        return tail
    return primary_document


def build_form4_xml_url(cik: str, accession_number: str, primary_document: str) -> str:
    """Build the public Archives URL for a Form 4 XML document.

    EDGAR's archive paths use the *integer* CIK (no leading zeros) and
    the dash-stripped accession number as folder names. Example:
    ``…/Archives/edgar/data/320193/000114036126020871/form4.xml``.
    """
    folder = accession_number.replace("-", "")
    cik_int = int(cik)  # strip leading zeros; sec.companies stores zero-padded
    basename = strip_xsl_prefix(primary_document)
    return f"{ARCHIVES_BASE}/{cik_int}/{folder}/{basename}"


def _store_blob(
    ingest_run_id: int, endpoint_name: str, url: str, payload_text: str
) -> None:
    """Insert one raw_blobs row.

    Form 4 payloads are XML, not JSON. We wrap them in a single-key JSON
    object (``{"xml": "<...>"}``) so the existing ``payload JSONB``
    column accepts them without a schema change.
    """
    import json as _json

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            RAW_BLOBS_INSERT,
            [ingest_run_id, endpoint_name, url, _json.dumps({"xml": payload_text})],
        )


def collect(
    *,
    http: HttpClient | None = None,
    backfill: bool = False,
    limit: int = DEFAULT_LIMIT,
) -> int:
    """Fetch uncached Form 4 XMLs and return the meta.ingest_runs id."""
    targets = select_uncached_form4s(limit=None if backfill else limit)
    user_agent = resolve_user_agent()

    owns_http = http is None
    if http is None:
        http = HttpClient(SOURCE_NAME, rate_limit=DEFAULT_RATE_LIMIT, user_agent=user_agent)

    failures: list[dict[str, str]] = []
    try:
        with db.ingest_run(
            SOURCE_NAME,
            endpoint=COLLECT_FORM4_ENDPOINT_LABEL,
            metadata={
                "mode": "backfill" if backfill else "incremental",
                "limit": None if backfill else limit,
                "candidate_count": len(targets),
            },
        ) as run:
            written = 0
            for index, target in enumerate(targets, start=1):
                if _fetch_one(target, http, run.id, failures):
                    written += 1
                if index % 25 == 0:
                    LOGGER.info(
                        "Form 4 collect progress: %s/%s (cached=%s)",
                        index,
                        len(targets),
                        written,
                    )
            run.add_rows(written)
            # Soft-failure mode: a 404 on a single Form 4 (e.g. SEC
            # redacted an old one) shouldn't fail the whole run and
            # block the rest. Log + record partials, return success.
            if failures:
                _record_partial(run.id, failures)
            return run.id
    finally:
        if owns_http:
            http.close()


def _fetch_one(
    target: Form4Target,
    http: HttpClient,
    ingest_run_id: int,
    failures: list[dict[str, str]],
) -> bool:
    """Fetch one Form 4 XML and store it. Returns True on success."""
    url = build_form4_xml_url(target.cik, target.accession_number, target.primary_document)
    endpoint_name = f"{FORM4_BLOB_PREFIX}{target.accession_number}"
    try:
        response = http.get(url)
        response.raise_for_status()
        text = response.text
    except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
        LOGGER.warning(
            "Form 4 fetch failed for %s (CIK %s filed %s): %s",
            target.accession_number,
            target.cik,
            target.filed_at,
            exc,
        )
        failures.append({"name": endpoint_name, "url": url, "error": str(exc)})
        return False
    _store_blob(ingest_run_id, endpoint_name, url, text)
    return True


def _record_partial(ingest_run_id: int, partial: list[dict[str, str]]) -> None:
    """Stash per-filing partial-failure metadata on the ingest_runs row."""
    import json as _json

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE meta.ingest_runs SET metadata = "
            "COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('partial_endpoints', %s::jsonb) "
            "WHERE id = %s",
            [_json.dumps(partial), ingest_run_id],
        )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect SEC Form 4 XML documents for filings already in sec.filings."
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Fetch every uncached Form 4 (no limit). Daily cron uses incremental mode.",
    )
    parser.add_argument(
        "--limit",
        type=positive_int,
        default=DEFAULT_LIMIT,
        help="Cap on filings per incremental run (ignored with --backfill).",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    import json as _json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv or sys.argv[1:])
    run_id = collect(backfill=args.backfill, limit=args.limit)
    if args.json:
        print(
            _json.dumps(
                {
                    "ingest_run_id": run_id,
                    "source": SOURCE_NAME,
                    "endpoint": COLLECT_FORM4_ENDPOINT_LABEL,
                    "mode": "backfill" if args.backfill else "incremental",
                }
            )
        )
    else:
        print(f"SEC Form 4 collector wrote ingest_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
