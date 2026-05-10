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

Vintage handling: every observations call fetches the *latest vintage*
only (no ``realtime_start`` / ``realtime_end`` params). The schema is
vintage-aware (D-013) so daily runs that find a revised value land it
as a new row keyed on the fresh ``realtime_start`` returned by FRED.
The full pre-existing revision history is forfeit because FRED's JSON
file type caps responses at 2000 vintage dates — see G-019.

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

import httpx
import yaml

from genkei.common import db
from genkei.common.http import HttpClient, RateLimit

DEFAULT_WATCHLIST_PATH = Path("config/watchlists.yml")
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
OBSERVATIONS_PAGE_LIMIT = 100_000
RAW_BLOBS_INSERT = (
    "INSERT INTO meta.raw_blobs (ingest_run_id, endpoint_name, url, payload) "
    "VALUES (%s, %s, %s, %s::jsonb) "
    "ON CONFLICT (ingest_run_id, endpoint_name) DO NOTHING"
)
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SeriesTarget:
    """A FRED series we want to fetch."""

    series_id: str
    name: str
    rationale: str | None = None


def load_series(path: Path) -> list[SeriesTarget]:
    """Read ``macro_series:`` from watchlists.yml as ``SeriesTarget``s."""
    try:
        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise SystemExit(f"Watchlist file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise SystemExit(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("Watchlist root must be a YAML mapping.")
    raw = data.get("macro_series", [])
    if not isinstance(raw, list) or not raw:
        raise SystemExit("watchlists.yml is missing macro_series or it is empty.")
    out: list[SeriesTarget] = []
    seen_ids: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise SystemExit("Each macro_series entry must be a mapping.")
        sid = entry.get("id")
        name = entry.get("name")
        if not isinstance(sid, str) or not sid:
            raise SystemExit("macro_series entry is missing a string `id`.")
        if sid in seen_ids:
            raise SystemExit(f"Duplicate macro_series id: {sid}")
        seen_ids.add(sid)
        if not isinstance(name, str) or not name:
            raise SystemExit(f"macro_series entry {sid!r} is missing a string `name`.")
        rationale = entry.get("rationale")
        out.append(
            SeriesTarget(
                series_id=sid,
                name=name,
                rationale=rationale if isinstance(rationale, str) else None,
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


def build_observations_url(
    api_key: str,
    series_id: str,
    *,
    limit: int = OBSERVATIONS_PAGE_LIMIT,
    offset: int = 0,
) -> str:
    """Build the URL for the full-vintage observations endpoint."""
    return (
        f"{FRED_BASE_URL}/series/observations"
        f"?series_id={series_id}"
        f"&api_key={api_key}"
        f"&file_type=json"
        f"&realtime_start={EARLIEST_REALTIME}"
        f"&realtime_end=9999-12-31"
        f"&limit={limit}"
        f"&offset={offset}"
    )


def _store_blob(ingest_run_id: int, endpoint_name: str, url: str, payload: Any) -> None:
    """Insert one raw_blobs row, redacting the API key from the stored URL."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(RAW_BLOBS_INSERT, [ingest_run_id, endpoint_name, url, json.dumps(payload)])


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
        _store_blob(ingest_run_id, endpoint_name, _redact_key(url, api_key), payload)
        written += 1
    return written


def _fetch_observations_payload(
    target: SeriesTarget,
    api_key: str,
    http: HttpClient,
) -> tuple[str, Any]:
    """Fetch every observations page and return one combined raw payload."""
    first_url = build_observations_url(
        api_key, target.series_id, limit=OBSERVATIONS_PAGE_LIMIT, offset=0
    )
    combined: dict[str, Any] | None = None
    observations: list[Any] = []
    offset = 0
    expected_count: int | None = None

    while True:
        url = build_observations_url(
            api_key, target.series_id, limit=OBSERVATIONS_PAGE_LIMIT, offset=offset
        )
        payload = http.get_json(url)
        if not isinstance(payload, dict):
            raise ValueError(f"FRED observations payload for {target.series_id} is not an object.")
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

        if expected_count is not None and len(observations) >= expected_count:
            break
        if len(page_observations) < OBSERVATIONS_PAGE_LIMIT:
            if expected_count is not None and len(observations) < expected_count:
                raise ValueError(
                    f"FRED observations payload for {target.series_id} ended after "
                    f"{len(observations)} of {expected_count} rows."
                )
            break
        offset += OBSERVATIONS_PAGE_LIMIT

    if combined is None:
        raise ValueError(f"FRED observations payload for {target.series_id} was empty.")
    combined["observations"] = observations
    combined["count"] = expected_count if expected_count is not None else len(observations)
    combined["limit"] = len(observations)
    return first_url, combined


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
