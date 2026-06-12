"""Treasury Fiscal Data normalizer — reads meta.raw_blobs, upserts treasury.* (B-030).

A normalizer run is itself a row in ``meta.ingest_runs`` with
``endpoint='normalize'`` and ``metadata.source_run_id`` pointing at the
collector run whose blobs were processed (same audit pattern as FRED's
B-028 and BEA's B-029 normalizers).

**Per-blob dispatch** — each blob's ``endpoint_name`` (e.g.
``treasury_v2_accounting_od_debt_to_penny__record_date``) reverse-maps to
its Fiscal Data endpoint + date-field tuple. The normalizer reads the
watchlist, indexes every series by that tuple, and for each blob picks
out the watched series + their (value_field, row_filter) projection.

**Watchlist filter/aggregate at parse time** — Treasury endpoints return many
fields per row and may include many rows per ``record_date`` (e.g.
``operating_cash_balance`` returns 25+ rows per day, one per
account_type). The watchlist curates the specific (value_field +
row_filter) tuples we care about; the normalizer applies the filter,
optionally sums multi-line endpoints, and extracts one observation per
series per period. Watchlist series with no matching rows in a blob
raise — that points to a Treasury contract change (renamed account_type,
dropped column) or a watchlist typo. Catching it loud is the point.

**Vintage** — latest-only (NOT vintage-aware). ``treasury.observations``
PK is ``(series_id, ts)``; re-running this normalizer upserts new
values over old ones, matching Treasury's own revise-in-place
semantics. See the migration docstring for the v2 vintage-aware path.

**Date parsing** — Fiscal Data publishes dates as ``YYYY-MM-DD``
strings. The normalizer parses each to a UTC midnight timestamp so
the hypertable's ``ts`` column always points at the start of the
reporting period.

**Numeric values** — Treasury fields come as strings, often with
thousands separators (``"36,176,659,847,936.05"``) and the literal
sentinel ``null`` for unavailable rows. ``parse_value`` handles both
plus the rare scientific-notation form some derived fields use.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from genkei.common import db
from genkei.common.slugs import blob_slug_part as _blob_slug_part
from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    TreasurySeriesEntry,
    load_watchlist,
)

SOURCE_NAME = "treasury"
NORMALIZE_ENDPOINT_LABEL = "normalize"
COLLECT_ENDPOINT_LABEL = "collect"
BLOB_PREFIX = "treasury_"
RawBlob = tuple[str, Any, datetime]
JsonObject = dict[str, Any]
SourceRunRow = tuple[Any, Any, Any, Any]
LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def parse_value(raw: Any) -> float | None:
    """Coerce a Treasury Fiscal Data field to ``float``.

    Treasury publishes most numeric fields as strings, often with
    thousands separators (``"36,176,659,847,936.05"``) and a literal
    ``"null"`` sentinel for missing values. Booleans, None, and the
    empty string all collapse to None rather than raising.
    """
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped or stripped.lower() in ("null", "n/a", "na", "-"):
            return None
        cleaned = stripped.replace(",", "")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def parse_record_date(raw: Any) -> datetime | None:
    """Parse Treasury's ``YYYY-MM-DD`` record_date strings to UTC midnight.

    Returns None for unparseable inputs. Treasury also occasionally
    emits already-typed dates from the Fiscal Data Python client; the
    isoformat fallback handles those.
    """
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            return raw.replace(tzinfo=timezone.utc)
        return raw.astimezone(timezone.utc)
    if isinstance(raw, date):
        return _ts(raw)
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        parsed = datetime.strptime(stripped, "%Y-%m-%d").date()
    except ValueError:
        return None
    return _ts(parsed)


def _ts(d: date) -> datetime:
    return datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)


def row_matches_filter(row: Mapping[str, Any], row_filter: Mapping[str, str]) -> bool:
    """True if every field in ``row_filter`` matches the row case-sensitively.

    Treasury uses the literal account_type / security_desc strings the
    watchlist tracks; we compare with ``str(...)`` to coerce
    occasional numeric or null values into a comparable form.
    """
    for key, expected in row_filter.items():
        actual = row.get(key)
        if actual is None or str(actual) != expected:
            return False
    return True


# ---------------------------------------------------------------------------
# Per-endpoint normalizer
# ---------------------------------------------------------------------------


def normalize_endpoint(
    payload: Any,
    *,
    series: list[TreasurySeriesEntry],
    source_endpoint: str,
    ingest_run_id: int,
    fetched_at: datetime,
) -> tuple[list[JsonObject], list[JsonObject]]:
    """Parse one Treasury endpoint blob into (series_rows, observation_rows).

    Returns rows for the *watched* series only. Within a blob, multiple
    series may share the same endpoint — each is filtered independently
    and produces its own ``(series_id, ts)`` observations. Every watched
    series must match the endpoint's latest parseable period; this keeps
    forward-looking source contract drift from being hidden by older
    historical matches in the same full-history blob. Per-(series, ts)
    dedup: by default the last matching row wins (rare; Treasury
    occasionally publishes intraday updates that get superseded by the
    same blob). Series with ``aggregate="sum"`` instead add all matching
    numeric values for the same period.
    """
    if not isinstance(payload, dict):
        raise ValueError(
            f"Treasury payload for endpoint {source_endpoint} is not a JSON object."
        )
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError(
            f"Treasury payload for endpoint {source_endpoint} has invalid/missing "
            "`data` list."
        )
    unsupported_aggregates = sorted(
        {entry.aggregate for entry in series if entry.aggregate not in (None, "sum")}
    )
    if unsupported_aggregates:
        names = ", ".join(str(value) for value in unsupported_aggregates)
        raise ValueError(f"Unsupported Treasury aggregate mode(s): {names}")

    accepted: set[str] = set()
    series_by_id: dict[str, JsonObject] = {}
    observations_by_key: dict[tuple[str, datetime], JsonObject] = {}
    latest_ts_by_field: dict[str, datetime] = {}
    date_fields = {entry.date_field for entry in series}

    for raw_row in data:
        if not isinstance(raw_row, dict):
            continue
        for date_field in date_fields:
            ts = parse_record_date(raw_row.get(date_field))
            if ts is None:
                continue
            current = latest_ts_by_field.get(date_field)
            if current is None or ts > current:
                latest_ts_by_field[date_field] = ts

    for raw_row in data:
        if not isinstance(raw_row, dict):
            continue
        for entry in series:
            if entry.row_filter and not row_matches_filter(
                raw_row, entry.row_filter
            ):
                continue
            ts = parse_record_date(raw_row.get(entry.date_field))
            if ts is None:
                continue
            if entry.value_field not in raw_row:
                raise ValueError(
                    f"Treasury response for {entry.endpoint} is missing value field "
                    f"{entry.value_field!r} for series {entry.series_id}."
                )
            raw_value = raw_row[entry.value_field]
            value = parse_value(raw_value)

            accepted.add(entry.series_id)
            series_by_id[entry.series_id] = {
                "series_id": entry.series_id,
                "name": entry.name,
                "endpoint": entry.endpoint,
                "value_field": entry.value_field,
                "date_field": entry.date_field,
                "row_filter": Jsonb(dict(entry.row_filter))
                if entry.row_filter
                else None,
                "units": entry.units,
                "frequency": entry.frequency,
                "rationale": entry.rationale,
                "source_endpoint": source_endpoint,
                "fetched_at": fetched_at,
                "ingest_run_id": ingest_run_id,
            }
            observation_key = (entry.series_id, ts)
            observation = observations_by_key.get(observation_key)
            if entry.aggregate == "sum" and observation is not None:
                if value is not None:
                    if observation["value"] is None:
                        observation["value"] = value
                    else:
                        observation["value"] += value
                continue
            observations_by_key[observation_key] = {
                "series_id": entry.series_id,
                "ts": ts,
                "value": value,
                "source_endpoint": source_endpoint,
                "fetched_at": fetched_at,
                "ingest_run_id": ingest_run_id,
            }

    missing = {e.series_id for e in series} - accepted
    if missing:
        names = ", ".join(sorted(missing))
        endpoints = ", ".join(sorted({e.endpoint for e in series}))
        raise ValueError(
            f"Treasury response for endpoint(s) {endpoints} matched no rows "
            f"for series: {names}"
        )

    missing_latest = []
    for entry in series:
        latest_ts = latest_ts_by_field.get(entry.date_field)
        if latest_ts is None:
            continue
        if (entry.series_id, latest_ts) not in observations_by_key:
            missing_latest.append(f"{entry.series_id} at {latest_ts.date().isoformat()}")
    if missing_latest:
        names = ", ".join(sorted(missing_latest))
        endpoints = ", ".join(sorted({e.endpoint for e in series}))
        raise ValueError(
            f"Treasury response for endpoint(s) {endpoints} matched no latest-period "
            f"rows for series: {names}"
        )

    return list(series_by_id.values()), list(observations_by_key.values())


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def latest_collector_run_id() -> int:
    """Return the most recent successful Treasury collector run id."""
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
            "No successful Treasury collector run found in meta.ingest_runs. "
            "Run `python -m genkei.ingest.treasury` first."
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
    return {
        name: (url, payload, fetched_at)
        for name, url, payload, fetched_at in rows
    }


def validate_source_run(source_run_id: int) -> None:
    """Fail unless ``source_run_id`` points at a complete Treasury collect run."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT source, endpoint, status, metadata "
            "FROM meta.ingest_runs WHERE id = %s",
            [source_run_id],
        )
        row = cur.fetchone()
    _validate_source_run_row(source_run_id, row)


