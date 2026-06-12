"""EIA Open Data v2 collector (B-032).

Companion to the FRED (rates / credit / FX / vol), BEA (real-economy
growth), and Treasury (debt / cash / cost-of-debt) ingesters — covers
energy: oil + gas + power. v1 fetches every series listed under the
``eia:`` section of ``config/watchlists.yml``: 11 series across
petroleum (spot prices + weekly inventories + monthly crude
production), natural gas (Henry Hub spot, Lower-48 working storage,
marketed production), and electricity (US net generation).

**API contract** — REST endpoints at ``https://api.eia.gov/v2``. EIA
organizes data by *route* (e.g. ``petroleum/stoc/wstk``,
``natural-gas/stor/wkly``) and each route exposes facet filters
(``series``, ``location``, ``fueltype``, …) that select a specific
time series. Most legacy series ID's live under a ``series`` facet;
some routes (notably electricity) require multiple facet keys to pin
one series. The watchlist binds (route + facets + data_field + freq)
to a friendly local ``series_id``.

**Auth** — free API key required. Register at
``https://www.eia.gov/opendata/register.php`` and set ``EIA_API_KEY``
in your local ``.env`` or as a GitHub Actions secret. The collector
redacts the key from any URL it stores in ``meta.raw_blobs`` or any
error message it logs.

**Pagination** — EIA v2 caps responses at ``length=5000`` per request
and uses ``offset`` to page. The collector loops until either the
response's reported ``total`` is reached or a short page (< 5000 rows)
arrives. Most v1 series fit in one page (daily WTI = ~2,500 rows for
10y; weekly inventories = ~520 rows for 10y), but the loop is in
place for any future series that needs it.

**Fetch shape** — one API call per (series_id × backfill window).
Multiple series may share a route (e.g. ``petroleum/stoc/wstk``
covers WCESTUS1 / WGTSTUS1 / WDISTUS1 / WCSSTUS1) but their facets
differ. Doing one call per series keeps the request URLs simple,
keeps each blob scoped to a single watched series, and lets the
normalizer dispatch by blob name without route-level row sieving.

**Backfill window** — ``--start`` defaults to 10 years before today
(``Date.today - 3650 days``). Daily incremental runs override with
``--start`` set to the most-recent observation date if that ever
becomes a concern; today the full-history pull on every run is cheap
enough that we always re-fetch and upsert.

**Vintage** — latest-only (NOT vintage-aware). The schema's PK is
``(series_id, ts)`` with no vintage column; revisions overwrite on
upsert, matching EIA's revise-in-place semantics.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from genkei.common import db
from genkei.common.http import HttpClient, RateLimit
from genkei.common.slugs import blob_slug_part as _blob_slug_part
from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    load_watchlist,
)

SOURCE_NAME = "eia"
COLLECT_ENDPOINT_LABEL = "collect"
EIA_BASE_URL = "https://api.eia.gov/v2"
BLOB_PREFIX = "eia_"
PAGE_SIZE = 5000  # EIA v2 hard cap per response.
MAX_PAGES = 200  # 1M-row safety ceiling — current v1 series peak at one page.
DEFAULT_BACKFILL_DAYS = 365 * 10  # 10y default, matches Treasury + BEA.

# EIA's documented anonymous-tier limit is 5,000 requests/hour. With ~11
# series × ~1 page each per run we're nowhere near the cap; 2 req/s is the
# polite default we use for any keyed external API.
DEFAULT_RATE_LIMIT = RateLimit.per_second(2)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SeriesTarget:
    """One EIA series we fetch in a single (route + facets) API call.

    The watchlist may carry multiple series per route — each one is its
    own target with a distinct facet projection. The normalizer reverse-
    maps blobs back to series_ids by the blob_endpoint slug.
    """

    series_id: str
    route: str
    frequency: str
    data_field: str
    facets: Mapping[str, str]

    @property
    def blob_endpoint(self) -> str:
        """Stable endpoint name used as ``meta.raw_blobs.endpoint_name``.

        Slugified ``series_id`` keeps every (series, route) pair on its
        own row in ``meta.raw_blobs`` and lets the normalizer pick the
        right watchlist entry without round-tripping the facet dict.
        """
        return f"{BLOB_PREFIX}{_blob_slug_part(self.series_id)}"


def load_targets(path: Path = DEFAULT_WATCHLIST_PATH) -> list[SeriesTarget]:
    """Read the ``eia:`` watchlist section into deduped fetch targets.

    Watchlist series_ids are upper-cased + deduped during loading, so
    this only needs to project the typed entries into ``SeriesTarget``s.
    """
    try:
        watchlist = load_watchlist(path)
    except FileNotFoundError as exc:
        raise SystemExit(f"Watchlist file not found: {path}") from exc
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not watchlist.eia:
        raise SystemExit(
            "watchlists.yml is missing an `eia:` section or it is empty."
        )
    out: list[SeriesTarget] = []
    for entry in watchlist.eia:
        out.append(
            SeriesTarget(
                series_id=entry.series_id,
                route=entry.route,
                frequency=entry.frequency,
                data_field=entry.data_field,
                facets=dict(entry.facets),
            )
        )
    out.sort(key=lambda t: t.series_id)
    return out


def require_api_key() -> str:
    """Return ``EIA_API_KEY`` from the environment or raise."""
    key = os.environ.get("EIA_API_KEY")
    if not key:
        raise SystemExit(
            "EIA_API_KEY is not set. Register a free key at "
            "https://www.eia.gov/opendata/register.php and set it in your "
            "local .env or as a GitHub Actions secret."
        )
    return key


def build_page_url(
    target: SeriesTarget,
    *,
    api_key: str,
    start: str,
    offset: int = 0,
    length: int = PAGE_SIZE,
) -> str:
    """Build one paginated request URL for an EIA v2 series.

    Sort ascending on the series's date_field so page boundaries cannot
    duplicate or skip rows on subsequent pulls. ``urlencode`` keeps the
    param order stable across runs so ``meta.raw_blobs.url`` matches
    on identical inputs.
    """
    params: list[tuple[str, str]] = [
        ("api_key", api_key),
        ("frequency", _eia_frequency(target.frequency)),
        ("data[0]", target.data_field),
        ("start", start),
        ("sort[0][column]", "period"),
        ("sort[0][direction]", "asc"),
        ("offset", str(offset)),
        ("length", str(length)),
    ]
    for facet_key in sorted(target.facets.keys()):
        params.append((f"facets[{facet_key}][]", target.facets[facet_key]))
    route_path = target.route.strip("/")
    return f"{EIA_BASE_URL}/{route_path}/data/?{urlencode(params)}"


def _eia_frequency(freq: str) -> str:
    """Map our single-char frequency to EIA's verbose form."""
    return {
        "D": "daily",
        "W": "weekly",
        "M": "monthly",
        "Q": "quarterly",
        "A": "annual",
    }[freq.upper()]


