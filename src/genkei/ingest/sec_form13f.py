"""SEC 13F collector (B-080).

13F-HR is the quarterly institutional-holdings report — every manager
exercising investment discretion over $100M+ in 13(f)-eligible
securities must file. This is the *positioning* counterpart to B-079's
Form 4 *flow* data: Form 4 tells you "insider X just sold," 13F tells
you "as of quarter-end Q, manager Y held N shares of CUSIP Z."

The collector runs in two phases inside one ``meta.ingest_runs`` row:

* **Phase A — submissions per filer.** For each filer in
  ``config/watchlists.yml::filers``, fetch
  ``https://data.sec.gov/submissions/CIK{filer_cik}.json`` and any
  history-pages referenced by ``filings.files``. Stored as
  ``submissions_filer_<cik>`` / ``submissions_filer_history_<cik>_<file>``
  blobs. Same shape as the issuer-side ``sec`` collector — different
  blob prefix so the normalizer dispatches correctly.

* **Phase B — information-table XML per uncached 13F-HR.** Phase A's
  payloads already list every filing the manager has made. We scan
  them in-memory for ``form_type`` in ``13F-HR / 13F-HR/A / 13F-CTR /
  13F-CTR/A`` (the holdings-bearing variants), skip any whose
  ``accession_number`` is already in ``sec.form13f_normalized_filings``,
  and for the rest fetch the filing's directory ``index.json`` to find
  the actual information-table XML filename (the convention varies:
  ``infotable.xml``, ``informationtable.xml``,
  ``form13fInfoTable.xml``, etc.). The XML lands as a
  ``form13f_<accession>`` blob.

13F-NT (notice) filings *don't* carry holdings — they cross-reference
an aggregate 13F-HR submitted by another filer. We don't fetch any
XML for them; the normalizer still marks them in
``sec.form13f_normalized_filings`` so the collector doesn't keep
re-considering them on every run.

Two operational modes:

* **Incremental** (default) — caps Phase B at ``--limit`` (default 50)
  uncached 13F-HRs per run. At ~10 filers × ~quarterly cadence, the
  steady-state daily run touches 0-2 new filings; the limit just keeps
  one-off "history just landed" surges from blowing the cron budget.
* **Backfill** (``--backfill``) — drop the limit. Combined with the
  filer's full submissions history, this lands every 13F the manager
  has ever filed. ~1k filings × 2 fetches each × 8 req/s = ~5 min.

Soft-failure per filing in Phase B: a 404 or malformed index for one
13F-HR logs + records partial and continues. Phase A failures (the
submissions index itself) are hard: without the index we can't
discover filings, so the run fails.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from genkei.common import db
from genkei.common.http import HttpClient
from genkei.common.watchlist import DEFAULT_WATCHLIST_PATH, FilerEntry, load_watchlist
from genkei.ingest.sec import (
    DEFAULT_RATE_LIMIT,
    SOURCE_NAME,
    resolve_user_agent,
)

COLLECT_FORM13F_ENDPOINT_LABEL = "collect_form13f"
SUBMISSIONS_FILER_BLOB_PREFIX = "submissions_filer_"
SUBMISSIONS_FILER_HISTORY_BLOB_PREFIX = "submissions_filer_history_"
FORM13F_BLOB_PREFIX = "form13f_"
SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
DEFAULT_LIMIT = 50

# 13F form types that carry an information table. The other 13F variants
# (NT, NT/A) are notice-only — they cross-reference another filer's
# aggregate 13F-HR and don't carry holdings themselves, so we skip
# fetching any XML for them.
HOLDINGS_BEARING_FORM_TYPES = frozenset(
    {"13F-HR", "13F-HR/A", "13F-CTR", "13F-CTR/A"}
)

LOGGER = logging.getLogger(__name__)


def positive_int(value: str) -> int:
    """Argparse type for positive integer limits."""
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("--limit must be greater than 0")
    return parsed


@dataclass(frozen=True)
class Form13FCandidate:
    """A 13F filing discovered in-memory during phase A.

    Carries everything phase B needs to fetch the info-table XML without
    a round-trip back to the database.
    """

    accession_number: str
    filer_cik: str
    form_type: str
    primary_document: str | None
    filed_at: str | None = None
    accepted_at: str | None = None


def build_filer_submissions_url(filer_cik: str) -> str:
    return f"{SUBMISSIONS_BASE}/CIK{filer_cik}.json"


def build_filing_index_url(filer_cik: str, accession_number: str) -> str:
    """Build the URL for a filing's directory ``index.json``."""
    folder = accession_number.replace("-", "")
    cik_int = int(filer_cik)
    return f"{ARCHIVES_BASE}/{cik_int}/{folder}/index.json"


