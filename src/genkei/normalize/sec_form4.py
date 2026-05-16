"""SEC Form 4 normalizer (B-079).

Reads ``form4_<accession>`` raw blobs landed by
``genkei.ingest.sec_form4``, parses each XML ``<ownershipDocument>``,
and upserts:

  - ``sec.insiders``           — one row per unique reporting-owner CIK
  - ``sec.form4_transactions`` — one row per transaction inside a Form 4

A Form 4 can carry both non-derivative transactions (open-market
buys/sells) and derivative transactions (option exercises, etc.). We
normalize both into the same wide table with ``is_derivative`` as the
discriminator — querying "all insider activity for AAPL since X" is the
common pattern and a single table keeps that query clean.

Idempotency: PK on ``(accession_number, transaction_idx)`` plus
``ON CONFLICT DO UPDATE`` means re-runs against the same blobs land the
same rows.

Default mode processes *every* ``form4_*`` blob whose accession isn't
yet represented in ``sec.form4_transactions`` — naturally catches both
fresh blobs from the latest collect and any older blobs that never got
normalized. ``--source-run-id`` scopes to one collect run.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from xml.etree import ElementTree as ET

from genkei.common import db

SOURCE_NAME = "sec"
NORMALIZE_FORM4_ENDPOINT_LABEL = "normalize_form4"
FORM4_BLOB_PREFIX = "form4_"
LOGGER = logging.getLogger(__name__)

JsonObject = dict[str, Any]


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------


def _text(elem: ET.Element | None, path: str) -> str | None:
    """``elem.findtext(path)`` with strip + empty-string-to-None."""
    if elem is None:
        return None
    val = elem.findtext(path)
    if val is None:
        return None
    val = val.strip()
    return val or None


def _value(elem: ET.Element | None, path: str) -> str | None:
    """Form 4 wraps most fields in a ``<value>...</value>`` child."""
    return _text(elem, f"{path}/value")


def _bool(value: str | None) -> bool | None:
    if value is None:
        return None
    v = value.strip().lower()
    if v in {"1", "true", "yes", "y"}:
        return True
    if v in {"0", "false", "no", "n"}:
        return False
    return None


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


def _normalize_cik(raw: str | None) -> str | None:
    """Zero-pad to 10 chars to match sec.companies / sec.insiders."""
    if raw is None:
        return None
    raw = raw.strip()
    if not raw.isdigit():
        return None
    return raw.zfill(10)


# ---------------------------------------------------------------------------
# Per-filing parser
# ---------------------------------------------------------------------------


def parse_form4_xml(
    xml_text: str,
    *,
    accession_number: str,
    source_endpoint: str,
    ingest_run_id: int,
    fetched_at: datetime,
) -> tuple[list[JsonObject], list[JsonObject]]:
    """Parse one Form 4 XML into ``(insider_rows, transaction_rows)``.

    Empty filings (Form 4 with no transactions, just a holdings update)
    return ``([insider_rows], [])`` so the insider row still lands.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        LOGGER.warning("Form 4 XML parse failed for %s: %s", accession_number, exc)
        return [], []

    issuer = root.find("issuer")
    issuer_cik = _normalize_cik(_text(issuer, "issuerCik"))
    if issuer_cik is None:
        LOGGER.warning("Form 4 %s missing issuerCik; skipping", accession_number)
        return [], []
    period_of_report = _date(root.findtext("periodOfReport"))

    # A Form 4 can have multiple reporting owners (rare but real:
    # joint-filing partnerships, family trusts). Walk all of them.
    reporters: list[dict[str, Any]] = []
    for owner in root.findall("reportingOwner"):
        rid = owner.find("reportingOwnerId")
        cik = _normalize_cik(_text(rid, "rptOwnerCik"))
        name = _text(rid, "rptOwnerName")
        if cik is None or name is None:
            continue
        rel = owner.find("reportingOwnerRelationship")
        reporters.append(
            {
                "reporter_cik": cik,
                "reporter_name": name,
                "is_director": _bool(_text(rel, "isDirector")),
                "is_officer": _bool(_text(rel, "isOfficer")),
                "is_ten_percent_owner": _bool(_text(rel, "isTenPercentOwner")),
                "is_other": _bool(_text(rel, "isOther")),
                "officer_title": _text(rel, "officerTitle"),
                "other_text": _text(rel, "otherText"),
            }
        )

    if not reporters:
        LOGGER.warning("Form 4 %s had no parseable reportingOwner; skipping", accession_number)
        return [], []

    insider_rows = [
        {
            "reporter_cik": r["reporter_cik"],
            "reporter_name": r["reporter_name"],
            "source_endpoint": source_endpoint,
            "ingest_run_id": ingest_run_id,
        }
        for r in reporters
    ]

    transaction_rows: list[JsonObject] = []
    transaction_idx = 0
    for reporter in reporters:
        # Non-derivative (open-market) transactions
        for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
            row = _build_transaction_row(
                tx,
                is_derivative=False,
                accession_number=accession_number,
                transaction_idx=transaction_idx,
                issuer_cik=issuer_cik,
                reporter=reporter,
                period_of_report=period_of_report,
                source_endpoint=source_endpoint,
                ingest_run_id=ingest_run_id,
                fetched_at=fetched_at,
            )
            if row is not None:
                transaction_rows.append(row)
                transaction_idx += 1
        # Derivative (option / warrant / convertible) transactions
        for tx in root.findall("derivativeTable/derivativeTransaction"):
            row = _build_transaction_row(
                tx,
                is_derivative=True,
                accession_number=accession_number,
                transaction_idx=transaction_idx,
                issuer_cik=issuer_cik,
                reporter=reporter,
                period_of_report=period_of_report,
                source_endpoint=source_endpoint,
                ingest_run_id=ingest_run_id,
                fetched_at=fetched_at,
            )
            if row is not None:
                transaction_rows.append(row)
                transaction_idx += 1

    return insider_rows, transaction_rows