def _redact_key(url: str, api_key: str) -> str:
    """Replace the API key in a URL with ``***`` so raw_blobs.url is safe to log."""
    return url.replace(api_key, "***") if api_key else url


def _default_start_date(today: date | None = None) -> str:
    """Return the default backfill start date (10 years before today, ISO)."""
    base = today or date.today()
    return (base - timedelta(days=DEFAULT_BACKFILL_DAYS)).isoformat()


def collect(
    config_path: Path = DEFAULT_WATCHLIST_PATH,
    *,
    http: HttpClient | None = None,
    api_key: str | None = None,
    start: str | None = None,
) -> int:
    """Run the EIA collector once and return the meta.ingest_runs id.

    ``api_key`` is injectable for testing; production reads
    ``EIA_API_KEY``. ``start`` is an ISO date string overriding the
    10-year default backfill window.
    """
    targets = load_targets(config_path)
    key = api_key or require_api_key()
    start_iso = start or _default_start_date()

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
                "series_count": len(targets),
                "start": start_iso,
            },
        ) as run:
            written = 0
            for i, target in enumerate(targets, start=1):
                written += _fetch_target(target, key, start_iso, http, run.id, failures)
                if i % 3 == 0:
                    LOGGER.info("EIA collect progress: %s/%s", i, len(targets))
            run.add_rows(written)
            if failures:
                db.record_partial_endpoints(run.id, failures)
                raise RuntimeError(
                    f"EIA fetch failed for {len(failures)} series; "
                    "no partial snapshot will be normalized."
                )
            return run.id
    finally:
        if owns_http:
            http.close()


