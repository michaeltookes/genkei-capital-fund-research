"""SEC EDGAR normalizer — reads meta.raw_blobs, upserts sec.* (B-027).

A normalizer run is itself a row in ``meta.ingest_runs`` with
``endpoint='normalize'`` and ``metadata.source_run_id`` pointing at the
collector run whose blobs were processed. Re-running is idempotent:
every write is an ``ON CONFLICT DO UPDATE`` keyed on the table's
natural PK.

Three blob shapes dispatched by endpoint_name prefix:

  - ``submissions_<cik>``                  → company metadata + recent filings
  - ``submissions_history_<cik>_<file>``   → older-history filings (no metadata)
  - ``companyfacts_<cik>``                 → XBRL facts (taxonomy → concept → unit → values)

XBRL parsing notes:
  - Concept names are namespaced (us-gaap:Revenues). We split into
    ``taxonomy`` + ``concept`` columns to allow per-taxonomy queries.
  - Units encode the value's measurement (USD, shares, USD/shares,
    pure). Same concept can appear under multiple units; PK includes
    unit so all variants land.
  - The same fact (concept × period) often appears in multiple filings
    (10-Q first, then 10-K confirms). PK includes accession_number so
    both rows land — downstream queries can filter to the most recent
    filing per (concept, period) when desired.
  - Period overlap: facts can be instant (period_start = period_end)
    or duration (period_start ≠ period_end). We store both ends.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from genkei.common import db

SOURCE_NAME = "sec"
NORMALIZE_ENDPOINT_LABEL = "normalize"
COLLECT_ENDPOINT_LABEL = "collect"
SUBMISSIONS_BLOB_PREFIX = "submissions_"
SUBMISSIONS_HISTORY_BLOB_PREFIX = "submissions_history_"
COMPANYFACTS_BLOB_PREFIX = "companyfacts_"
RawBlob = tuple[str, Any, datetime]
JsonObject = dict[str, Any]
LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def parse_sec_date(value: Any) -> date | None:
    """Parse a YYYY-MM-DD SEC date into a ``date``."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse_sec_datetime(value: Any) -> datetime | None:
    """Parse a SEC ISO-8601 timestamp (e.g. ``"2024-05-09T16:30:00.000Z"``)."""
    if not isinstance(value, str) or not value:
        return None
    cleaned = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def split_concept(taxonomy: str, concept: str) -> tuple[str, str]:
    """Return (taxonomy, concept) ensuring both are non-empty."""
    return taxonomy, concept


# ---------------------------------------------------------------------------
# Per-table normalizers
# ---------------------------------------------------------------------------


def normalize_company(
    payload: Any,
    *,
    cik: str,
    source_endpoint: str,
    ingest_run_id: int,
    fetched_at: datetime,
) -> JsonObject | None:
    """Map a /submissions payload's top-level metadata to a sec.companies row."""
    if not isinstance(payload, dict):
        return None
    name = payload.get("name")
    if not isinstance(name, str) or not name:
        return None
    tickers = payload.get("tickers")
    primary_ticker = (
        tickers[0]
        if isinstance(tickers, list) and tickers and isinstance(tickers[0], str)
        else None
    )
    raw_exchanges = payload.get("exchanges")
    exchanges = [str(x) for x in raw_exchanges] if isinstance(raw_exchanges, list) else None
    former_names = payload.get("formerNames")
    return {
        "cik": cik,
        "ticker": primary_ticker,
        "name": name,
        "sic": _stringify(payload.get("sic")),
        "sic_description": _stringify(payload.get("sicDescription")),
        "ein": _stringify(payload.get("ein")),
        "entity_type": _stringify(payload.get("entityType")),
        "fiscal_year_end": _stringify(payload.get("fiscalYearEnd")),
        "exchanges": exchanges,
        "former_names": _maybe_jsonable(former_names),
        "source_endpoint": source_endpoint,
        "fetched_at": fetched_at,
        "ingest_run_id": ingest_run_id,
    }