def _validate_source_run_row(
    source_run_id: int,
    row: SourceRunRow | None,
) -> None:
    if row is None:
        raise SystemExit(
            f"No Treasury collector run found for ingest_run_id={source_run_id}."
        )

    source, endpoint, status, metadata = row
    if source != SOURCE_NAME or endpoint != COLLECT_ENDPOINT_LABEL:
        raise SystemExit(
            f"ingest_run_id={source_run_id} is not a Treasury collect run "
            f"(source={source!r}, endpoint={endpoint!r})."
        )
    if status != "success":
        raise SystemExit(
            f"Treasury source run {source_run_id} is not successful "
            f"(status={status!r})."
        )

    partial_names = _partial_endpoint_names(metadata)
    if partial_names:
        names = ", ".join(partial_names)
        raise SystemExit(
            f"Treasury source run {source_run_id} has partial endpoint failure(s): "
            f"{names}"
        )


def _partial_endpoint_names(metadata: Any) -> list[str]:
    if not isinstance(metadata, dict):
        return []
    partial = metadata.get("partial_endpoints")
    if not isinstance(partial, list):
        return []

    names: list[str] = []
    for item in partial:
        if isinstance(item, dict):
            name = item.get("name")
            names.append(str(name) if name else "<unknown>")
        else:
            names.append(str(item))
    return names


