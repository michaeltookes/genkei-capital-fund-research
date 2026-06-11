"""Treasury Fiscal Data collector (B-030).

Companion to the FRED ingester (B-028) — covers the public-debt level,
the Treasury operating cash balance (TGA), monthly interest expense,
and weighted-average cost-of-debt per security class. The four
endpoints we pull in v1:

  * ``/v2/accounting/od/debt_to_penny`` — daily total + held-public
    + intragovernmental debt outstanding (~30+ year history).
  * ``/v1/accounting/dts/operating_cash_balance`` — daily Treasury
    cash balance (TGA closing balance). v1 covers ~April 2022 forward
    due to DTS format change; pre-2022 backfill deferred to v2.
  * ``/v2/accounting/od/interest_expense`` — monthly interest expense
    on the public debt.
  * ``/v2/accounting/od/avg_interest_rates`` — monthly weighted-average
    yield per security class (bills / notes / bonds / total).

**API contract** — REST endpoints at
``https://api.fiscaldata.treasury.gov/services/api/fiscal_service``.
No auth, no API key, no documented rate limit — we use 2 req/s as
the polite default. The JSON envelope is ``{"data": [...], "meta":
{...}, "links": {...}}``. Pagination via ``page[number]`` + ``page[size]``
up to 10,000 rows per page; the collector loops every page until
``meta.total-pages`` is reached.

**Fetch shape** — one URL family per unique endpoint, NOT per series.
Multiple watchlist series can share an endpoint (e.g. the three
``debt_to_penny`` columns all come from a single endpoint call); the
collector fetches the endpoint once and the normalizer filters down
to each watched (value_field + row_filter) tuple. Fewer API calls,
simpler error recovery, lower chance of partial-state mid-run.

**Vintage** — latest-only (NOT vintage-aware). Treasury revises in
place at the source. The schema's PK is ``(series_id, ts)`` with no
vintage column; daily upsert overwrites the latest value.

The combined payload landed in ``meta.raw_blobs`` merges every page's
``data`` array into a single object so the normalizer sees one
canonical blob per endpoint.

No API key required — Treasury Fiscal Data is fully open. The
``url`` stored in ``meta.raw_blobs`` is the first-page request URL
(deterministic for a given watchlist run).
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from genkei.common import db
from genkei.common.http import HttpClient, RateLimit
from genkei.common.watchlist import DEFAULT_WATCHLIST_PATH, load_watchlist

SOURCE_NAME = "treasury"
COLLECT_ENDPOINT_LABEL = "collect"
TREASURY_BASE_URL = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
BLOB_PREFIX = "treasury_"
PAGE_SIZE = 10000
MAX_PAGES = 200  # 2M rows safety ceiling — current v1 endpoints peak at ~5 pages.

# Treasury Fiscal Data publishes no documented rate limit. 2 req/s is
# the polite default we use for any "be reasonable" free API. The
# whole watchlist (4 endpoints × a handful of pages each) finishes in
# under a minute either way.
DEFAULT_RATE_LIMIT = RateLimit.per_second(2)

LOGGER = logging.getLogger(__name__)


def _blob_slug_part(value: str) -> str:
    """Normalize endpoint/date-field text for raw blob endpoint names."""
    return value.strip("/").strip().replace("/", "_").replace(" ", "_").lower()


@dataclass(frozen=True)
class EndpointTarget:
    """One Treasury Fiscal Data endpoint we fetch in a single API call.

    Multiple watchlist series can share a target — the collector
    fetches the whole endpoint history and the normalizer filters
    down to each watched (value_field + row_filter) tuple.
    """

    endpoint: str  # e.g. "/v2/accounting/od/debt_to_penny"
    date_field: str  # e.g. "record_date"

    @property
    def blob_endpoint(self) -> str:
        """Stable endpoint name used as meta.raw_blobs.endpoint_name.

        The endpoint path is slugified — leading slashes dropped,
        remaining slashes replaced with underscores — so each
        (endpoint, date_field) tuple maps to a unique, filesystem-safe
        identifier that the normalizer can reverse-map back.
        """
        endpoint_slug = _blob_slug_part(self.endpoint)
        date_slug = _blob_slug_part(self.date_field)
        return f"{BLOB_PREFIX}{endpoint_slug}__{date_slug}"


def load_targets(path: Path = DEFAULT_WATCHLIST_PATH) -> list[EndpointTarget]:
    """Read the ``treasury:`` watchlist section into a deduped list of fetch targets.

    Watchlist may list several series per endpoint; the collector
    fetches each unique (endpoint, date_field) pair once. Sort the
    output for deterministic blob ordering across runs.
    """
    try:
        watchlist = load_watchlist(path)
    except FileNotFoundError as exc:
        raise SystemExit(f"Watchlist file not found: {path}") from exc
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not watchlist.treasury:
        raise SystemExit(
            "watchlists.yml is missing a `treasury:` section or it is empty."
        )
    seen: set[tuple[str, str]] = set()
    out: list[EndpointTarget] = []
    for entry in watchlist.treasury:
        key = (entry.endpoint, entry.date_field)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            EndpointTarget(endpoint=entry.endpoint, date_field=entry.date_field)
        )
    out.sort(key=lambda t: (t.endpoint, t.date_field))
    return out


def build_page_url(
    target: EndpointTarget,
    *,
    page_number: int,
    page_size: int = PAGE_SIZE,
) -> str:
    """Build one paginated request URL for a Treasury Fiscal Data endpoint.

    Sort ascending on the target's ``date_field`` so combined payloads
    are deterministic across runs (oldest → newest). ``urlencode`` keeps
    the param order stable so two consecutive runs produce identical
    first-page URLs in ``meta.raw_blobs.url``.
    """
    params = [
        ("sort", target.date_field),
        ("page[size]", str(page_size)),
        ("page[number]", str(page_number)),
        ("format", "json"),
    ]
    return f"{TREASURY_BASE_URL}{target.endpoint}?{urlencode(params)}"


def collect(
    config_path: Path = DEFAULT_WATCHLIST_PATH,
    *,
    http: HttpClient | None = None,
) -> int:
    """Run the Treasury collector once and return the meta.ingest_runs id.

    No API key — Treasury Fiscal Data is fully open.
    """
    targets = load_targets(config_path)

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
                written += _fetch_target(target, http, run.id, failures)
                if i % 2 == 0:
                    LOGGER.info(
                        "Treasury collect progress: %s/%s", i, len(targets)
                    )
            run.add_rows(written)
            if failures:
                db.record_partial_endpoints(run.id, failures)
                raise RuntimeError(
                    f"Treasury fetch failed for {len(failures)} target(s); "
                    "no partial snapshot will be normalized."
                )
            return run.id
    finally:
        if owns_http:
            http.close()


def _fetch_target(
    target: EndpointTarget,
    http: HttpClient,
    ingest_run_id: int,
    failures: list[dict[str, str]],
) -> int:
    """Fetch every page for one endpoint, land a single combined blob."""
    first_url = build_page_url(target, page_number=1)
    try:
        combined = _fetch_all_pages(target, http)
    except (
        httpx.TimeoutException,
        httpx.NetworkError,
        httpx.HTTPStatusError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        LOGGER.warning(
            "Treasury fetch failed for %s: %s", target.blob_endpoint, exc
        )
        failures.append(
            {
                "name": target.blob_endpoint,
                "url": first_url,
                "error": str(exc),
            }
        )
        return 0
    db.store_raw_blob(
        ingest_run_id,
        target.blob_endpoint,
        first_url,
        combined,
    )
    return 1


def _fetch_all_pages(target: EndpointTarget, http: HttpClient) -> dict[str, Any]:
    """Page through every record for one endpoint, return a single merged payload.

    Treasury's pagination metadata exposes ``meta.total-pages``;
    we trust it as the loop bound but also cap at ``MAX_PAGES`` to
    avoid a runaway in case the API ever returns a malformed count.
    """
    combined: dict[str, Any] | None = None
    data: list[Any] = []
    total_pages: int | None = None

    for page_number in range(1, MAX_PAGES + 1):
        url = build_page_url(target, page_number=page_number)
        payload = http.get_json(url)
        if not isinstance(payload, dict):
            raise ValueError(
                f"Treasury payload for {target.endpoint} page {page_number} "
                "is not an object."
            )
        page_data = payload.get("data")
        if not isinstance(page_data, list):
            raise ValueError(
                f"Treasury payload for {target.endpoint} page {page_number} "
                "is missing a `data` array."
            )
        if combined is None:
            # Shallow-copy the first page; we overwrite `data` after
            # the loop. Keep `meta` and `links` from the first page as
            # a representative snapshot.
            combined = copy.deepcopy(payload)
            total_pages = _extract_total_pages(payload)
        data.extend(page_data)

        # Loop exit conditions:
        # 1. ``total-pages`` from the meta block is the source of truth
        #    when present — Treasury sets it on every response we use.
        # 2. Fallback only when meta lacks ``total-pages``: a
        #    sub-PAGE_SIZE response means the server has no more rows.
        if total_pages is not None:
            if page_number >= total_pages:
                break
        elif len(page_data) < PAGE_SIZE:
            break
    else:
        raise ValueError(
            f"Treasury payload for {target.endpoint} exceeded MAX_PAGES "
            f"({MAX_PAGES}); increase the cap or paginate differently."
        )

    if combined is None:
        # MAX_PAGES > 0 so the loop runs at least once; this branch
        # only triggers if the loop short-circuits with no iterations
        # which can't happen. Defensive guard for the type checker.
        raise ValueError(f"Treasury payload for {target.endpoint} was empty.")
    combined["data"] = data
    if isinstance(combined.get("meta"), dict):
        combined["meta"]["count"] = len(data)
        combined["meta"]["total-count"] = len(data)
        combined["meta"]["total-pages"] = 1
    return combined


def _extract_total_pages(payload: Mapping[str, Any]) -> int | None:
    """Best-effort read of the ``meta.total-pages`` field."""
    meta = payload.get("meta")
    if not isinstance(meta, Mapping):
        return None
    raw = meta.get("total-pages")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw >= 0 else None
    if isinstance(raw, str):
        try:
            parsed = int(raw)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None


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
        LOGGER.exception("Treasury collect failed")
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"Treasury collect failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"ok": True, "ingest_run_id": run_id}))
    else:
        print(f"Treasury collect: meta.ingest_runs id={run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
