"""SEC 13F normalizer (B-080).

Reads two blob shapes landed by ``genkei.ingest.sec_form13f``:

  - ``submissions_filer_<cik>``                    → filer + filing index
  - ``submissions_filer_history_<cik>_<file>``     → older filings (no top-level metadata)
  - ``form13f_<accession>``                        → information-table XML payload

Upserts:
  - ``sec.filers``                — one row per reporting manager CIK
  - ``sec.form13f_filings``       — one row per 13F filing
  - ``sec.form13f_holdings``      — one row per *position* inside a 13F-HR

The canonical 13F gotcha lives here: the ``<value>`` element on every
infoTable is reported in **thousands of dollars**. We multiply by 1000
at parse time so ``sec.form13f_holdings.value_usd`` always carries
dollars. Tests pin this — if SEC's wire format ever changes, the
multiplication needs to come out and a column rename should follow.

13F-NT (notice-only) filings carry the filing-index row but no
holdings — the filer is reporting via an affiliated manager's
aggregate 13F-HR. Linking an NT back to the HR is via shared
``period_of_report`` between filers; the holdings table stays the
authoritative count.

Idempotent: every upsert is keyed on the table's natural PK and
``sec.form13f_normalized_filings`` blocks re-fetching XML for already-
processed accessions.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from genkei.common import db

SOURCE_NAME = "sec"
NORMALIZE_FORM13F_ENDPOINT_LABEL = "normalize_form13f"
SUBMISSIONS_FILER_BLOB_PREFIX = "submissions_filer_"
SUBMISSIONS_FILER_HISTORY_BLOB_PREFIX = "submissions_filer_history_"
FORM13F_BLOB_PREFIX = "form13f_"

# Multiplier baked into the XML wire format. Every <value> in a 13F
# information table is in thousands. The test pins the value 42 in
# the XML → 42000 in the table.
THOUSANDS_MULTIPLIER = Decimal("1000")

JsonObject = dict[str, Any]
LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def parse_sec_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse_sec_datetime(value: Any) -> datetime | None:
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


def derive_report_type(form_type: str) -> str | None:
    """13F form_type → report_type label.

    13F-HR / 13F-HR/A → HOLDINGS REPORT
    13F-NT / 13F-NT/A → NOTICE
    13F-CTR / 13F-CTR/A → COMBINATION
    """
    upper = form_type.upper()
    if upper.startswith("13F-HR"):
        return "HOLDINGS REPORT"
    if upper.startswith("13F-NT"):
        return "NOTICE"
    if upper.startswith("13F-CTR"):
        return "COMBINATION"
    return None


# ---------------------------------------------------------------------------
# Submissions / filings normalizers
# ---------------------------------------------------------------------------


def normalize_filer(
    payload: Any,
    *,
    filer_cik: str,
    source_endpoint: str,
    ingest_run_id: int,
    fetched_at: datetime,
) -> JsonObject | None:
    """Top-level submissions payload → sec.filers row."""
    if not isinstance(payload, dict):
        return None
    name = payload.get("name")
    if not isinstance(name, str) or not name:
        return None
    return {
        "filer_cik": filer_cik,
        "name": name,
        "source_endpoint": source_endpoint,
        "last_seen_at": fetched_at,
        "ingest_run_id": ingest_run_id,
    }


def normalize_form13f_filings(
    payload: Any,
    *,
    filer_cik: str,
    source_endpoint: str,
    ingest_run_id: int,
    fetched_at: datetime,
    is_history_page: bool = False,
) -> list[JsonObject]:
    """Submissions payload → list of sec.form13f_filings rows.

    Filters to ``form_type`` starting with ``13F``. The parallel-array
    shape under ``filings.recent`` (or root-level on history pages) is
    the same shape the issuer-side SEC normalizer parses for sec.filings.
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

    rows: list[JsonObject] = []
    seen: set[str] = set()
    for i in range(n):
        accn = accessions[i]
        form = forms[i]
        if not isinstance(accn, str) or not accn or accn in seen:
            continue
        if not isinstance(form, str) or not form.startswith("13F"):
            continue
        filed = parse_sec_date(filing_dates[i])
        if filed is None:
            continue
        seen.add(accn)
        rows.append(
            {
                "accession_number": accn,
                "filer_cik": filer_cik,
                "form_type": form,
                "filed_at": filed,
                "accepted_at": parse_sec_datetime(accepted[i]),
                "period_of_report": parse_sec_date(report_dates[i]),
                "report_type": derive_report_type(form),
                "primary_document": _stringify(primary_docs[i]),
                "primary_doc_description": _stringify(primary_descs[i]),
                # other_managers is populated by the XML cover page when
                # we fetch it; left NULL here since the submissions
                # parallel-arrays don't expose it. Future enhancement
                # tracked separately if cross-manager linkage gets used.
                "other_managers": None,
                "source_endpoint": source_endpoint,
                "fetched_at": fetched_at,
                "ingest_run_id": ingest_run_id,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Information-table XML parser
# ---------------------------------------------------------------------------


def _local(tag: str) -> str:
    """ElementTree returns ``{namespace}localname``; strip the namespace."""
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _find_child(elem: ElementTree.Element, name: str) -> ElementTree.Element | None:
    """Namespace-agnostic single-child lookup by local-name."""
    for child in elem:
        if _local(child.tag) == name:
            return child
    return None


def _children(elem: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    """Namespace-agnostic multi-child lookup by local-name."""
    return [child for child in elem if _local(child.tag) == name]


def _child_text(elem: ElementTree.Element | None, name: str) -> str | None:
    if elem is None:
        return None
    child = _find_child(elem, name)
    if child is None or child.text is None:
        return None
    text = child.text.strip()
    return text or None


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


def parse_form13f_xml(
    xml_text: str,
    *,
    accession_number: str,
    filer_cik: str,
    period_of_report: date | None,
    source_endpoint: str,
    ingest_run_id: int,
    fetched_at: datetime,
) -> list[JsonObject]:
    """Parse one information-table XML into sec.form13f_holdings rows.

    Returns rows ready for ``bulk_upsert``. Skips infoTable entries
    without a CUSIP (the SEC schema marks it required but defensive
    against malformed filings — drop the row, log the gap).

    The ``<value>`` field's $1000s convention is applied here:
    ``value_usd = raw_value * 1000``.
    """
    try:
        root = ElementTree.fromstring(xml_text)
    except (ElementTree.ParseError, DefusedXmlException) as exc:
        LOGGER.warning("Form 13F XML parse failed for %s: %s", accession_number, exc)
        return []

    info_tables = _children(root, "infoTable")
    rows: list[JsonObject] = []
    for idx, info in enumerate(info_tables):
        cusip = _child_text(info, "cusip")
        if cusip is None:
            LOGGER.warning(
                "Form 13F %s infoTable[%s] missing CUSIP; skipping",
                accession_number,
                idx,
            )
            continue

        raw_value = _decimal(_child_text(info, "value"))
        value_usd = raw_value * THOUSANDS_MULTIPLIER if raw_value is not None else None

        shr_prn = _find_child(info, "shrsOrPrnAmt")
        voting = _find_child(info, "votingAuthority")

        rows.append(
            {
                "accession_number": accession_number,
                "holding_idx": idx,
                "filer_cik": filer_cik,
                "period_of_report": period_of_report,
                "cusip": cusip,
                "issuer_name": _child_text(info, "nameOfIssuer"),
                "class_title": _child_text(info, "titleOfClass"),
                "value_usd": value_usd,
                "shares_or_principal": _decimal(_child_text(shr_prn, "sshPrnamt")),
                "shares_or_principal_type": _child_text(shr_prn, "sshPrnamtType"),
                "put_call": _child_text(info, "putCall"),
                "investment_discretion": _child_text(info, "investmentDiscretion"),
                "other_managers": _child_text(info, "otherManager"),
                "voting_authority_sole": _decimal(_child_text(voting, "Sole")),
                "voting_authority_shared": _decimal(_child_text(voting, "Shared")),
                "voting_authority_none": _decimal(_child_text(voting, "None")),
                "source_endpoint": source_endpoint,
                "fetched_at": fetched_at,
                "ingest_run_id": ingest_run_id,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Run orchestration
# ---------------------------------------------------------------------------


def fetch_unnormalized_form13f_blobs(
    *, source_run_id: int | None = None
) -> list[tuple[str, str, str, datetime]]:
    """Return ``[(accession, url, xml_text, fetched_at), ...]`` to process.

    Default: every ``form13f_*`` blob whose accession isn't yet in
    ``sec.form13f_normalized_filings``. With ``source_run_id``: every
    ``form13f_*`` blob from that one collect run regardless of state
    (force-replay).
    """
    if source_run_id is None:
        sql = """
            SELECT r.endpoint_name, r.url, r.payload, r.fetched_at
            FROM meta.raw_blobs r
            WHERE r.endpoint_name LIKE %s
              AND NOT EXISTS (
                  SELECT 1 FROM sec.form13f_normalized_filings f
                  WHERE f.accession_number = substr(r.endpoint_name, %s)
              )
            ORDER BY r.fetched_at DESC
        """
        like = f"{FORM13F_BLOB_PREFIX}%"
        prefix_len_plus_one = len(FORM13F_BLOB_PREFIX) + 1
        params: list[Any] = [like, prefix_len_plus_one]
    else:
        sql = """
            SELECT endpoint_name, url, payload, fetched_at
            FROM meta.raw_blobs
            WHERE ingest_run_id = %s AND endpoint_name LIKE %s
            ORDER BY fetched_at DESC
        """
        params = [source_run_id, f"{FORM13F_BLOB_PREFIX}%"]

    out: list[tuple[str, str, str, datetime]] = []
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        for endpoint_name, url, payload, fetched_at in cur.fetchall():
            accession = endpoint_name[len(FORM13F_BLOB_PREFIX) :]
            xml_text = _payload_to_xml(payload)
            if xml_text is None:
                continue
            out.append((accession, url, xml_text, fetched_at))
    return out


def _payload_to_xml(payload: Any) -> str | None:
    """form13f_* blobs are ``{"xml": "...", ...}``; extract the XML string."""
    if isinstance(payload, dict):
        xml = payload.get("xml")
        if isinstance(xml, str):
            return xml
    if isinstance(payload, str):
        return payload
    return None


def fetch_submissions_filer_blobs(
    *, source_run_id: int | None = None
) -> list[tuple[str, str, str, Any, datetime]]:
    """Return ``[(filer_cik, endpoint_name, url, payload, fetched_at), ...]``.

    Always pulls every submissions-filer blob for the run when
    ``source_run_id`` is given (the collector path). Otherwise pulls
    the most-recent blob per filer_cik so a re-run picks up the latest
    snapshot of each filer's filing index.
    """
    if source_run_id is None:
        sql = """
            SELECT DISTINCT ON (r.endpoint_name)
                r.endpoint_name, r.url, r.payload, r.fetched_at
            FROM meta.raw_blobs r
            WHERE r.endpoint_name LIKE %s OR r.endpoint_name LIKE %s
            ORDER BY r.endpoint_name, r.fetched_at DESC
        """
        params: list[Any] = [
            f"{SUBMISSIONS_FILER_BLOB_PREFIX}%",
            f"{SUBMISSIONS_FILER_HISTORY_BLOB_PREFIX}%",
        ]
    else:
        sql = """
            SELECT endpoint_name, url, payload, fetched_at
            FROM meta.raw_blobs
            WHERE ingest_run_id = %s
              AND (endpoint_name LIKE %s OR endpoint_name LIKE %s)
        """
        params = [
            source_run_id,
            f"{SUBMISSIONS_FILER_BLOB_PREFIX}%",
            f"{SUBMISSIONS_FILER_HISTORY_BLOB_PREFIX}%",
        ]

    out: list[tuple[str, str, str, Any, datetime]] = []
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        for endpoint_name, url, payload, fetched_at in cur.fetchall():
            filer_cik = _extract_filer_cik(endpoint_name)
            if filer_cik is None:
                continue
            out.append((filer_cik, endpoint_name, url, payload, fetched_at))
    return out


def _extract_filer_cik(endpoint_name: str) -> str | None:
    """Pull the filer CIK out of a submissions blob endpoint_name."""
    if endpoint_name.startswith(SUBMISSIONS_FILER_HISTORY_BLOB_PREFIX):
        rest = endpoint_name[len(SUBMISSIONS_FILER_HISTORY_BLOB_PREFIX) :]
        return rest.split("_", 1)[0]
    if endpoint_name.startswith(SUBMISSIONS_FILER_BLOB_PREFIX):
        return endpoint_name[len(SUBMISSIONS_FILER_BLOB_PREFIX) :]
    return None


def _is_history_page(endpoint_name: str) -> bool:
    return endpoint_name.startswith(SUBMISSIONS_FILER_HISTORY_BLOB_PREFIX)


def _mark_normalized_filings(
    conn: Any, accessions: list[str], *, ingest_run_id: int
) -> None:
    """Mark accessions processed even when their XML produced zero holdings."""
    if not accessions:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO sec.form13f_normalized_filings (accession_number, ingest_run_id)
            VALUES (%s, %s)
            ON CONFLICT (accession_number) DO UPDATE SET
                normalized_at = now(),
                ingest_run_id = EXCLUDED.ingest_run_id
            """,
            [(accession, ingest_run_id) for accession in accessions],
        )


def _period_of_report_lookup(conn: Any, accessions: list[str]) -> dict[str, date | None]:
    """Pull period_of_report per accession from sec.form13f_filings."""
    if not accessions:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT accession_number, period_of_report "
            "FROM sec.form13f_filings WHERE accession_number = ANY(%s)",
            [accessions],
        )
        return {accn: pod for accn, pod in cur.fetchall()}


def normalize(*, source_run_id: int | None = None) -> tuple[int, int]:
    """Run the 13F normalizer once.

    Returns ``(normalizer_run_id, blobs_processed)`` where blobs_processed
    counts the info-table XMLs handled (the holdings-rows-bearing work).
    """
    with db.ingest_run(
        SOURCE_NAME,
        endpoint=NORMALIZE_FORM13F_ENDPOINT_LABEL,
        metadata={"source_run_id": source_run_id},
    ) as run:
        # Pass 1 — filer + filings rows from every submissions blob the
        # run touched (or every latest-per-filer blob in default mode).
        submissions_blobs = fetch_submissions_filer_blobs(source_run_id=source_run_id)
        filer_rows_by_cik: dict[str, JsonObject] = {}
        filing_rows: list[JsonObject] = []
        for filer_cik, endpoint_name, url, payload, fetched_at in submissions_blobs:
            if not _is_history_page(endpoint_name):
                filer_row = normalize_filer(
                    payload,
                    filer_cik=filer_cik,
                    source_endpoint=url,
                    ingest_run_id=run.id,
                    fetched_at=fetched_at,
                )
                if filer_row is not None:
                    filer_rows_by_cik.setdefault(filer_cik, filer_row)
            filing_rows.extend(
                normalize_form13f_filings(
                    payload,
                    filer_cik=filer_cik,
                    source_endpoint=url,
                    ingest_run_id=run.id,
                    fetched_at=fetched_at,
                    is_history_page=_is_history_page(endpoint_name),
                )
            )

        filer_rows = list(filer_rows_by_cik.values())

        # Pass 2 — info-table XML blobs → holdings rows.
        xml_blobs = fetch_unnormalized_form13f_blobs(source_run_id=source_run_id)

        with db.connection() as conn:
            # Filers must land before filings (FK).
            run.add_rows(
                db.bulk_upsert(
                    conn, "sec.filers", filer_rows, conflict_keys=["filer_cik"]
                )
            )
            # Filings must land before holdings + normalized markers (FK).
            run.add_rows(
                db.bulk_upsert(
                    conn,
                    "sec.form13f_filings",
                    filing_rows,
                    conflict_keys=["accession_number"],
                )
            )

            # Look up period_of_report for every XML blob's accession —
            # the holdings table denormalizes it for fast filer/period
            # queries, but the source of truth lives in form13f_filings.
            accessions = [accn for accn, _u, _x, _f in xml_blobs]
            period_by_accession = _period_of_report_lookup(conn, accessions)

            holdings_rows: list[JsonObject] = []
            normalized_accessions: list[str] = []
            for accession, url, xml_text, fetched_at in xml_blobs:
                period = period_by_accession.get(accession)
                if period is None:
                    # No matching filings row — typically a blob whose
                    # phase-A submissions never landed (collector failed
                    # partway). Skip rather than emit FK-violating rows.
                    LOGGER.warning(
                        "Form 13F %s has XML blob but no filings row; "
                        "skipping holdings parse",
                        accession,
                    )
                    continue
                rows = parse_form13f_xml(
                    xml_text,
                    accession_number=accession,
                    filer_cik=_filer_cik_for_accession(conn, accession),
                    period_of_report=period,
                    source_endpoint=url,
                    ingest_run_id=run.id,
                    fetched_at=fetched_at,
                )
                holdings_rows.extend(rows)
                # Mark the accession processed even when rows is empty —
                # otherwise the collector will keep re-fetching XMLs
                # that legitimately have no holdings (rare but possible).
                normalized_accessions.append(accession)

            run.add_rows(
                db.bulk_upsert(
                    conn,
                    "sec.form13f_holdings",
                    holdings_rows,
                    conflict_keys=["accession_number", "holding_idx"],
                )
            )
            _mark_normalized_filings(
                conn, normalized_accessions, ingest_run_id=run.id
            )

        return run.id, len(xml_blobs)


def _filer_cik_for_accession(conn: Any, accession: str) -> str:
    """Look up the filer_cik for an accession from sec.form13f_filings."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT filer_cik FROM sec.form13f_filings WHERE accession_number = %s",
            [accession],
        )
        row = cur.fetchone()
    if row is None:
        # Should not happen — the caller already filtered to accessions
        # with a filings row via _period_of_report_lookup. Defensive.
        raise SystemExit(f"no filings row for accession {accession}")
    return row[0]


def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    return str(value)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize SEC 13F raw blobs into sec.filers + "
            "sec.form13f_filings + sec.form13f_holdings."
        )
    )
    parser.add_argument(
        "--source-run-id",
        type=int,
        default=None,
        help="Force-process every 13F blob from this collect run id "
        "(default: pick up every unnormalized blob).",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv or sys.argv[1:])
    run_id, processed = normalize(source_run_id=args.source_run_id)
    if args.json:
        print(
            json.dumps(
                {
                    "ingest_run_id": run_id,
                    "source": SOURCE_NAME,
                    "endpoint": NORMALIZE_FORM13F_ENDPOINT_LABEL,
                    "blobs_processed": processed,
                }
            )
        )
    else:
        print(
            f"SEC 13F normalizer wrote ingest_run_id={run_id} "
            f"(processed {processed} XML blob(s))"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
