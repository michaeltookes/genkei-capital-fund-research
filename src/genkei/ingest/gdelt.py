"""GDELT 2.0 GKG news/event ingester (B-033).

Fetches the firehose GKG (Global Knowledge Graph) CSV files at
``https://data.gdeltproject.org/gdeltv2/<YYYYMMDDHHMMSS>.gkg.csv.zip``
(published every 15 min, 96 files/day), parses the V2.1 tab-separated
rows, filters to articles mentioning any watchlist asset, and bulk-
upserts into ``gdelt.gkg``.

Each fetched 15-min CSV lands in ``meta.raw_blobs`` as ``{"csv": ...}``
before parsing. Backfill re-runs first check prior raw blobs by
timestamp endpoint, copy any cached blob into the current ingest run,
and parse the cached CSV without re-fetching the same public URL.

Watchlist filter:
- An article is kept iff at least one watchlist asset name matches
  inside the article's themes / persons / organizations / document_
  identifier (case-insensitive substring). Matches per article are
  stored in ``matched_assets TEXT[]``. Articles with zero matches are
  dropped at parse time and never land in the table.
- Match terms: equity company names, crypto names, protocol names,
  13F filer names. Macro series IDs (FRED) are skipped — they don't
  appear in news. Min term length = 4 chars to avoid two-letter false
  positives (e.g. "AA" matching unrelated text).

Two modes:
- **incremental** (default) — fetches the last ``--hours`` window
  (default 24h) anchored on GDELT's published ``lastupdate.txt``.
- **--backfill --since YYYY-MM-DD** — walks from ``since`` to today,
  capped at ``MAX_BACKFILL_DAYS`` (365) so we don't pull data the
  retention policy will immediately drop.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import sys
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from genkei.common import db
from genkei.common.http import HttpClient, RateLimit
from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    Watchlist,
    load_watchlist,
)

LOGGER = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

SOURCE_NAME = "gdelt"
COLLECT_ENDPOINT = "collect"
BACKFILL_ENDPOINT = "backfill"

GDELT_BASE_URL = "https://data.gdeltproject.org/gdeltv2"
LASTUPDATE_URL = f"{GDELT_BASE_URL}/lastupdate.txt"
GDELT_RATE_LIMIT = RateLimit.per_second(2)
GDELT_USER_AGENT = "genkei/0.1 (+gdelt; research-desk)"

# GKG 2.1 CSV columns (0-indexed). Tab-separated, 27 columns total; we
# read only the ones we need to avoid paying for the heavy V2GCAM
# (col 17) and the V2.1 image / quote / translation columns.
COL_GKG_RECORD_ID = 0
COL_DATE = 1
COL_SOURCE_COLLECTION = 2
COL_SOURCE_NAME = 3
COL_DOC_IDENTIFIER = 4
COL_V1_THEMES = 7
COL_V1_LOCATIONS = 9
COL_V1_PERSONS = 11
COL_V1_ORGS = 13
COL_TONE = 15
# Row needs at least through the tone column; trailing cols optional.
GKG_MIN_COLUMNS = 16

# Server-side retention is 365 days. Backfill caps here so we don't pull
# data the retention policy will drop on its next sweep.
MAX_BACKFILL_DAYS = 365

# Empirical floor — most company names + crypto names are well above 4
# chars; below it the substring match leaks noise.
MIN_TERM_LENGTH = 4

# psycopg's executemany is happiest with bounded batches.
UPSERT_BATCH_SIZE = 500


@dataclass(frozen=True)
class _MatchTerm:
    """A canonical search term + the watchlist label to record on hit."""

    term_lower: str
    label: str


@dataclass(frozen=True)
class _ToneFields:
    tone: Decimal | None
    positive: Decimal | None
    negative: Decimal | None
    polarity: Decimal | None
    activity_density: Decimal | None
    self_density: Decimal | None
    word_count: int | None


@dataclass
class _ParsedRow:
    gkg_record_id: str
    published_at: datetime
    source_collection_id: int | None
    source_common_name: str
    document_identifier: str
    themes: list[str]
    locations: list[dict[str, Any]] | None
    persons: list[str]
    organizations: list[str]
    tone: _ToneFields
    matched_assets: list[str]


def build_match_terms(watchlist: Watchlist) -> list[_MatchTerm]:
    """Compile the substring terms to match articles against.

    Returns a deduped list, lower-cased + length-filtered. Equity entries
    contribute the company name labeled by ticker; crypto entries the
    coin name labeled by symbol; protocols the protocol name labeled by
    slug; filers the filer name labeled by CIK.
    """
    seen: dict[str, str] = {}

    def add(candidate: str, label: str, *, min_length: int = MIN_TERM_LENGTH) -> bool:
        term = candidate.strip().lower()
        if len(term) >= min_length and term not in seen:
            seen[term] = label
            return True
        return False

    for entry in watchlist.equities:
        add(entry.name, entry.symbol.upper())
    for entry in watchlist.crypto:
        if not add(entry.name, entry.symbol.upper()):
            add(entry.symbol, entry.symbol.upper(), min_length=3)
    for entry in watchlist.protocols:
        add(entry.name, entry.slug.lower())
    for entry in watchlist.filers:
        add(entry.name, entry.filer_cik)
    return [_MatchTerm(term_lower=k, label=v) for k, v in seen.items()]


def latest_gkg_timestamp(client: HttpClient) -> datetime:
    """Read GDELT's lastupdate.txt and return the latest GKG file timestamp.

    lastupdate.txt has up to 3 lines (export / mentions / gkg). Each line
    is ``<size>\\t<md5>\\t<url>``. We extract the GKG-suffixed URL and
    parse its filename timestamp.
    """
    response = client.get(LASTUPDATE_URL)
    response.raise_for_status()
    for line in response.text.splitlines():
        parts = line.split()
        if not parts:
            continue
        url = parts[-1]
        if url.endswith(".gkg.csv.zip"):
            basename = url.rsplit("/", 1)[-1]
            stamp = basename.split(".", 1)[0]
            return datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(
                tzinfo=timezone.utc
            )
    raise RuntimeError(
        f"lastupdate.txt did not contain a GKG file URL: {response.text!r}"
    )


def file_timestamps_for_window(end: datetime, *, hours: int) -> list[datetime]:
    """Generate 15-min file timestamps in ``[end - hours, end]``.

    ``end`` is rounded down to the previous 15-min slot. The lower boundary is
    included so incremental runs overlap by one file instead of skipping a file
    when GDELT's latest timestamp advances by more than exactly ``hours``.
    """
    if hours <= 0:
        raise ValueError(f"hours must be > 0, got {hours}")
    end_floor = end.replace(
        minute=(end.minute // 15) * 15, second=0, microsecond=0
    )
    start = end_floor - timedelta(hours=hours)
    out: list[datetime] = []
    cursor = start
    while cursor <= end_floor:
        out.append(cursor)
        cursor += timedelta(minutes=15)
    return out


def file_timestamps_for_date_range(
    *, since: date, until: date
) -> list[datetime]:
    """All 15-min file timestamps spanning whole UTC days in ``[since, until]``."""
    if until < since:
        raise ValueError(f"until ({until}) precedes since ({since})")
    out: list[datetime] = []
    day = since
    while day <= until:
        anchor = datetime.combine(day, time.min, tzinfo=timezone.utc)
        for i in range(96):  # 24 hours x 4 slots
            out.append(anchor + timedelta(minutes=15 * i))
        day += timedelta(days=1)
    return out


def url_for_timestamp(ts: datetime) -> str:
    """GKG csv.zip URL for the 15-min boundary timestamp."""
    stamp = ts.strftime("%Y%m%d%H%M%S")
    return f"{GDELT_BASE_URL}/{stamp}.gkg.csv.zip"


def _parse_tone(raw: str) -> _ToneFields:
    """V1.5 tone field: 7 comma-separated values.

    Missing field or fewer than 7 segments → all None. Individual non-
    numeric entries silently fall to None rather than raising.
    """
    if not raw:
        return _ToneFields(None, None, None, None, None, None, None)
    parts = raw.split(",")
    if len(parts) < 7:
        return _ToneFields(None, None, None, None, None, None, None)

    def dec(value: str) -> Decimal | None:
        value = value.strip()
        if not value:
            return None
        try:
            return Decimal(value)
        except InvalidOperation:
            return None

    def integer(value: str) -> int | None:
        value = value.strip()
        if not value:
            return None
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return None

    return _ToneFields(
        tone=dec(parts[0]),
        positive=dec(parts[1]),
        negative=dec(parts[2]),
        polarity=dec(parts[3]),
        activity_density=dec(parts[4]),
        self_density=dec(parts[5]),
        word_count=integer(parts[6]),
    )


def _parse_themes(raw: str) -> list[str]:
    """V1Themes: semicolon-delimited theme names."""
    if not raw:
        return []
    return [t for t in (s.strip() for s in raw.split(";")) if t]


def _parse_persons_orgs(raw: str) -> list[str]:
    """V1Persons / V1Organizations: semicolon-delimited names."""
    if not raw:
        return []
    return [p for p in (s.strip() for s in raw.split(";")) if p]


def _parse_locations(raw: str) -> list[dict[str, Any]] | None:
    """V1Locations: semicolon-delimited; each record is hash-delimited
    ``type#name#countrycode#adm1#lat#lon#featureid``.

    Returns None when the raw field is empty or every record was
    malformed (no fields → don't store an empty array, store NULL).
    """
    if not raw:
        return None
    records: list[dict[str, Any]] = []
    for entry in raw.split(";"):
        if not entry:
            continue
        parts = entry.split("#")
        if len(parts) < 7:
            continue
        try:
            loc_type = int(parts[0]) if parts[0] else None
        except ValueError:
            loc_type = None
        try:
            lat = float(parts[4]) if parts[4] else None
        except ValueError:
            lat = None
        try:
            lon = float(parts[5]) if parts[5] else None
        except ValueError:
            lon = None
        records.append(
            {
                "type": loc_type,
                "name": parts[1],
                "country_code": parts[2],
                "adm1": parts[3],
                "lat": lat,
                "lon": lon,
                "feature_id": parts[6],
            }
        )
    return records or None


def _parse_published_at(raw: str) -> datetime | None:
    try:
        return datetime.strptime(raw, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def match_article(
    *,
    themes: list[str],
    persons: list[str],
    organizations: list[str],
    document_identifier: str,
    terms: list[_MatchTerm],
) -> list[str]:
    """Return the asset labels matched by an article.

    Substring match (case-insensitive) over the concatenated themes /
    persons / organizations / document_identifier text. Sorted for
    determinism — tests rely on the order, and the array index column
    stays stable across re-upserts of the same row.
    """
    haystack = " | ".join(
        [
            " ".join(themes),
            " ".join(persons),
            " ".join(organizations),
            document_identifier,
        ]
    ).lower()
    if not haystack:
        return []
    hits: set[str] = set()
    for term in terms:
        if term.term_lower in haystack:
            hits.add(term.label)
    return sorted(hits)


def parse_csv_rows(
    csv_text: str, terms: list[_MatchTerm]
) -> Iterator[_ParsedRow]:
    """Parse a GKG CSV (tab-separated) and yield only matched rows.

    The csv module handles quoting correctly. QUOTE_NONE keeps embedded
    double quotes inside theme / org text intact — GDELT does not
    escape them per CSV convention.
    """
    reader = csv.reader(
        io.StringIO(csv_text), delimiter="\t", quoting=csv.QUOTE_NONE
    )
    for raw_row in reader:
        if len(raw_row) < GKG_MIN_COLUMNS:
            continue
        published_at = _parse_published_at(raw_row[COL_DATE])
        if published_at is None:
            continue
        themes = _parse_themes(raw_row[COL_V1_THEMES])
        persons = _parse_persons_orgs(raw_row[COL_V1_PERSONS])
        organizations = _parse_persons_orgs(raw_row[COL_V1_ORGS])
        document_identifier = raw_row[COL_DOC_IDENTIFIER]
        matched = match_article(
            themes=themes,
            persons=persons,
            organizations=organizations,
            document_identifier=document_identifier,
            terms=terms,
        )
        if not matched:
            continue
        try:
            source_collection_id: int | None = int(raw_row[COL_SOURCE_COLLECTION])
        except (ValueError, TypeError):
            source_collection_id = None
        yield _ParsedRow(
            gkg_record_id=raw_row[COL_GKG_RECORD_ID],
            published_at=published_at,
            source_collection_id=source_collection_id,
            source_common_name=raw_row[COL_SOURCE_NAME],
            document_identifier=document_identifier,
            themes=themes,
            locations=_parse_locations(raw_row[COL_V1_LOCATIONS]),
            persons=persons,
            organizations=organizations,
            tone=_parse_tone(raw_row[COL_TONE]),
            matched_assets=matched,
        )


def _row_to_dict(
    parsed: _ParsedRow, *, endpoint_label: str, ingest_run_id: int
) -> dict[str, Any]:
    """Serialize a parsed row to the dict shape bulk_upsert expects."""
    return {
        "published_at": parsed.published_at,
        "gkg_record_id": parsed.gkg_record_id,
        "source_collection_id": parsed.source_collection_id,
        "source_common_name": parsed.source_common_name or None,
        "document_identifier": parsed.document_identifier or None,
        "themes": parsed.themes,
        "locations": Jsonb(parsed.locations) if parsed.locations is not None else None,
        "persons": parsed.persons,
        "organizations": parsed.organizations,
        "tone": parsed.tone.tone,
        "positive_score": parsed.tone.positive,
        "negative_score": parsed.tone.negative,
        "polarity": parsed.tone.polarity,
        "activity_density": parsed.tone.activity_density,
        "self_density": parsed.tone.self_density,
        "word_count": parsed.tone.word_count,
        "matched_assets": parsed.matched_assets,
        "source_endpoint": endpoint_label,
        "fetched_at": datetime.now(timezone.utc),
        "ingest_run_id": ingest_run_id,
    }


def _decompress_csv(payload: bytes) -> str:
    """Open the .gkg.csv.zip payload and return the inner CSV text.

    GDELT publishes Latin-1-tolerant CSV; non-ASCII article text is
    common. utf-8 with ``errors='replace'`` keeps the parser robust
    against per-byte garbage in a single article rather than failing
    the whole 15-min batch.
    """
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        names = [n for n in zf.namelist() if n.endswith(".csv")]
        if not names:
            raise RuntimeError(f"zip contains no .csv member: {zf.namelist()}")
        with zf.open(names[0]) as fh:
            return fh.read().decode("utf-8", errors="replace")


def _raw_blob_endpoint_name(ts: datetime) -> str:
    """Stable raw-blob endpoint name for one GDELT GKG timestamp."""
    return f"gkg_{ts.strftime('%Y%m%d%H%M%S')}"


def _cached_raw_blob(
    endpoint_name: str,
) -> tuple[str, str, dict[str, Any], datetime] | None:
    """Return the newest cached GDELT CSV raw blob for ``endpoint_name``."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT url, payload, fetched_at FROM meta.raw_blobs "
            "WHERE endpoint_name = %s ORDER BY fetched_at DESC LIMIT 1",
            [endpoint_name],
        )
        row = cur.fetchone()
    if row is None:
        return None

    url, payload, fetched_at = row
    if not isinstance(payload, dict) or not isinstance(payload.get("csv"), str):
        LOGGER.warning(
            "GDELT cached raw blob %s has unexpected payload shape; refetching",
            endpoint_name,
        )
        return None
    return payload["csv"], url, payload, fetched_at


