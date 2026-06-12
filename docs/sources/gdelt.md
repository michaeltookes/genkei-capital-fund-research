# GDELT GKG ingester (B-033)

The [GDELT Project](https://www.gdeltproject.org/) publishes a
real-time global news firehose. The Global Knowledge Graph (GKG) v2.0
feed indexes every monitored article every 15 minutes with themes,
persons, organizations, and locations. v1 ingests the GKG, filters to
articles mentioning watchlist assets, and lands them with a 365-day
rolling retention policy matching GDELT's own server-side window.

## Coverage v1

Every 15-minute GKG CSV from GDELT, filtered to articles whose themes,
persons, or organizations match any watchlist asset (equities + crypto
+ protocols + 13F filers, case-insensitive substring, 4-char minimum).

The match-term set is computed at collect time from watchlist names,
but `matched_assets` stores canonical labels: equity / crypto tickers
(e.g. `AAPL`, `BTC`), protocol slugs, and filer CIKs. FRED series IDs
are intentionally excluded — they're not entities news articles refer
to by name.

## Endpoint contract

- **Base URL** — `https://data.gdeltproject.org/gdeltv2`.
- **Auth** — none.
- **Rate limit** — 2 req/s cap in the collector. ~96 files/day at
  steady state.
- **Endpoints** — `/lastupdate.txt` (current firehose index;
  the published latest 15-minute file URL) + `/{YYYYMMDDHHMMSS}.gkg.csv.zip`
  (per-window file, tab-separated, ~5-15 MB compressed).
- **Server retention** — GDELT keeps 365 days of historical files.
  Older windows return 404; the collector's `MAX_BACKFILL_DAYS=365`
  guard mirrors this.

## Schema

- `gdelt.gkg` — fact table, PK `(published_at, gkg_record_id)`.
  Hypertable on `published_at`, 7-day chunks, compressed > 30d,
  **365-day retention policy** (server-side mirror).

Column highlights: `matched_assets TEXT[]` (canonical watchlist labels
the record matched — PK CHECK constraint requires non-empty),
`themes TEXT[]`, `persons TEXT[]`, `organizations TEXT[]`,
`locations JSONB` (parsed GKG location objects), `document_identifier`,
`tone NUMERIC`,
`source_common_name`.

## v1 limitations & known issues

- **365-day rolling retention** — server-side and our table both cap
  at 365 days. Queries asking about events older than 365 days lose
  GDELT context. Documented design call; mirrors source policy.
- **Single-stage** — collect + normalize fused. Each 15-minute CSV is
  parsed inline + filtered + bulk-upserted; raw CSV blobs are stored
  in `meta.raw_blobs` for replay / cache but not for normalize replay
  (the filter happens during fetch).
- **Watchlist-driven filter** — `matched_assets` non-empty is a PK
  CHECK constraint. Rows that match zero watchlist assets are dropped
  and never enter the lake. Adding a new asset to the watchlist =>
  new rows in tomorrow's window onward, **NOT** retroactive (would
  need a `--backfill` re-fetch).
- **Backfill cache** — re-running `--backfill --since YYYY-MM-DD` on
  failure picks up cleanly without re-fetching already-stored windows
  (`meta.raw_blobs.endpoint_name = gdelt_<window_ts>`).
- **Match-term hit rate** — 1-3% on average. The other 97% of every
  CSV is dropped. Expect ~10-20k surviving rows/day depending on news
  cycle intensity.

## How it runs

- **Daily workflow** — `.github/workflows/gdelt-daily.yml`, cron
  `30 14 * * *` (14:30 UTC). Fetches the trailing 24h via
  `lastupdate.txt` + walks the 96 windows.
- **Reads** — `GENKEI_DATABASE_URL`. No API key gate.
- **Backfill** — `python -m genkei.ingest.gdelt --backfill --since
  2025-06-12` (capped at 365d).

## Query path

`genkei query` over `gdelt.gkg`. Canonical-label overlap searches
(`'AAPL' = ANY(matched_assets)`) are the join key to the rest of the
lake.

## Acceptance gates

Before consuming GDELT-derived signals:

1. **Freshness** — `meta.ingest_runs.finished_at` for the latest
   `(gdelt, collect)` row is within 36 hours.
2. **Latest window within 4 hours of `lastupdate.txt`** — GDELT
   publishes every 15 min; >4h gap signals fetch / parse drift.
3. **Surviving-row rate within band** — daily row count between 5,000
   and 50,000. Below band → match terms drifted; above band → filter
   over-permissive.
4. **`matched_assets` non-empty** — `SELECT COUNT(*) FROM gdelt.gkg
   WHERE cardinality(matched_assets) = 0` returns 0 (also enforced by
   PK CHECK).
5. **365-day retention enforced** — `MIN(published_at) >= NOW() - 365d`
   on the hypertable. Drift indicates the retention policy detached.

## Follow-ups

- **Dynamic watchlist-term config** — terms hardcoded inline today;
  break out to `config/gdelt.terms.yml` so new asset additions auto-
  expand the filter without a code change.
- **Retroactive backfill on watchlist additions** — re-fetch + re-
  filter the last 365 days when a new asset enters the watchlist so
  it appears with history rather than just from tomorrow forward.
- **News-cluster signal emitter** — cross-source rule integration:
  detect spikes in `matched_assets` cardinality per asset and emit
  into `meta.signal_events` (B-064 follow-up).
- **Theme-driven research overlay** — query GDELT themes
  (`ECON_BANKRUPTCY`, `MANMADE_DISASTER`, etc.) joined with watchlist
  movements for event-study triggers.
