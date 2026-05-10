"""SEC EDGAR collector (B-027 option B).

Fetches the submissions index + XBRL company-facts payload for every
equity in ``config/watchlists.yml::equities``. Lands two raw blobs per
company (``submissions_<cik>``, ``companyfacts_<cik>``) in
``meta.raw_blobs``. The downstream normalizer (``genkei.normalize.sec``)
reads from those blobs.

Per-filing structured payloads (Form 4 transactions, 13F holdings) are
explicit follow-ups (B-079, B-080) — driven by the experiments that
will need them rather than guessed up front.

Single-mode design: SEC's submissions endpoint returns the recent
filing index in one call, with `filings.files` pointing at older-history
JSON files for companies with deep history. The XBRL companyfacts
endpoint returns the full fact history per call. Daily and backfill
are the same code path; new filings/facts land via natural PKs.

SEC fair-access rules:
  - Rate limit: 10 req/sec across all SEC.gov endpoints. We use
    HttpClient with per_second(8) to stay under (G-021).
  - User-Agent must identify the user (name + email). Configurable via
    ``SEC_USER_AGENT`` env var; defaults to a project string with
    placeholder email if unset, which SEC may reject (G-022).

No API key required — SEC EDGAR is open-access.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from genkei.common import db
from genkei.common.http import HttpClient, RateLimit

DEFAULT_WATCHLIST_PATH = Path("config/watchlists.yml")
SOURCE_NAME = "sec"
COLLECT_ENDPOINT_LABEL = "collect"
SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
COMPANYFACTS_BASE = "https://data.sec.gov/api/xbrl/companyfacts"
SUBMISSIONS_BLOB_PREFIX = "submissions_"
SUBMISSIONS_HISTORY_BLOB_PREFIX = "submissions_history_"
COMPANYFACTS_BLOB_PREFIX = "companyfacts_"
# SEC's documented limit is 10 req/sec across data.sec.gov; we stay under
# at 8 req/sec so a momentary burst from another client on the same
# runner doesn't push us over.
DEFAULT_RATE_LIMIT = RateLimit.per_second(8)
USER_AGENT_ENV = "SEC_USER_AGENT"
DEFAULT_USER_AGENT = "Genkei Capital research-desk noreply@example.com"
RAW_BLOBS_INSERT = (
    "INSERT INTO meta.raw_blobs (ingest_run_id, endpoint_name, url, payload) "
    "VALUES (%s, %s, %s, %s::jsonb) "
    "ON CONFLICT (ingest_run_id, endpoint_name) DO NOTHING"
)
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompanyTarget:
    """A SEC EDGAR company we want to fetch."""

    cik: str
    symbol: str
    name: str


def load_companies(path: Path) -> list[CompanyTarget]:
    """Read ``equities:`` from watchlists.yml as ``CompanyTarget``s.

    Skips entries without a ``cik`` field. Dedupes on CIK so multi-class
    listings (GOOG/GOOGL share Alphabet's CIK) only fetch once.
    """
    try:
        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise SystemExit(f"Watchlist file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise SystemExit(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("Watchlist root must be a YAML mapping.")
    equities = data.get("equities", {})
    if not isinstance(equities, dict):
        raise SystemExit("watchlists.yml `equities` must be a mapping (tier -> list).")

    out: list[CompanyTarget] = []
    seen_ciks: set[str] = set()
    for tier_name, tier_entries in equities.items():
        if not isinstance(tier_entries, list):
            continue
        for entry in tier_entries:
            if not isinstance(entry, dict):
                continue
            cik = entry.get("cik")
            symbol = entry.get("symbol")
            name = entry.get("name")
            if not isinstance(cik, str) or not cik:
                LOGGER.warning("skip equity %s in tier %s — missing cik", symbol, tier_name)
                continue
            if not isinstance(symbol, str) or not isinstance(name, str):
                LOGGER.warning("skip malformed equity entry under tier %s", tier_name)
                continue
            if cik in seen_ciks:
                LOGGER.debug("skip duplicate CIK %s (%s)", cik, symbol)
                continue
            seen_ciks.add(cik)
            out.append(CompanyTarget(cik=cik, symbol=symbol, name=name))
    if not out:
        raise SystemExit("No equities with CIK found under `equities:` in the watchlist.")
    return out


def resolve_user_agent() -> str:
    """Return the User-Agent for SEC requests.

    SEC requires identification (name + contact). We read ``SEC_USER_AGENT``
    from the environment; if unset we fall back to a placeholder string,
    which SEC may rate-limit or reject (G-022).
    """
    ua = os.environ.get(USER_AGENT_ENV)
    if ua:
        return ua
    LOGGER.warning(
        "%s not set; using placeholder User-Agent. SEC may rate-limit or reject. "
        "Set %s in .env / GH Actions secrets to your real name + email.",
        USER_AGENT_ENV,
        USER_AGENT_ENV,
    )
    return DEFAULT_USER_AGENT


def build_submissions_url(cik: str) -> str:
    """Build the URL for the submissions index."""
    return f"{SUBMISSIONS_BASE}/CIK{cik}.json"


def build_companyfacts_url(cik: str) -> str:
    """Build the URL for the XBRL company-facts endpoint."""
    return f"{COMPANYFACTS_BASE}/CIK{cik}.json"


def _store_blob(ingest_run_id: int, endpoint_name: str, url: str, payload: Any) -> None:
    """Insert one raw_blobs row."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(RAW_BLOBS_INSERT, [ingest_run_id, endpoint_name, url, json.dumps(payload)])


def collect(
    config_path: Path = DEFAULT_WATCHLIST_PATH,
    *,
    http: HttpClient | None = None,
) -> int:
    """Run the SEC collector once and return the meta.ingest_runs id."""
    companies = load_companies(config_path)
    user_agent = resolve_user_agent()

    owns_http = http is None
    if http is None:
        http = HttpClient(SOURCE_NAME, rate_limit=DEFAULT_RATE_LIMIT, user_agent=user_agent)

    failures: list[dict[str, str]] = []
    try:
        with db.ingest_run(
            SOURCE_NAME,
            endpoint=COLLECT_ENDPOINT_LABEL,
            metadata={
                "watchlist_path": str(config_path),
                "company_count": len(companies),
            },
        ) as run:
            written = 0
            for index, target in enumerate(companies, start=1):
                written += _fetch_company_pair(target, http, run.id, failures)
                if index % 5 == 0:
                    LOGGER.info("SEC collect progress: %s/%s", index, len(companies))
            run.add_rows(written)
            if failures:
                _record_partial(run.id, failures)
                # Per the FRED-fix lesson (G-019/G-020): partial-fetch failures
                # mark the run failed so the normalizer doesn't half-load.
                raise RuntimeError(
                    f"SEC fetch failed for {len(failures)} endpoint(s); "
                    "no partial SEC snapshot will be normalized."
                )
            return run.id
    finally:
        if owns_http:
            http.close()


def _fetch_company_pair(
    target: CompanyTarget,
    http: HttpClient,
    ingest_run_id: int,
    failures: list[dict[str, str]],
) -> int:
    """Fetch submissions + history pages + companyfacts for one CIK.

    Returns the number of raw_blobs rows written.
    """
    written = 0

    # 1. Submissions index (recent ~1000 filings + a `files` list pointing
    #    at older-history JSON files for companies with deeper history).
    submissions_url = build_submissions_url(target.cik)
    submissions_endpoint = f"{SUBMISSIONS_BLOB_PREFIX}{target.cik}"
    submissions_payload = _fetch_blob(
        target, submissions_endpoint, submissions_url, http, ingest_run_id, failures
    )
    if submissions_payload is None:
        # Submissions index failed; skip the rest for this CIK so the
        # downstream normalizer doesn't see partial data.
        return written
    written += 1

    # 2. Older-history pages referenced by submissions.filings.files. Each
    #    history file holds another batch of filings as parallel arrays.
    file_refs = (submissions_payload.get("filings", {}) or {}).get("files", []) or []
    for ref in file_refs:
        if not isinstance(ref, dict):
            continue
        fname = ref.get("name")
        if not isinstance(fname, str) or "/" in fname or not fname.endswith(".json"):
            continue
        history_url = f"{SUBMISSIONS_BASE}/{fname}"
        # endpoint_name carries the filename so the normalizer can still
        # tell history pages apart within one ingest_run.
        history_endpoint = f"{SUBMISSIONS_HISTORY_BLOB_PREFIX}{target.cik}_{fname}"
        history_payload = _fetch_blob(
            target, history_endpoint, history_url, http, ingest_run_id, failures
        )
        if history_payload is not None:
            written += 1

    # 3. XBRL companyfacts. Returns the entire fact history per call.
    facts_url = build_companyfacts_url(target.cik)
    facts_endpoint = f"{COMPANYFACTS_BLOB_PREFIX}{target.cik}"
    facts_payload = _fetch_blob(target, facts_endpoint, facts_url, http, ingest_run_id, failures)
    if facts_payload is not None:
        written += 1

    return written


def _fetch_blob(
    target: CompanyTarget,
    endpoint_name: str,
    url: str,
    http: HttpClient,
    ingest_run_id: int,
    failures: list[dict[str, str]],
) -> Any | None:
    """Fetch one URL, store the blob, return parsed payload or None on error."""
    try:
        payload = http.get_json(url)
    except Exception as exc:
        LOGGER.warning("SEC fetch failed for %s (%s): %s", endpoint_name, target.symbol, exc)
        failures.append({"name": endpoint_name, "url": url, "error": str(exc)})
        return None
    _store_blob(ingest_run_id, endpoint_name, url, payload)
    return payload


def _record_partial(ingest_run_id: int, partial: list[dict[str, str]]) -> None:
    """Stash per-company partial-failure metadata on the ingest_runs row."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE meta.ingest_runs SET metadata = "
            "COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('partial_endpoints', %s::jsonb) "
            "WHERE id = %s",
            [json.dumps(partial), ingest_run_id],
        )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect SEC EDGAR submissions + XBRL company facts into Postgres."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_WATCHLIST_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv or sys.argv[1:])
    run_id = collect(args.config)
    print(f"SEC collector wrote ingest_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