def _fetch_and_parse(
    client: HttpClient,
    ts: datetime,
    terms: list[_MatchTerm],
    *,
    ingest_run_id: int,
) -> list[_ParsedRow]:
    """Fetch one 15-min GKG CSV and return its matched rows.

    A 404 for a specific 15-min slot is treated as benign-empty — GDELT
    occasionally skips a slot (their upstream feeder failed for that
    window) and the next slot picks back up. Re-running won't help.
    """
    url = url_for_timestamp(ts)
    endpoint_name = _raw_blob_endpoint_name(ts)
    cached = _cached_raw_blob(endpoint_name)
    if cached is not None:
        csv_text, cached_url, payload, fetched_at = cached
        db.copy_raw_blob_for_run(
            ingest_run_id, endpoint_name, cached_url, payload, fetched_at
        )
        return list(parse_csv_rows(csv_text, terms))

    response = client.get(url)
    if response.status_code == 404:
        LOGGER.debug("GDELT %s not published (404)", url)
        return []
    response.raise_for_status()
    csv_text = _decompress_csv(response.content)
    db.store_raw_blob(ingest_run_id, endpoint_name, url, {"csv": csv_text})
    return list(parse_csv_rows(csv_text, terms))


def _upsert_rows(
    conn: psycopg.Connection, rows: list[dict[str, Any]]
) -> int:
    """Bulk-upsert rows in batches."""
    if not rows:
        return 0
    written = 0
    for i in range(0, len(rows), UPSERT_BATCH_SIZE):
        chunk = rows[i : i + UPSERT_BATCH_SIZE]
        written += db.bulk_upsert(
            conn,
            "gdelt.gkg",
            chunk,
            conflict_keys=("published_at", "gkg_record_id"),
        )
    return written