def build_filing_file_url(filer_cik: str, accession_number: str, filename: str) -> str:
    folder = accession_number.replace("-", "")
    cik_int = int(filer_cik)
    return f"{ARCHIVES_BASE}/{cik_int}/{folder}/{filename}"


def select_info_table_filename(index_payload: Any) -> str | None:
    """Locate the information-table XML inside a filing's index.json.

    13F filers don't share a single naming convention for the info table.
    The two common shapes are ``infotable.xml`` and
    ``form13fInfoTable.xml``, but we've seen `informationtable.xml`,
    ``info_table.xml``, and capitalized variants in the wild. The rule
    we apply: case-insensitively, pick the first ``*.xml`` whose
    filename contains ``info`` (covering all of these). If nothing
    matches, fall back to the longest XML by reported size on the
    assumption that the info table is the largest XML in the filing.
    Returns None when no XML is present at all (genuinely malformed
    filing — we record + skip).
    """
    if not isinstance(index_payload, dict):
        return None
    directory = index_payload.get("directory")
    if not isinstance(directory, dict):
        return None
    items = directory.get("item")
    if not isinstance(items, list):
        return None
    xml_items: list[tuple[str, int]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.lower().endswith(".xml"):
            continue
        size = item.get("size")
        try:
            size_int = int(size) if size is not None else 0
        except (TypeError, ValueError):
            size_int = 0
        xml_items.append((name, size_int))
    if not xml_items:
        return None
    for name, _size in xml_items:
        if "info" in name.lower():
            return name
    # Fallback: largest XML in the filing. Avoids us silently picking
    # primary_doc.xml (which is the cover sheet, not the holdings).
    return max(xml_items, key=lambda pair: pair[1])[0]


def load_filers(path: Path) -> list[FilerEntry]:
    """Read 13F filers from the watchlist; raise if none configured."""
    try:
        watchlist = load_watchlist(path)
    except FileNotFoundError as exc:
        raise SystemExit(f"Watchlist file not found: {path}") from exc
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not watchlist.filers:
        raise SystemExit("No filers configured under `filers:` in the watchlist.")
    return list(watchlist.filers)


def extract_form13f_candidates(payload: Any, filer_cik: str) -> list[Form13FCandidate]:
    """Pull every 13F-flavored filing out of a submissions payload (any page).

    Walks both the ``filings.recent`` parallel-array shape (top-level
    submissions) and the root-level parallel-array shape (history
    pages). Returns 13F-HR / 13F-HR/A / 13F-NT / 13F-NT/A / 13F-CTR /
    13F-CTR/A entries — Phase B filters to the holdings-bearing subset.
    """
    if not isinstance(payload, dict):
        return []
    candidates: list[Form13FCandidate] = []

    def walk(parallel: Any) -> None:
        if not isinstance(parallel, dict):
            return
        accessions = parallel.get("accessionNumber")
        forms = parallel.get("form")
        primary_docs = parallel.get("primaryDocument")
        filing_dates = parallel.get("filingDate")
        acceptance_dates = parallel.get("acceptanceDateTime")
        if not isinstance(accessions, list) or not isinstance(forms, list):
            return
        n = min(len(accessions), len(forms))
        primary_docs_list = primary_docs if isinstance(primary_docs, list) else []
        filing_dates_list = filing_dates if isinstance(filing_dates, list) else []
        acceptance_dates_list = (
            acceptance_dates if isinstance(acceptance_dates, list) else []
        )
        for i in range(n):
            accn = accessions[i]
            form = forms[i]
            if not isinstance(accn, str) or not isinstance(form, str):
                continue
            if not form.startswith("13F"):
                continue
            primary_doc = (
                primary_docs_list[i] if i < len(primary_docs_list) else None
            )
            filed_at = filing_dates_list[i] if i < len(filing_dates_list) else None
            accepted_at = (
                acceptance_dates_list[i] if i < len(acceptance_dates_list) else None
            )
            candidates.append(
                Form13FCandidate(
                    accession_number=accn,
                    filer_cik=filer_cik,
                    form_type=form,
                    primary_document=primary_doc if isinstance(primary_doc, str) else None,
                    filed_at=filed_at if isinstance(filed_at, str) else None,
                    accepted_at=accepted_at if isinstance(accepted_at, str) else None,
                )
            )

    filings = payload.get("filings")
    if isinstance(filings, dict):
        walk(filings.get("recent"))
    # History pages: payload itself carries the parallel arrays.
    walk(payload)
    return candidates


def fetch_already_normalized_accessions() -> set[str]:
    """Return accession_numbers already marked in ``sec.form13f_normalized_filings``.

    Used by Phase B to skip filings whose XML we've already collected
    and normalized in a prior run. Empty set on first-ever run.
    """
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT accession_number FROM sec.form13f_normalized_filings"
        )
        return {row[0] for row in cur.fetchall()}