def normalize_filings(
    payload: Any,
    *,
    cik: str,
    source_endpoint: str,
    ingest_run_id: int,
    fetched_at: datetime,
    is_history_page: bool = False,
) -> list[JsonObject]:
    """Map a SEC submissions payload to ``sec.filings`` row dicts.

    Submissions JSON wraps the recent-filings index as a *parallel-array*
    object: every field is its own list, all of equal length. History
    pages have the same shape but at the *root* of the payload rather
    than under ``filings.recent``.
    """
    if not isinstance(payload, dict):
        return []
    if is_history_page:
        recent = payload
    else:
        filings = payload.get("filings")
        if not isinstance(filings, dict):
            return []
        recent = filings.get("recent")
        if not isinstance(recent, dict):
            return []

    accessions = recent.get("accessionNumber")
    if not isinstance(accessions, list) or not accessions:
        return []
    n = len(accessions)

    def col(name: str) -> list[Any]:
        v = recent.get(name)
        if isinstance(v, list) and len(v) == n:
            return v
        return [None] * n

    forms = col("form")
    filing_dates = col("filingDate")
    accepted = col("acceptanceDateTime")
    report_dates = col("reportDate")
    primary_docs = col("primaryDocument")
    primary_descs = col("primaryDocDescription")
    file_numbers = col("fileNumber")
    film_numbers = col("filmNumber")
    items_col = col("items")
    sizes = col("size")
    is_xbrl = col("isXBRL")
    is_inline_xbrl = col("isInlineXBRL")

    rows: list[JsonObject] = []
    seen_accns: set[str] = set()
    for i in range(n):
        accn = accessions[i]
        if not isinstance(accn, str) or not accn or accn in seen_accns:
            continue
        seen_accns.add(accn)
        form = forms[i]
        filed = parse_sec_date(filing_dates[i])
        if not isinstance(form, str) or not form or filed is None:
            continue
        rows.append(
            {
                "accession_number": accn,
                "cik": cik,
                "form_type": form,
                "filed_at": filed,
                "accepted_at": parse_sec_datetime(accepted[i]),
                "report_date": parse_sec_date(report_dates[i]),
                "primary_document": _stringify(primary_docs[i]),
                "primary_doc_description": _stringify(primary_descs[i]),
                "file_number": _stringify(file_numbers[i]),
                "film_number": _stringify(film_numbers[i]),
                "items": _stringify(items_col[i]),
                "size_bytes": _maybe_int(sizes[i]),
                "is_xbrl": _maybe_bool(is_xbrl[i]),
                "is_inline_xbrl": _maybe_bool(is_inline_xbrl[i]),
                "source_endpoint": source_endpoint,
                "fetched_at": fetched_at,
                "ingest_run_id": ingest_run_id,
            }
        )
    return rows


def normalize_facts(
    payload: Any,
    *,
    cik: str,
    source_endpoint: str,
    ingest_run_id: int,
    fetched_at: datetime,
) -> list[JsonObject]:
    """Map an XBRL companyfacts payload to ``sec.facts`` row dicts.

    Walks ``facts.{taxonomy}.{concept}.units.{unit}`` and emits one row
    per (taxonomy, concept, unit, period_start, period_end, accession_number).
    """
    if not isinstance(payload, dict):
        return []
    facts = payload.get("facts")
    if not isinstance(facts, dict):
        return []
    rows: list[JsonObject] = []
    seen: set[tuple[str, str, date, date, str]] = set()
    for taxonomy, concepts in facts.items():
        if not isinstance(concepts, dict):
            continue
        for concept_name, concept_data in concepts.items():
            if not isinstance(concept_data, dict):
                continue
            units = concept_data.get("units")
            if not isinstance(units, dict):
                continue
            for unit_name, fact_array in units.items():
                if not isinstance(fact_array, list):
                    continue
                for fact in fact_array:
                    if not isinstance(fact, dict):
                        continue
                    period_end = parse_sec_date(fact.get("end"))
                    period_start = parse_sec_date(fact.get("start")) or period_end
                    accn = fact.get("accn")
                    if period_end is None or not isinstance(accn, str) or not accn:
                        continue
                    full_concept = f"{taxonomy}:{concept_name}"
                    key = (full_concept, str(unit_name), period_start, period_end, accn)
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append(
                        {
                            "cik": cik,
                            "taxonomy": str(taxonomy),
                            "concept": full_concept,
                            "unit": str(unit_name),
                            "period_start": period_start,
                            "period_end": period_end,
                            "value": _as_numeric(fact.get("val")),
                            "accession_number": accn,
                            "form_type": _stringify(fact.get("form")),
                            "filed_at": parse_sec_date(fact.get("filed")),
                            "frame": _stringify(fact.get("frame")),
                            "fy": _maybe_int(fact.get("fy")),
                            "fp": _stringify(fact.get("fp")),
                            "source_endpoint": source_endpoint,
                            "fetched_at": fetched_at,
                            "ingest_run_id": ingest_run_id,
                        }
                    )
    return rows


# ---------------------------------------------------------------------------
# Run orchestration
# ---------------------------------------------------------------------------


def latest_collector_run_id() -> int:
    """Return the most recent successful SEC collector run id."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM meta.ingest_runs "
            "WHERE source = %s AND endpoint = %s AND status = 'success' "
            "ORDER BY started_at DESC LIMIT 1",
            [SOURCE_NAME, COLLECT_ENDPOINT_LABEL],
        )
        row = cur.fetchone()
    if row is None:
        raise SystemExit(
            "No successful SEC collector run found in meta.ingest_runs. "
            "Run `python -m genkei.ingest.sec` first."
        )
    return int(row[0])


def fetch_raw_blobs(source_run_id: int) -> dict[str, RawBlob]:
    """Return ``{endpoint_name: (url, payload, fetched_at)}`` for a run."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT endpoint_name, url, payload, fetched_at "
            "FROM meta.raw_blobs WHERE ingest_run_id = %s",
            [source_run_id],
        )
        rows = cur.fetchall()
    if not rows:
        raise SystemExit(f"No raw blobs found for ingest_run_id={source_run_id}.")
    return {name: (url, payload, fetched_at) for name, url, payload, fetched_at in rows}