def _run_ingest(
    *,
    endpoint: str,
    stamps: list[datetime],
    terms: list[_MatchTerm],
    client: HttpClient,
    metadata: dict[str, Any],
) -> int:
    """Shared per-file fetch + parse + upsert loop for both modes."""
    written_total = 0
    with db.ingest_run(SOURCE_NAME, endpoint=endpoint, metadata=metadata) as run:
        LOGGER.info("GDELT %s: fetching %d files", endpoint, len(stamps))
        for ts in stamps:
            parsed_rows = _fetch_and_parse(
                client, ts, terms, ingest_run_id=run.id
            )
            if not parsed_rows:
                continue
            rows = [
                _row_to_dict(p, endpoint_label=endpoint, ingest_run_id=run.id)
                for p in parsed_rows
            ]
            with db.connection() as conn:
                written = _upsert_rows(conn, rows)
            run.add_rows(written)
            written_total += written
    return written_total


def collect(
    *,
    hours: int = 24,
    watchlist_path: Path | None = None,
    http_client: HttpClient | None = None,
) -> int:
    """Incremental ingest: pull the last ``hours`` window of GKG.

    Returns the row count written to ``gdelt.gkg``.
    """
    watchlist = load_watchlist(watchlist_path or DEFAULT_WATCHLIST_PATH)
    terms = build_match_terms(watchlist)
    if not terms:
        LOGGER.warning("GDELT match-term list is empty — no rows can match.")

    owns_client = http_client is None
    client = http_client or HttpClient(
        SOURCE_NAME, rate_limit=GDELT_RATE_LIMIT, user_agent=GDELT_USER_AGENT
    )
    try:
        latest = latest_gkg_timestamp(client)
        stamps = file_timestamps_for_window(latest, hours=hours)
        return _run_ingest(
            endpoint=COLLECT_ENDPOINT,
            stamps=stamps,
            terms=terms,
            client=client,
            metadata={
                "hours": hours,
                "term_count": len(terms),
                "latest_published": latest.isoformat(),
            },
        )
    finally:
        if owns_client:
            client.close()