def collect(
    config_path: Path = DEFAULT_WATCHLIST_PATH,
    *,
    http: HttpClient | None = None,
    backfill: bool = False,
    limit: int = DEFAULT_LIMIT,
) -> int:
    """Run the 13F collector once and return the meta.ingest_runs id."""
    filers = load_filers(config_path)
    user_agent = resolve_user_agent()

    owns_http = http is None
    if http is None:
        http = HttpClient(SOURCE_NAME, rate_limit=DEFAULT_RATE_LIMIT, user_agent=user_agent)

    failures: list[dict[str, str]] = []
    try:
        with db.ingest_run(
            SOURCE_NAME,
            endpoint=COLLECT_FORM13F_ENDPOINT_LABEL,
            metadata={
                "watchlist_path": str(config_path),
                "filer_count": len(filers),
                "mode": "backfill" if backfill else "incremental",
                "phase_b_limit": None if backfill else limit,
            },
        ) as run:
            written = 0
            candidates: list[Form13FCandidate] = []

            # Phase A — submissions per filer (+ history pages).
            for index, filer in enumerate(filers, start=1):
                phase_a_written, phase_a_candidates = _fetch_filer_submissions(
                    filer, http, run.id, failures
                )
                written += phase_a_written
                candidates.extend(phase_a_candidates)
                if index % 5 == 0:
                    LOGGER.info(
                        "13F submissions progress: %s/%s (candidates so far=%s)",
                        index,
                        len(filers),
                        len(candidates),
                    )

            # Phase A is required: without submissions we can't discover
            # filings. If anything in Phase A failed we should not pretend
            # the run succeeded. (Per-history-page failures are individual
            # entries in `failures`; submissions root failures cause that
            # filer's candidates list to be empty above.)
            phase_a_root_failures = [
                f for f in failures
                if f["name"].startswith(SUBMISSIONS_FILER_BLOB_PREFIX)
                and not f["name"].startswith(SUBMISSIONS_FILER_HISTORY_BLOB_PREFIX)
            ]
            if phase_a_root_failures:
                db.record_partial_endpoints(run.id, failures)
                raise RuntimeError(
                    f"13F submissions fetch failed for {len(phase_a_root_failures)} filer(s); "
                    "no partial 13F snapshot will be processed."
                )

            # Phase B — info-table XML per uncached, holdings-bearing 13F.
            already_normalized = fetch_already_normalized_accessions()
            phase_b_candidates = _select_phase_b_candidates(
                candidates,
                already_normalized=already_normalized,
                limit=None if backfill else limit,
            )
            for index, candidate in enumerate(phase_b_candidates, start=1):
                if _fetch_info_table(candidate, http, run.id, failures):
                    written += 1
                if index % 25 == 0:
                    LOGGER.info(
                        "13F info-table progress: %s/%s",
                        index,
                        len(phase_b_candidates),
                    )

            run.add_rows(written)
            if failures:
                db.record_partial_endpoints(run.id, failures)
            return run.id
    finally:
        if owns_http:
            http.close()


def _select_phase_b_candidates(
    candidates: list[Form13FCandidate],
    *,
    already_normalized: set[str],
    limit: int | None,
) -> list[Form13FCandidate]:
    """Filter candidates to uncached holdings-bearing 13Fs, newest-first."""
    holdings_bearing = [
        c
        for c in candidates
        if c.form_type in HOLDINGS_BEARING_FORM_TYPES
        and c.accession_number not in already_normalized
    ]
    holdings_bearing.sort(
        key=lambda c: (c.accepted_at or "", c.filed_at or "", c.accession_number),
        reverse=True,
    )
    # Dedupe on accession_number — phase A walks both `filings.recent`
    # and any history pages, and a single recent filing could plausibly
    # appear in both shapes on edge cases.
    seen: set[str] = set()
    deduped: list[Form13FCandidate] = []
    for c in holdings_bearing:
        if c.accession_number in seen:
            continue
        seen.add(c.accession_number)
        deduped.append(c)
    if limit is not None:
        return deduped[:limit]
    return deduped