def _fetch_target(
    target: SeriesTarget,
    api_key: str,
    start: str,
    http: HttpClient,
    ingest_run_id: int,
    failures: list[dict[str, str]],
) -> int:
    """Fetch every page for one series, land a single combined blob."""
    first_url = build_page_url(target, api_key=api_key, start=start)
    redacted_first = _redact_key(first_url, api_key)
    try:
        combined = _fetch_all_pages(target, api_key, start, http)
    except (
        httpx.TimeoutException,
        httpx.NetworkError,
        httpx.HTTPStatusError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        safe_error = _redact_key(str(exc), api_key)
        LOGGER.warning("EIA fetch failed for %s: %s", target.blob_endpoint, safe_error)
        failures.append(
            {
                "name": target.blob_endpoint,
                "url": redacted_first,
                "error": safe_error,
            }
        )
        return 0
    db.store_raw_blob(
        ingest_run_id,
        target.blob_endpoint,
        redacted_first,
        combined,
    )
    return 1


def _fetch_all_pages(
    target: SeriesTarget,
    api_key: str,
    start: str,
    http: HttpClient,
) -> dict[str, Any]:
    """Page through every observation for one series, return a merged payload.

    EIA v2 wraps responses as ``{"response": {"total": "N", "data":
    [...], "warnings": [...], ...}, "request": {...}, "apiVersion":
    "..."}``. Pagination is via ``offset`` + ``length``. Loop bounds:

    1. If ``response.total`` parses as an int, accumulate rows until
       we've fetched ``total`` of them — that's the source of truth.
    2. Otherwise fall back to "short page wins": stop when a page
       returns < PAGE_SIZE rows. Defensive; EIA usually returns total.
    3. MAX_PAGES caps the loop at 1M rows.
    """
    combined: dict[str, Any] | None = None
    data: list[Any] = []
    total: int | None = None
    offset = 0

    for page_number in range(1, MAX_PAGES + 1):
        url = build_page_url(
            target, api_key=api_key, start=start, offset=offset, length=PAGE_SIZE
        )
        payload = http.get_json(url)
        if not isinstance(payload, dict):
            raise ValueError(
                f"EIA payload for {target.series_id} page {page_number} "
                "is not an object."
            )
        response_block = payload.get("response")
        if not isinstance(response_block, dict):
            raise ValueError(
                f"EIA payload for {target.series_id} page {page_number} "
                "is missing a `response` object."
            )
        page_data = response_block.get("data")
        if not isinstance(page_data, list):
            raise ValueError(
                f"EIA payload for {target.series_id} page {page_number} "
                "is missing a `response.data` array."
            )

        if combined is None:
            # First-page envelope becomes the canonical blob shape; we
            # overwrite ``response.data`` after the loop with the merged
            # list. URL is intentionally NOT stored inside the payload
            # (redacted URL goes to meta.raw_blobs.url separately).
            combined = {
                "request": payload.get("request"),
                "apiVersion": payload.get("apiVersion"),
                "response": dict(response_block),
            }
            total = _extract_total(response_block)

        data.extend(page_data)

        if total is not None:
            if len(data) >= total:
                break
        elif len(page_data) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    else:
        raise ValueError(
            f"EIA payload for {target.series_id} exceeded MAX_PAGES "
            f"({MAX_PAGES}); increase the cap or paginate differently."
        )

    if combined is None:
        # MAX_PAGES > 0 so unreachable in practice; guard for the type checker.
        raise ValueError(f"EIA payload for {target.series_id} was empty.")
    combined["response"]["data"] = data
    combined["response"]["total"] = len(data)
    return combined


def _extract_total(response_block: Mapping[str, Any]) -> int | None:
    """Best-effort read of ``response.total`` (EIA sometimes returns it as a str)."""
    raw = response_block.get("total")
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
        "--start",
        type=str,
        default=None,
        help="ISO date (YYYY-MM-DD) for backfill window start. Defaults to 10y ago.",
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
        run_id = collect(config_path=args.config, start=args.start)
    except SystemExit as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
            return 1
        raise
    except Exception as exc:
        LOGGER.exception("EIA collect failed")
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"EIA collect failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"ok": True, "ingest_run_id": run_id}))
    else:
        print(f"EIA collect: meta.ingest_runs id={run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