def _build_transaction_row(
    tx: ET.Element,
    *,
    is_derivative: bool,
    accession_number: str,
    transaction_idx: int,
    issuer_cik: str,
    reporter: dict[str, Any],
    period_of_report: date | None,
    source_endpoint: str,
    ingest_run_id: int,
    fetched_at: datetime,
) -> JsonObject | None:
    txn_date = _date(_value(tx, "transactionDate"))
    if txn_date is None:
        # transaction_date is NOT NULL in the schema; skip transactions
        # without a parseable date rather than crash the whole filing.
        return None
    amounts = tx.find("transactionAmounts")
    coding = tx.find("transactionCoding")
    nature = tx.find("ownershipNature")
    post = tx.find("postTransactionAmounts")

    row: JsonObject = {
        "accession_number": accession_number,
        "transaction_idx": transaction_idx,
        "issuer_cik": issuer_cik,
        "reporter_cik": reporter["reporter_cik"],
        "is_director": reporter["is_director"],
        "is_officer": reporter["is_officer"],
        "is_ten_percent_owner": reporter["is_ten_percent_owner"],
        "is_other": reporter["is_other"],
        "officer_title": reporter["officer_title"],
        "other_text": reporter["other_text"],
        "period_of_report": period_of_report,
        "transaction_date": txn_date,
        "transaction_code": _text(coding, "transactionCode"),
        "acquired_disposed": _value(amounts, "transactionAcquiredDisposedCode"),
        "security_title": _value(tx, "securityTitle"),
        "is_derivative": is_derivative,
        "shares": _decimal(_value(amounts, "transactionShares")),
        "price_usd": _decimal(_value(amounts, "transactionPricePerShare")),
        "post_transaction_shares": _decimal(_value(post, "sharesOwnedFollowingTransaction")),
        "ownership_type": _value(nature, "directOrIndirectOwnership"),
        # Derivative-only fields (None on non-derivative rows)
        "underlying_security_title": None,
        "underlying_shares": None,
        "conversion_or_exercise_price": None,
        "exercise_date": None,
        "expiration_date": None,
        "source_endpoint": source_endpoint,
        "fetched_at": fetched_at,
        "ingest_run_id": ingest_run_id,
    }
    if is_derivative:
        underlying = tx.find("underlyingSecurity")
        row["underlying_security_title"] = _value(underlying, "underlyingSecurityTitle")
        row["underlying_shares"] = _decimal(_value(underlying, "underlyingSecurityShares"))
        row["conversion_or_exercise_price"] = _decimal(_value(tx, "conversionOrExercisePrice"))
        row["exercise_date"] = _date(_value(tx, "exerciseDate"))
        row["expiration_date"] = _date(_value(tx, "expirationDate"))
    return row


# ---------------------------------------------------------------------------
# Run orchestration
# ---------------------------------------------------------------------------


