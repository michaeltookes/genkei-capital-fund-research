"""FRED macro-series collector (B-028).

Fetches series metadata + full-vintage observations for every series
configured under ``macro_series:`` in ``config/watchlists.yml``. Lands
two raw blobs per series (``series_<id>``, ``observations_<id>``) in
``meta.raw_blobs``. The downstream normalizer
(``genkei.normalize.fred``) reads from those blobs.

Single-mode design (D-014): FRED's ``/series/observations`` returns the
full series in one call, so daily and backfill flows are the same code
path. Each daily run upserts the latest state of every observation
including any new vintages — D-013's vintage-aware schema means new
revisions land as new rows rather than overwriting historical values.

Vintage handling: observations calls use explicit ``vintage_dates``
chunks from ``/series/vintagedates`` with ``output_type=3`` (new and
revised observations). Passing the full-history realtime window
``realtime_start=1776-07-04&realtime_end=9999-12-31`` returns 400 Bad
Request on long daily series because FRED caps JSON responses at 2000
vintage dates, while omitting realtime params defaults to today's
realtime period and would duplicate current snapshots under D-013's
``(series_id, ts, realtime_start)`` PK.

API key: the free FRED API key lives in the ``FRED_API_KEY`` env var.
Register at https://fredaccount.stlouisfed.org/apikeys.
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
from genkei.common.watchlist import DEFAULT_WATCHLIST_PATH, load_watchlist
SOURCE_NAME = "fred"
COLLECT_ENDPOINT_LABEL = "collect"
FRED_BASE_URL = "https://api.stlouisfed.org/fred"
SERIES_BLOB_PREFIX = "series_"
OBSERVATIONS_BLOB_PREFIX = "observations_"
# Free-tier rate limit is documented at 120 req/min. We stay well under
# (1 req / sec) since 20 series × 2 calls = 40 calls per run completes
# in under a minute either way.
DEFAULT_RATE_LIMIT = RateLimit.per_second(1)
# Earliest documented FRED realtime_start.
EARLIEST_REALTIME = "1776-07-04"
LATEST_REALTIME = "9999-12-31"
OBSERVATIONS_PAGE_LIMIT = 100_000
VINTAGE_DATES_PAGE_LIMIT = 10_000
VINTAGE_DATES_CHUNK_SIZE = 500
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SeriesTarget:
    """A FRED series we want to fetch."""

    series_id: str
    name: str
    rationale: str | None = None


def load_series(path: Path) -> list[SeriesTarget]:
    """Read ``macro_series:`` from watchlists.yml as ``SeriesTarget``s.

    Rejects duplicate series IDs since the FRED collector keys on them and
    a duplicate would silently double-fetch.
    """
    try:
        watchlist = load_watchlist(path)
    except FileNotFoundError as exc:
        raise SystemExit(f"Watchlist file not found: {path}") from exc
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not watchlist.macro:
        raise SystemExit("watchlists.yml is missing macro_series or it is empty.")
    out: list[SeriesTarget] = []
    seen_ids: set[str] = set()
    for entry in watchlist.macro:
        if entry.series_id in seen_ids:
            raise SystemExit(f"Duplicate macro_series id: {entry.series_id}")
        seen_ids.add(entry.series_id)
        if not entry.name:
            raise SystemExit(
                f"macro_series entry {entry.series_id!r} is missing a string `name`."
            )
        out.append(
            SeriesTarget(
                series_id=entry.series_id,
                name=entry.name,
                rationale=entry.rationale,
            )
        )
    return out


def require_api_key() -> str:
    """Return ``FRED_API_KEY`` from the environment or raise."""
    key = os.environ.get("FRED_API_KEY")
    if not key:
        raise SystemExit(
            "FRED_API_KEY is not set. Register a free key at "
            "https://fredaccount.stlouisfed.org/apikeys and set it in your "
            "local .env or as a GitHub Actions secret."
        )
    return key


def build_series_url(api_key: str, series_id: str) -> str:
    """Build the URL for the series-metadata endpoint."""
    return f"{FRED_BASE_URL}/series?series_id={series_id}&api_key={api_key}&file_type=json"


def build_vintage_dates_url(
    api_key: str,
    series_id: str,
    *,
    limit: int = VINTAGE_DATES_PAGE_LIMIT,
    offset: int = 0,
) -> str:
    """Build the URL for the vintage-dates endpoint."""
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "limit": str(limit),
        "offset": str(offset),
    }
    return f"{FRED_BASE_URL}/series/vintagedates?{urlencode(params)}"


def build_observations_url(
    api_key: str,
    series_id: str,
    *,
    vintage_dates: list[str],
    limit: int = OBSERVATIONS_PAGE_LIMIT,
    offset: int = 0,
) -> str:
    """Build the URL for the observations endpoint.

    Uses explicit ``vintage_dates`` instead of FRED's default realtime
    period. Leaving ``realtime_start`` / ``realtime_end`` unset defaults
    to today's realtime period, which would produce daily snapshot
    duplicates under the vintage-aware PK. A single all-history realtime
    window hits the 2000-vintage JSON cap for long daily series, so the
    collector asks for bounded vintage-date chunks instead.
    """
    if not vintage_dates:
        raise ValueError("vintage_dates must contain at least one date")
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "limit": str(limit),
        "offset": str(offset),
        "output_type": "3",
        "vintage_dates": ",".join(vintage_dates),
    }
    return f"{FRED_BASE_URL}/series/observations?{urlencode(params, safe=',')}"


def _redact_key(url: str, api_key: str) -> str:
    """Replace the API key in a URL with ``***`` so raw_blobs.url is safe to log."""
    return url.replace(api_key, "***") if api_key else url


def _as_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def collect(
    config_path: Path = DEFAULT_WATCHLIST_PATH,
    *,
    http: HttpClient | None = None,
    api_key: str | None = None,
) -> int:
    """Run the FRED collector once and return the meta.ingest_runs id.

    `api_key` is injectable for testing; production reads ``FRED_API_KEY``.
    """
    series = load_series(config_path)
    key = api_key or require_api_key()

    owns_http = http is None
    if http is None:
        http = HttpClient(SOURCE_NAME, rate_limit=DEFAULT_RATE_LIMIT)

    failures: list[dict[str, str]] = []
    try:
        with db.ingest_run(
            SOURCE_NAME,
            endpoint=COLLECT_ENDPOINT_LABEL,
            metadata={"watchlist_path": str(config_path), "series_count": len(series)},
        ) as run:
            written = 0
            for target_index, target in enumerate(series, start=1):
                written += _fetch_series_pair(target, key, http, run.id, failures)
                if target_index % 5 == 0:
                    LOGGER.info("FRED collect progress: %s/%s", target_index, len(series))
            run.add_rows(written)
            if failures:
                _record_partial(run.id, failures)
                raise RuntimeError(
                    f"FRED fetch failed for {len(failures)} endpoint(s); "
                    "no partial macro snapshot will be normalized."
                )
            return run.id
    finally:
        if owns_http:
            http.close()


def _fetch_series_pair(
    target: SeriesTarget,
    api_key: str,
    http: HttpClient,
    ingest_run_id: int,
    failures: list[dict[str, str]],
) -> int:
    """Fetch metadata + observations for one series. Returns 0/1/2 rows written."""
    written = 0
    for blob_prefix in (SERIES_BLOB_PREFIX, OBSERVATIONS_BLOB_PREFIX):
        endpoint_name = f"{blob_prefix}{target.series_id}"
        try:
            if blob_prefix == SERIES_BLOB_PREFIX:
                url = build_series_url(api_key, target.series_id)
                payload = http.get_json(url)
            else:
                url, payload = _fetch_observations_payload(target, api_key, http)
        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.HTTPStatusError,
            json.JSONDecodeError,
        ) as exc:
            safe_error = _redact_key(str(exc), api_key)
            LOGGER.warning("FRED fetch failed for %s: %s", endpoint_name, safe_error)
            failures.append(
                {"name": endpoint_name, "url": _redact_key(url, api_key), "error": safe_error}
            )
            continue
        # Persist the redacted URL so raw_blobs.url can't leak the key.
        db.store_raw_blob(ingest_run_id, endpoint_name, _redact_key(url, api_key), payload)
        written += 1
    return written


def _fetch_observations_payload(
    target: SeriesTarget,
    api_key: str,
    http: HttpClient,
) -> tuple[str, Any]:
    """Fetch every vintage-date observation page and return one combined raw payload."""
    vintage_dates = _fetch_vintage_dates(target, api_key, http)
    first_chunk = vintage_dates[:VINTAGE_DATES_CHUNK_SIZE]
    first_url = build_observations_url(
        api_key,
        target.series_id,
        vintage_dates=first_chunk,
        limit=OBSERVATIONS_PAGE_LIMIT,
        offset=0,
    )
    combined: dict[str, Any] | None = None
    observations: list[Any] = []
    for vintage_chunk in _chunks(vintage_dates, VINTAGE_DATES_CHUNK_SIZE):
        offset = 0
        expected_count: int | None = None
        chunk_observations = 0

        while True:
            url = build_observations_url(
                api_key,
                target.series_id,
                vintage_dates=vintage_chunk,
                limit=OBSERVATIONS_PAGE_LIMIT,
                offset=offset,
            )
            payload = http.get_json(url)
            if not isinstance(payload, dict):
                raise ValueError(
                    f"FRED observations payload for {target.series_id} is not an object."
                )
            page_observations = payload.get("observations")
            if not isinstance(page_observations, list):
                raise ValueError(
                    f"FRED observations payload for {target.series_id} is missing observations."
                )

            if combined is None:
                combined = dict(payload)
                combined["offset"] = 0
            page_count = _as_non_negative_int(payload.get("count"))
            expected_count = page_count if expected_count is None else expected_count
            observations.extend(page_observations)
            chunk_observations += len(page_observations)

            if expected_count is not None and chunk_observations >= expected_count:
                break
            if len(page_observations) < OBSERVATIONS_PAGE_LIMIT:
                if expected_count is not None and chunk_observations < expected_count:
                    raise ValueError(
                        f"FRED observations payload for {target.series_id} ended after "
                        f"{chunk_observations} of {expected_count} rows."
                    )
                break
            offset += OBSERVATIONS_PAGE_LIMIT

    if combined is None:
        raise ValueError(f"FRED observations payload for {target.series_id} was empty.")
    combined["observations"] = observations
    combined["count"] = len(observations)
    combined["limit"] = len(observations)
    return first_url, combined


def _fetch_vintage_dates(target: SeriesTarget, api_key: str, http: HttpClient) -> list[str]:
    """Fetch every vintage date for a series."""
    vintage_dates: list[str] = []
    offset = 0
    expected_count: int | None = None

    while True:
        url = build_vintage_dates_url(
            api_key, target.series_id, limit=VINTAGE_DATES_PAGE_LIMIT, offset=offset
        )
        payload = http.get_json(url)
        if not isinstance(payload, dict):
            raise ValueError(f"FRED vintage-dates payload for {target.series_id} is not an object.")
        page_dates = payload.get("vintage_dates")
        if not isinstance(page_dates, list):
            raise ValueError(
                f"FRED vintage-dates payload for {target.series_id} is missing vintage_dates."
            )
        if any(not isinstance(item, str) or not item for item in page_dates):
            raise ValueError(
                f"FRED vintage-dates payload for {target.series_id} contains invalid dates."
            )

        page_count = _as_non_negative_int(payload.get("count"))
        expected_count = page_count if expected_count is None else expected_count
        vintage_dates.extend(page_dates)

        if expected_count is not None and len(vintage_dates) >= expected_count:
            break
        if len(page_dates) < VINTAGE_DATES_PAGE_LIMIT:
            if expected_count is not None and len(vintage_dates) < expected_count:
                raise ValueError(
                    f"FRED vintage-dates payload for {target.series_id} ended after "
                    f"{len(vintage_dates)} of {expected_count} rows."
                )
            break
        offset += VINTAGE_DATES_PAGE_LIMIT

    if not vintage_dates:
        raise ValueError(f"FRED vintage-dates payload for {target.series_id} was empty.")
    return vintage_dates


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _record_partial(ingest_run_id: int, partial: list[dict[str, str]]) -> None:
    """Stash per-series partial-failure metadata on the ingest_runs row."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE meta.ingest_runs SET metadata = "
            "COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('partial_endpoints', %s::jsonb) "
            "WHERE id = %s",
            [json.dumps(partial), ingest_run_id],
        )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect FRED macro-series snapshots into Postgres."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_WATCHLIST_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv or sys.argv[1:])
    run_id = collect(args.config)
    print(f"FRED collector wrote ingest_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