def normalize(*, source_run_id: int | None = None) -> tuple[int, int]:
    """Run the SEC normalizer once and return ``(normalizer_run_id, source_run_id)``."""
    if source_run_id is None:
        source_run_id = latest_collector_run_id()
    blobs = fetch_raw_blobs(source_run_id)

    with db.ingest_run(
        SOURCE_NAME,
        endpoint=NORMALIZE_ENDPOINT_LABEL,
        metadata={"source_run_id": source_run_id},
    ) as run:
        company_rows: list[JsonObject] = []
        filing_rows: list[JsonObject] = []
        fact_blobs: list[tuple[str, str, Any, datetime]] = []

        for endpoint_name, (url, payload, fetched_at) in blobs.items():
            if endpoint_name.startswith(SUBMISSIONS_HISTORY_BLOB_PREFIX):
                # endpoint_name shape: submissions_history_<cik>_<file>.json
                rest = endpoint_name[len(SUBMISSIONS_HISTORY_BLOB_PREFIX) :]
                cik = rest.split("_", 1)[0]
                filing_rows.extend(
                    normalize_filings(
                        payload,
                        cik=cik,
                        source_endpoint=url,
                        ingest_run_id=run.id,
                        fetched_at=fetched_at,
                        is_history_page=True,
                    )
                )
            elif endpoint_name.startswith(SUBMISSIONS_BLOB_PREFIX):
                cik = endpoint_name[len(SUBMISSIONS_BLOB_PREFIX) :]
                company_row = normalize_company(
                    payload,
                    cik=cik,
                    source_endpoint=url,
                    ingest_run_id=run.id,
                    fetched_at=fetched_at,
                )
                if company_row is not None:
                    company_rows.append(company_row)
                filing_rows.extend(
                    normalize_filings(
                        payload,
                        cik=cik,
                        source_endpoint=url,
                        ingest_run_id=run.id,
                        fetched_at=fetched_at,
                    )
                )
            elif endpoint_name.startswith(COMPANYFACTS_BLOB_PREFIX):
                cik = endpoint_name[len(COMPANYFACTS_BLOB_PREFIX) :]
                fact_blobs.append((cik, url, payload, fetched_at))
            else:
                LOGGER.debug("SEC normalizer skipping unknown blob: %s", endpoint_name)

        # Companies must land before filings + facts (FK dependency).
        with db.connection() as conn:
            run.add_rows(db.bulk_upsert(conn, "sec.companies", company_rows, conflict_keys=["cik"]))
            run.add_rows(
                db.bulk_upsert(
                    conn,
                    "sec.filings",
                    filing_rows,
                    conflict_keys=["accession_number"],
                )
            )
            for cik, url, payload, fetched_at in fact_blobs:
                fact_rows = normalize_facts(
                    payload,
                    cik=cik,
                    source_endpoint=url,
                    ingest_run_id=run.id,
                    fetched_at=fetched_at,
                )
                run.add_rows(
                    db.bulk_upsert(
                        conn,
                        "sec.facts",
                        fact_rows,
                        conflict_keys=[
                            "cik",
                            "concept",
                            "unit",
                            "period_start",
                            "period_end",
                            "accession_number",
                        ],
                    )
                )

        return run.id, source_run_id


# ---------------------------------------------------------------------------
# Small coercion helpers
# ---------------------------------------------------------------------------


def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    return str(value)


def _maybe_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _maybe_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    # SEC sometimes serializes booleans as 0/1 ints.
    if isinstance(value, int):
        return bool(value)
    return None


def _as_numeric(value: Any) -> Decimal | None:
    """Coerce XBRL fact values to ``Decimal`` while preserving missingness."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _maybe_jsonable(value: Any) -> Any:
    """Pass through JSON-serializable values; drop non-serializable junk."""
    if value is None:
        return None
    if isinstance(value, (dict, list, str, int, float, bool)):
        return value
    return None


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize SEC EDGAR raw blobs into sec.* tables.")
    parser.add_argument(
        "--source-run-id",
        type=int,
        default=None,
        help="SEC collector ingest_run id. Default: latest success.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv or sys.argv[1:])
    run_id, resolved_source_run_id = normalize(source_run_id=args.source_run_id)
    if args.json:
        print(
            json.dumps(
                {
                    "ingest_run_id": run_id,
                    "source": SOURCE_NAME,
                    "endpoint": NORMALIZE_ENDPOINT_LABEL,
                    "source_run_id": resolved_source_run_id,
                }
            )
        )
    else:
        print(f"SEC normalizer wrote ingest_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