def fetch_unnormalized_form4_blobs(
    *, source_run_id: int | None = None
) -> list[tuple[str, str, str, datetime]]:
    """Return ``[(accession, url, xml_text, fetched_at), ...]`` to process.

    Default: every ``form4_*`` blob whose accession isn't yet in
    ``sec.form4_transactions``. With ``source_run_id``: every
    ``form4_*`` blob from that one collect run regardless of
    normalization state (so the user can force-replay).
    """
    if source_run_id is None:
        sql = """
            SELECT r.endpoint_name, r.url, r.payload, r.fetched_at
            FROM meta.raw_blobs r
            WHERE r.endpoint_name LIKE %s
              AND NOT EXISTS (
                  SELECT 1 FROM sec.form4_transactions t
                  WHERE t.accession_number = substr(r.endpoint_name, %s)
              )
            ORDER BY r.fetched_at DESC
        """
        like = f"{FORM4_BLOB_PREFIX}%"
        # substr is 1-indexed in Postgres; prefix is e.g. "form4_" (6 chars)
        prefix_len_plus_one = len(FORM4_BLOB_PREFIX) + 1
        params: list[Any] = [like, prefix_len_plus_one]
    else:
        sql = """
            SELECT endpoint_name, url, payload, fetched_at
            FROM meta.raw_blobs
            WHERE ingest_run_id = %s AND endpoint_name LIKE %s
            ORDER BY fetched_at DESC
        """
        params = [source_run_id, f"{FORM4_BLOB_PREFIX}%"]

    out: list[tuple[str, str, str, datetime]] = []
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        for endpoint_name, url, payload, fetched_at in cur.fetchall():
            accession = endpoint_name[len(FORM4_BLOB_PREFIX) :]
            xml_text = _payload_to_xml(payload)
            if xml_text is None:
                continue
            out.append((accession, url, xml_text, fetched_at))
    return out


def _payload_to_xml(payload: Any) -> str | None:
    """Form 4 blobs are stored as ``{"xml": "<...>"}``; extract the string."""
    if isinstance(payload, dict):
        xml = payload.get("xml")
        if isinstance(xml, str):
            return xml
    if isinstance(payload, str):
        # Defensive — some pipelines might land plain strings.
        return payload
    return None


def normalize(*, source_run_id: int | None = None) -> tuple[int, int]:
    """Run the Form 4 normalizer once. Returns ``(normalizer_run_id, blobs_processed)``."""
    blobs = fetch_unnormalized_form4_blobs(source_run_id=source_run_id)

    with db.ingest_run(
        SOURCE_NAME,
        endpoint=NORMALIZE_FORM4_ENDPOINT_LABEL,
        metadata={
            "source_run_id": source_run_id,
            "blob_count": len(blobs),
        },
    ) as run:
        if not blobs:
            run.add_rows(0)
            return run.id, 0

        # Insider rows must land first (transaction FK references them).
        # Dedupe across the whole batch on reporter_cik so we don't
        # upsert the same reporter ten times in one batch.
        insider_rows_by_cik: dict[str, JsonObject] = {}
        transaction_rows: list[JsonObject] = []

        for accession, url, xml_text, fetched_at in blobs:
            insiders, transactions = parse_form4_xml(
                xml_text,
                accession_number=accession,
                source_endpoint=url,
                ingest_run_id=run.id,
                fetched_at=fetched_at,
            )
            for ins in insiders:
                # First-write wins for the dedupe; row count is what we
                # care about, not which specific filing supplied the
                # name (insiders never change CIK; names get casing
                # variations which is fine to settle on whichever).
                insider_rows_by_cik.setdefault(ins["reporter_cik"], ins)
            transaction_rows.extend(transactions)

        insider_rows = list(insider_rows_by_cik.values())

        with db.connection() as conn:
            run.add_rows(
                db.bulk_upsert(
                    conn, "sec.insiders", insider_rows, conflict_keys=["reporter_cik"]
                )
            )
            run.add_rows(
                db.bulk_upsert(
                    conn,
                    "sec.form4_transactions",
                    transaction_rows,
                    conflict_keys=["accession_number", "transaction_idx"],
                )
            )

        return run.id, len(blobs)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize SEC Form 4 raw blobs into sec.form4_transactions + sec.insiders."
    )
    parser.add_argument(
        "--source-run-id",
        type=int,
        default=None,
        help="Force-process every form4 blob from this collect run id "
        "(default: pick up every unnormalized form4 blob).",
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
                    "endpoint": NORMALIZE_FORM4_ENDPOINT_LABEL,
                    "blobs_processed": processed,
                }
            )
        )
    else:
        print(
            f"SEC Form 4 normalizer wrote ingest_run_id={run_id} "
            f"(processed {processed} blob(s))"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