def _series_by_endpoint(
    config_path: Path | str,
) -> dict[tuple[str, str], list[TreasurySeriesEntry]]:
    """Group watchlist series by endpoint path and date field.

    Returns ``{(endpoint, date_field): [TreasurySeriesEntry, ...]}`` for use by
    the per-blob dispatch loop. The tuple mirrors the collector's target key so
    distinct pulls from the same endpoint cannot overwrite each other.
    """
    resolved = config_path if isinstance(config_path, Path) else Path(config_path)
    watchlist = load_watchlist(resolved)
    out: dict[tuple[str, str], list[TreasurySeriesEntry]] = {}
    for entry in watchlist.treasury:
        out.setdefault((entry.endpoint, entry.date_field), []).append(entry)
    return out


def _endpoint_to_blob_name(endpoint: str, date_field: str = "record_date") -> str:
    """Mirror ``ingest.treasury.EndpointTarget.blob_endpoint`` slug logic."""
    endpoint_slug = _blob_slug_part(endpoint)
    date_slug = _blob_slug_part(date_field)
    return f"{BLOB_PREFIX}{endpoint_slug}__{date_slug}"


def _validate_blob_coverage(
    source_run_id: int,
    blobs: Mapping[str, RawBlob],
    expected_endpoints: set[str],
) -> None:
    missing = expected_endpoints - set(blobs)
    if missing:
        names = ", ".join(sorted(missing))
        raise SystemExit(
            f"Treasury source run {source_run_id} missing raw blob endpoint(s): "
            f"{names}"
        )


