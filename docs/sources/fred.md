# FRED ingester (B-028)

The St. Louis Fed's [FRED](https://fred.stlouisfed.org/) is the
**rates / credit / vol / FX** spine of the lake. Twenty curated macro
series cover the curve, credit spreads, equity volatility, USD strength,
and several headline real-economy series. FRED is the **first ingester
to ship vintage-aware** (D-013) — the schema preserves every revision
date so a research session can ask "what did GDP look like as known on
2023-04-15?"

## Coverage v1

The watchlist's `macro_series:` block curates twenty FRED series across
five dimensions:

| Dimension | Series IDs |
|---|---|
| **Curve** | `DGS10`, `T10Y2Y`, `DGS2`, `DGS3MO`, `DFF` |
| **Credit** | `BAMLH0A0HYM2`, `BAMLC0A0CM` |
| **Vol** | `VIXCLS` |
| **FX** | `DTWEXBGS`, `DEXUSEU`, `DEXJPUS` |
| **Real economy** | `GDPC1`, `INDPRO`, `UNRATE`, `PAYEMS`, `ICSA`, `CPIAUCSL`, `PCEPILFE`, `RSAFS`, `M2SL` |

The full curated list lives in `src/genkei/data/watchlists.yml` under
`macro_series:`. Adding a series = one YAML entry; no code change.

## Endpoint contract

- **Base URL** — `https://api.stlouisfed.org/fred`.
- **Auth** — `api_key` query param. Free; register at
  <https://fredaccount.stlouisfed.org/apikeys>. Set `FRED_API_KEY` in
  `.env` locally or as a GitHub Actions secret.
- **Rate limit** — documented 120 req/min. Collector defaults to
  1 req/s — 20 series × 2 calls = 40 calls per run, well under the cap.
- **Endpoints** — `/series/{id}` (metadata), `/series/{id}/vintagedates`
  (vintage epoch list), `/series/{id}/observations` (per-vintage history
  via `output_type=3`).
- **Pagination** — `/series/vintagedates` capped at 10,000 dates per
  call; `/series/observations` at 100,000 obs. Collector chunks
  `vintage_dates` at 500 per call to stay under FRED's 2,000-vintage
  JSON cap.

## Schema

- `fred.series` — entity dim, PK `series_id`. Captures
  FRED-published metadata (title, frequency, units, last_updated).
- `fred.observations` — time-series fact, PK
  `(series_id, ts, realtime_start)`. Hypertable on `ts`, 90-day chunks,
  compressed > 30d, segmentby `series_id`.

The **`realtime_start` column in the PK** is what makes the schema
vintage-aware (D-013): each (series, date) tuple can carry multiple
rows differing only by the date at which that value was the FRED-
published latest. Daily upserts add new vintages without overwriting
historical ones.

## v1 limitations & known issues

- **Vintage chunking required** — a single full-history realtime window
  (`1776-07-04 → 9999-12-31`) hits FRED's 2,000-vintage JSON cap with a
  400 Bad Request on long daily series. The collector splits via
  `/series/vintagedates` + per-chunk `vintage_dates` parameter.
- **API key redaction** — `api_key=…` appears in every URL. The
  collector redacts to `***` before any URL or error message lands in
  `meta.raw_blobs.url` or the partial-endpoints log (G-021 / G-015).
- **No `--backfill` flag** — single-mode design (D-014). Daily runs use
  the same code path that pulls full history; FRED returns the entire
  series in one call (chunked by vintage window), so backfill and
  incremental are identical.
- **No CLI surface for as-of queries yet** — the data lands with full
  vintage info, but a typed `genkei fred --as-of YYYY-MM-DD --series
  GDPC1` subcommand is deferred. Today the path is `genkei query` with
  raw SQL `WHERE realtime_start <= '<date>'`.

## How it runs

- **Daily workflow** — `.github/workflows/fred-daily.yml`, cron
  `0 11 * * *` (11:00 UTC). First macro pull of the day.
- **Two-stage** — collect → `meta.raw_blobs` (one blob per series + one
  for metadata) → normalize → `fred.series` / `fred.observations`.
- **Reads** — `FRED_API_KEY` + `GENKEI_DATABASE_URL` from GH Actions
  secrets.

## Query path

`genkei macro --series DGS10 --since 2024-01-01` is the canonical typed
path. `--as-of YYYY-MM-DD` enables vintage-aware queries against the
realtime trail (the schema supports it; CLI surface lands when a
research session asks for it).

## Acceptance gates

Before consuming a FRED-driven brief or regime call:

1. **Freshness** — `meta.ingest_runs.finished_at` for the latest
   `(fred, collect)` and `(fred, normalize)` rows is within 36 hours.
2. **Every watchlist series present in `fred.observations`** — count
   distinct `series_id` matches `len(watchlist.macro)`. Missing series
   → contract-drift issue.
3. **Latest observation per series within expected staleness** — daily
   series (e.g. `DGS10`) lag ≤ 3 calendar days; weekly series (e.g.
   `ICSA`) lag ≤ 10 days; monthly series (e.g. `UNRATE`, `CPIAUCSL`)
   lag ≤ 60 days. Anything longer triggers manual investigation.
4. **No partial-endpoint failures** —
   `meta.ingest_runs.metadata.partial_endpoints` is empty for the latest run.
5. **`realtime_start` column populated** — every observation row carries
   a non-null `realtime_start`. Null indicates a missed vintage pass.

## Follow-ups

- **`genkei fred --as-of` CLI** — typed vintage-aware query surface.
- **Series-coverage expansion** — every research session that touches a
  new macro dimension (housing, manufacturing surveys, money supply)
  should add the relevant FRED series before relying on inference.
- **B-066** — macro regime classifier extension; the FRED series feed
  the classifier directly via `experiments/macro_regime.py`.