def collect_backfill(
    *,
    since: date,
    until: date | None = None,
    watchlist_path: Path | None = None,
    http_client: HttpClient | None = None,
) -> int:
    """Backfill GKG files from ``since`` to ``until`` (default today UTC).

    Capped at the ``MAX_BACKFILL_DAYS`` (365) retention window — older
    days would land then be pruned on the next retention sweep.
    """
    today = datetime.now(timezone.utc).date()
    until = until or today
    if until > today:
        raise ValueError(
            f"until ({until}) cannot be in the future; latest allowed date is {today}"
        )
    floor = today - timedelta(days=MAX_BACKFILL_DAYS - 1)
    effective_since = max(since, floor)
    if effective_since != since:
        LOGGER.warning(
            "GDELT backfill since=%s clamped to %s (retention floor)",
            since,
            effective_since,
        )

    watchlist = load_watchlist(watchlist_path or DEFAULT_WATCHLIST_PATH)
    terms = build_match_terms(watchlist)
    if not terms:
        LOGGER.warning("GDELT match-term list is empty — no rows can match.")

    owns_client = http_client is None
    client = http_client or HttpClient(
        SOURCE_NAME, rate_limit=GDELT_RATE_LIMIT, user_agent=GDELT_USER_AGENT
    )
    try:
        stamps = file_timestamps_for_date_range(
            since=effective_since, until=until
        )
        return _run_ingest(
            endpoint=BACKFILL_ENDPOINT,
            stamps=stamps,
            terms=terms,
            client=client,
            metadata={
                "since": effective_since.isoformat(),
                "until": until.isoformat(),
                "term_count": len(terms),
                "clamped_from": (
                    since.isoformat() if effective_since != since else None
                ),
            },
        )
    finally:
        if owns_client:
            client.close()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Incremental mode: window in hours (default 24).",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Switch to backfill mode (requires --since).",
    )
    parser.add_argument(
        "--since", help="Backfill start date (YYYY-MM-DD)."
    )
    parser.add_argument(
        "--until",
        help="Backfill end date (YYYY-MM-DD); default today UTC.",
    )
    parser.add_argument(
        "--watchlist", help="Override path to watchlists.yml."
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
    watchlist_path = Path(args.watchlist) if args.watchlist else None

    try:
        if args.backfill:
            if not args.since:
                print("--backfill requires --since YYYY-MM-DD", file=sys.stderr)
                return 2
            since = date.fromisoformat(args.since)
            until = date.fromisoformat(args.until) if args.until else None
            written = collect_backfill(
                since=since, until=until, watchlist_path=watchlist_path
            )
            mode = "backfill"
        else:
            written = collect(hours=args.hours, watchlist_path=watchlist_path)
            mode = "incremental"
    except Exception as exc:
        LOGGER.exception("GDELT collect failed")
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"GDELT collect failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"ok": True, "mode": mode, "rows_written": written}))
    else:
        print(f"GDELT {mode}: {written} rows written to gdelt.gkg")
    return 0


if __name__ == "__main__":
    sys.exit(main())
