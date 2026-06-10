"""BEA NIPA macro-data collector (B-029).

Companion to the FRED ingester (B-028) — covers the real-economy
dimension (GDP, personal income, PCE, PCE-core inflation, corporate
profits, savings rate) that FRED's rates/credit/vol/FX coverage
doesn't reach.

**API contract** — REST endpoint at ``https://apps.bea.gov/api/data/``.
Auth is a free ``UserID`` query parameter (register at
https://apps.bea.gov/API/signup/). The free tier publishes no
documented rate limit; the community convention is "be polite" — we
use 2 req/s. The JSON envelope is
``{"BEAAPI": {"Request": ..., "Results": {"Statistic": ..., "Data":
[...], "Notes": [...]}}}``; the parser unwraps to ``Results.Data``
where each row is a per-(line, period) observation.

**Coverage v1** — NIPA dataset only. The 12 other BEA datasets (MNE,
FixedAssets, ITA, IIP, Regional, GDPbyIndustry, ...) are deferred —
every macro signal we'd actually want from BEA lives in NIPA, and the
v1 watchlist (10 curated lines, per the design call) exercises it.

**Fetch shape** — one URL per unique ``(table_id, frequency)`` tuple,
NOT per (table_id, line_number). The watchlist groups multiple lines
per table (T20100 has lines 1 / 24 / 35; T20804 has lines 1 / 25); a
single ``TableName=Tnnnnn&LineNumber=X`` call returns just that line,
but a ``TableName=Tnnnnn`` call returns every line on the table at
~the same cost. We fetch whole tables and let the normalizer filter
to watchlist lines — fewer API calls, simpler error recovery, lower
chance of partial-state mid-run.

**Vintage** — latest-only (NOT vintage-aware). BEA's API doesn't
expose a vintage-date parameter the way FRED's ``realtime_start``
does; revisions overwrite in place at the source. v1 matches that
semantics. The migration's PK omits a vintage column for the same
reason — a future v2 vintage-aware path would add ``fetched_at_date``
to the PK and snapshot a private revision trail.

API key: ``BEA_API_KEY`` env var. CI reads from a same-named GH
Actions secret; local dev sets it in ``.env``. Mirrors FRED's G-015
fix — the key is redacted from URLs landed in ``meta.raw_blobs`` so
the audit table stays safe to share for replay/debug.
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
from urllib.parse import urlencode

import httpx

from genkei.common import db
from genkei.common.http import HttpClient, RateLimit
from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    BeaSeriesEntry,
    load_watchlist,
)

SOURCE_NAME = "bea"
COLLECT_ENDPOINT_LABEL = "collect"
BEA_BASE_URL = "https://apps.bea.gov/api/data/"
BEA_DATASET_NIPA = "NIPA"
BLOB_PREFIX = "bea_"

# Free tier publishes no documented per-second cap; 2 req/s is the
# polite default we use for any "be reasonable" free API. The whole
# watchlist (~7 unique tables) finishes in <5 seconds either way.
DEFAULT_RATE_LIMIT = RateLimit.per_second(2)

API_KEY_ENV = "BEA_API_KEY"

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class TableTarget:
    """One BEA table + frequency pair we fetch in a single API call.

    Multiple watchlist lines can share a target — the collector fetches
    the whole table and the normalizer filters down to the watched
    lines.
    """

    table_id: str
    frequency: str  # 'Q' | 'A' | 'M'

    @property
    def blob_endpoint(self) -> str:
        """Stable endpoint name used as meta.raw_blobs.endpoint_name."""
        return f"{BLOB_PREFIX}{self.table_id.lower()}_{self.frequency.lower()}"


def load_targets(path: Path = DEFAULT_WATCHLIST_PATH) -> list[TableTarget]:
    """Read the ``bea:`` watchlist section into a deduped list of fetch targets.

    Watchlist may list several lines per (table, frequency); the
    collector fetches each unique pair once. Sort the output for
    deterministic test fixtures + reproducible blob ordering across
    runs.
    """
    try:
        watchlist = load_watchlist(path)
    except FileNotFoundError as exc:
        raise SystemExit(f"Watchlist file not found: {path}") from exc
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not watchlist.bea:
        raise SystemExit("watchlists.yml is missing a `bea:` section or it is empty.")
    seen: set[tuple[str, str]] = set()
    out: list[TableTarget] = []
    for entry in watchlist.bea:
        key = (entry.table_id, entry.frequency)
        if key in seen:
            continue
        seen.add(key)
        out.append(TableTarget(table_id=entry.table_id, frequency=entry.frequency))
    out.sort(key=lambda t: (t.table_id, t.frequency))
    return out


def require_api_key() -> str:
    """Return ``BEA_API_KEY`` from the environment or raise."""
    key = os.environ.get(API_KEY_ENV)
    if not key:
        raise SystemExit(
            f"{API_KEY_ENV} is not set. Register a free key at "
            "https://apps.bea.gov/API/signup/ and set it in your "
            "local .env or as a GitHub Actions secret."
        )
    return key


def build_table_url(api_key: str, target: TableTarget) -> str:
    """Build the GetData URL for one (TableName, Frequency) tuple.

    ``Year=ALL`` returns every available period; BEA caps the response
    at a reasonable size and the NIPA tables we want comfortably fit
    in one call (decades of quarterly observations × ~50 lines per
    table is still under a megabyte of JSON).
    """
    params = {
        "UserID": api_key,
        "method": "GetData",
        "datasetname": BEA_DATASET_NIPA,
        "TableName": target.table_id,
        "Frequency": target.frequency,
        "Year": "ALL",
        "ResultFormat": "JSON",
    }
    # urlencode keeps the param order stable so blob URLs are
    # deterministic across runs (helps debugging when comparing two
    # consecutive raw_blobs rows).
    return f"{BEA_BASE_URL}?{urlencode(params)}"


def _redact_key(url: str, api_key: str) -> str:
    """Replace the API key in a URL with ``***`` so raw_blobs.url is safe to log."""
    if not api_key:
        return url
    return url.replace(api_key, "***")


def collect(
    config_path: Path = DEFAULT_WATCHLIST_PATH,
    *,
    http: HttpClient | None = None,
    api_key: str | None = None,
) -> int:
    """Run the BEA collector once and return the meta.ingest_runs id.

    ``api_key`` is injectable for testing; production reads
    ``BEA_API_KEY`` via ``require_api_key()``.
    """
    targets = load_targets(config_path)
    key = api_key or require_api_key()

    owns_http = http is None
    if http is None:
        http = HttpClient(SOURCE_NAME, rate_limit=DEFAULT_RATE_LIMIT)

    failures: list[dict[str, str]] = []
    try:
        with db.ingest_run(
            SOURCE_NAME,
            endpoint=COLLECT_ENDPOINT_LABEL,
            metadata={
                "watchlist_path": str(config_path),
                "target_count": len(targets),
            },
        ) as run:
            written = 0
            for i, target in enumerate(targets, start=1):
                written += _fetch_target(target, key, http, run.id, failures)
                if i % 5 == 0:
                    LOGGER.info(
                        "BEA collect progress: %s/%s", i, len(targets)
                    )
            run.add_rows(written)
            if failures:
                db.record_partial_endpoints(run.id, failures)
                raise RuntimeError(
                    f"BEA fetch failed for {len(failures)} target(s); "
                    "no partial NIPA snapshot will be normalized."
                )
            return run.id
    finally:
        if owns_http:
            http.close()


def _fetch_target(
    target: TableTarget,
    api_key: str,
    http: HttpClient,
    ingest_run_id: int,
    failures: list[dict[str, str]],
) -> int:
    """Fetch one (TableName, Frequency) pair, land its raw blob."""
    url = build_table_url(api_key, target)
    try:
        payload = http.get_json(url)
    except (
        httpx.TimeoutException,
        httpx.NetworkError,
        httpx.HTTPStatusError,
        json.JSONDecodeError,
    ) as exc:
        safe_error = _redact_key(str(exc), api_key)
        LOGGER.warning(
            "BEA fetch failed for %s: %s", target.blob_endpoint, safe_error
        )
        failures.append(
            {
                "name": target.blob_endpoint,
                "url": _redact_key(url, api_key),
                "error": safe_error,
            }
        )
        return 0
    # BEA returns 200 OK with an error envelope inside on bad params
    # (invalid table id, missing key, etc.); detect + treat as failure
    # so the partial-endpoints record is honest.
    if _is_bea_error_envelope(payload):
        error_text = _extract_bea_error(payload)
        safe_error = _redact_key(error_text, api_key)
        LOGGER.warning("BEA returned error for %s: %s", target.blob_endpoint, safe_error)
        failures.append(
            {
                "name": target.blob_endpoint,
                "url": _redact_key(url, api_key),
                "error": safe_error,
            }
        )
        return 0
    db.store_raw_blob(
        ingest_run_id,
        target.blob_endpoint,
        _redact_key(url, api_key),
        payload,
    )
    return 1


def _is_bea_error_envelope(payload: Any) -> bool:
    """Detect BEA's 200-OK-with-error response shape.

    Bad table-id / missing-key returns
    ``{"BEAAPI": {"Error": {...}}}`` (no Results section). We check
    for the absence of a usable Results.Data array.
    """
    if not isinstance(payload, dict):
        return True
    bea = payload.get("BEAAPI")
    if not isinstance(bea, dict):
        return True
    if "Error" in bea:
        return True
    results = bea.get("Results")
    if results is None:
        return True
    # BEA sometimes nests Error inside Results
    # ({"Results": {"Error": {...}}}). Handle that shape too.
    if isinstance(results, dict) and "Error" in results:
        return True
    return False


def _extract_bea_error(payload: Any) -> str:
    """Best-effort extraction of BEA's error message for logging."""
    if not isinstance(payload, dict):
        return f"non-dict response: {payload!r}"
    bea = payload.get("BEAAPI", {})
    if isinstance(bea, dict):
        err = bea.get("Error")
        if isinstance(err, dict):
            return str(err.get("APIErrorDescription") or err)
        results = bea.get("Results")
        if isinstance(results, dict):
            err = results.get("Error")
            if isinstance(err, dict):
                return str(err.get("APIErrorDescription") or err)
            if isinstance(err, list) and err:
                # BEA sometimes wraps the error in a single-element list.
                first = err[0]
                if isinstance(first, dict):
                    return str(
                        first.get("APIErrorDescription") or first
                    )
    return f"unknown BEA error: {payload!r}"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_WATCHLIST_PATH,
        help="Path to watchlists.yml.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit summary as JSON for agent consumption.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args(argv)
    try:
        run_id = collect(config_path=args.config)
    except SystemExit:
        raise
    except Exception as exc:
        LOGGER.exception("BEA collect failed")
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"BEA collect failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"ok": True, "ingest_run_id": run_id}))
    else:
        print(f"BEA collect: meta.ingest_runs id={run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