def _fetch_filer_submissions(
    filer: FilerEntry,
    http: HttpClient,
    ingest_run_id: int,
    failures: list[dict[str, str]],
) -> tuple[int, list[Form13FCandidate]]:
    """Phase A: fetch submissions + history pages for one filer."""
    written = 0
    candidates: list[Form13FCandidate] = []

    submissions_url = build_filer_submissions_url(filer.filer_cik)
    submissions_endpoint = f"{SUBMISSIONS_FILER_BLOB_PREFIX}{filer.filer_cik}"
    submissions_payload = _fetch_json(
        submissions_endpoint, submissions_url, http, ingest_run_id, failures
    )
    if submissions_payload is None:
        return written, candidates
    written += 1
    candidates.extend(extract_form13f_candidates(submissions_payload, filer.filer_cik))

    file_refs = (submissions_payload.get("filings", {}) or {}).get("files", []) or []
    for ref in file_refs:
        if not isinstance(ref, dict):
            continue
        fname = ref.get("name")
        if not isinstance(fname, str) or "/" in fname or not fname.endswith(".json"):
            continue
        history_url = f"{SUBMISSIONS_BASE}/{fname}"
        history_endpoint = f"{SUBMISSIONS_FILER_HISTORY_BLOB_PREFIX}{filer.filer_cik}_{fname}"
        history_payload = _fetch_json(
            history_endpoint, history_url, http, ingest_run_id, failures
        )
        if history_payload is not None:
            written += 1
            candidates.extend(extract_form13f_candidates(history_payload, filer.filer_cik))

    return written, candidates


def _fetch_info_table(
    candidate: Form13FCandidate,
    http: HttpClient,
    ingest_run_id: int,
    failures: list[dict[str, str]],
) -> bool:
    """Phase B: fetch one filing's info-table XML and land the blob."""
    index_url = build_filing_index_url(candidate.filer_cik, candidate.accession_number)
    try:
        index_payload = http.get_json(index_url)
    except (
        httpx.TimeoutException,
        httpx.NetworkError,
        httpx.HTTPStatusError,
        json.JSONDecodeError,
    ) as exc:
        LOGGER.warning(
            "13F filing index fetch failed for %s (CIK %s): %s",
            candidate.accession_number,
            candidate.filer_cik,
            exc,
        )
        failures.append(
            {
                "name": f"{FORM13F_BLOB_PREFIX}{candidate.accession_number}_index",
                "url": index_url,
                "error": str(exc),
            }
        )
        return False

    filename = select_info_table_filename(index_payload)
    if filename is None:
        LOGGER.warning(
            "13F filing %s (CIK %s) has no info-table XML in its index; skipping",
            candidate.accession_number,
            candidate.filer_cik,
        )
        failures.append(
            {
                "name": f"{FORM13F_BLOB_PREFIX}{candidate.accession_number}",
                "url": index_url,
                "error": "no info-table xml found in filing index",
            }
        )
        return False

    xml_url = build_filing_file_url(
        candidate.filer_cik, candidate.accession_number, filename
    )
    try:
        response = http.get(xml_url)
        response.raise_for_status()
        xml_text = response.text
    except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
        LOGGER.warning(
            "13F info-table XML fetch failed for %s (%s): %s",
            candidate.accession_number,
            filename,
            exc,
        )
        failures.append(
            {
                "name": f"{FORM13F_BLOB_PREFIX}{candidate.accession_number}",
                "url": xml_url,
                "error": str(exc),
            }
        )
        return False

    endpoint_name = f"{FORM13F_BLOB_PREFIX}{candidate.accession_number}"
    db.store_raw_blob(
        ingest_run_id,
        endpoint_name,
        xml_url,
        {"xml": xml_text, "info_table_filename": filename, "index_url": index_url},
    )
    return True


def _fetch_json(
    endpoint_name: str,
    url: str,
    http: HttpClient,
    ingest_run_id: int,
    failures: list[dict[str, str]],
) -> Any | None:
    """Fetch JSON and store it; return parsed payload (or None on failure)."""
    try:
        payload = http.get_json(url)
    except (
        httpx.TimeoutException,
        httpx.NetworkError,
        httpx.HTTPStatusError,
        json.JSONDecodeError,
    ) as exc:
        LOGGER.warning("13F fetch failed for %s: %s", endpoint_name, exc)
        failures.append({"name": endpoint_name, "url": url, "error": str(exc)})
        return None
    db.store_raw_blob(ingest_run_id, endpoint_name, url, payload)
    return payload


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect SEC 13F submissions + information-table XML into Postgres."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_WATCHLIST_PATH)
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="No limit on uncached 13F-HR XMLs in Phase B. Run once historically.",
    )
    parser.add_argument(
        "--limit",
        type=positive_int,
        default=DEFAULT_LIMIT,
        help="Cap on uncached 13F-HR XMLs per Phase B run (ignored with --backfill).",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv or sys.argv[1:])
    run_id = collect(args.config, backfill=args.backfill, limit=args.limit)
    if args.json:
        print(
            json.dumps(
                {
                    "ingest_run_id": run_id,
                    "source": SOURCE_NAME,
                    "endpoint": COLLECT_FORM13F_ENDPOINT_LABEL,
                    "mode": "backfill" if args.backfill else "incremental",
                }
            )
        )
    else:
        print(f"SEC 13F collector wrote ingest_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