def normalize(
    *,
    source_run_id: int | None = None,
    config_path: Path | str = DEFAULT_WATCHLIST_PATH,
) -> int:
    """Run the Treasury normalizer once and return the normalizer run id."""
    if source_run_id is None:
        source_run_id = latest_collector_run_id()
    validate_source_run(source_run_id)
    grouped = _series_by_endpoint(config_path)
    if not grouped:
        raise SystemExit(
            "watchlists.yml is missing a `treasury:` section or it is empty."
        )
    expected_blob_endpoints = {
        _endpoint_to_blob_name(endpoint, date_field)
        for endpoint, date_field in grouped
    }
    blobs = fetch_raw_blobs(source_run_id)
    _validate_blob_coverage(source_run_id, blobs, expected_blob_endpoints)

    with db.ingest_run(
        SOURCE_NAME,
        endpoint=NORMALIZE_ENDPOINT_LABEL,
        metadata={"source_run_id": source_run_id},
    ) as run:
        series_rows: list[JsonObject] = []
        observation_rows: list[JsonObject] = []
        blob_endpoint_to_target = {
            _endpoint_to_blob_name(endpoint, date_field): (endpoint, date_field)
            for endpoint, date_field in grouped
        }

        for endpoint_name, (url, payload, fetched_at) in blobs.items():
            target = blob_endpoint_to_target.get(endpoint_name)
            if target is None:
                LOGGER.debug(
                    "Treasury normalizer skipping unrecognized blob: %s",
                    endpoint_name,
                )
                continue
            series_for_endpoint = grouped[target]
            endpoint_series, endpoint_observations = normalize_endpoint(
                payload,
                series=series_for_endpoint,
                source_endpoint=url,
                ingest_run_id=run.id,
                fetched_at=fetched_at,
            )
            series_rows.extend(endpoint_series)
            observation_rows.extend(endpoint_observations)

        # Dedup series rows across blobs — every series_id is keyed on
        # one endpoint so this is just a defensive guard against
        # accidental watchlist duplication.
        series_dedup: dict[str, JsonObject] = {
            row["series_id"]: row for row in series_rows
        }

        with db.connection() as conn:
            run.add_rows(
                db.bulk_upsert(
                    conn,
                    "treasury.series",
                    list(series_dedup.values()),
                    conflict_keys=["series_id"],
                )
            )
            run.add_rows(
                db.bulk_upsert(
                    conn,
                    "treasury.observations",
                    observation_rows,
                    conflict_keys=["series_id", "ts"],
                )
            )
        return run.id


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-run-id",
        type=int,
        help="Specific collector run to normalize (default: latest success).",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_WATCHLIST_PATH),
        help="Watchlist path (drives the per-endpoint series filter).",
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
        run_id = normalize(
            source_run_id=args.source_run_id,
            config_path=Path(args.config),
        )
    except SystemExit:
        raise
    except Exception as exc:
        LOGGER.exception("Treasury normalize failed")
        if args.json:
            import json as json_mod

            print(json_mod.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"Treasury normalize failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        import json as json_mod

        print(json_mod.dumps({"ok": True, "normalize_run_id": run_id}))
    else:
        print(f"Treasury normalize: meta.ingest_runs id={run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
