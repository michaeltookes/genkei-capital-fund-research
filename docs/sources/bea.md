# BEA NIPA ingester (B-029)

The Bureau of Economic Analysis publishes the real-economy companion
to FRED's rates/credit/vol/FX coverage. This ingester covers the
**NIPA** (National Income and Product Accounts) dataset — every macro
signal a research session would actually want from BEA lives here.

## Coverage v1

The watchlist seeds **10 NIPA lines across 6 tables**, deliberately
curated to fill the three dimensions FRED can't reach:

| Dimension | Lines | Frequency |
|---|---|---|
| **Growth** | Real GDP %Δ (T10101:1), nominal GDP (T10105:1), Real GDP per capita (T70100:5) | Q, Q, A |
| **Income / spending** | Personal Income (T20100:1), PCE total (T20100:24), Savings rate as % of DPI (T20100:35), Real PCE %Δ (T20302:1) | Q |
| **Inflation** | PCE Price Index headline (T20804:1), PCE Core ex F&E (T20804:25) — Fed's preferred gauge | Q |
| **Earnings** | Corporate Profits with IVA+CCAdj (T11400:4) | Q |

`series_id` is the composite `<table_id>:<line_number>:<frequency>` key
(e.g. `T10101:1:Q` for "Real GDP, % change SAAR, line 1"). The full curated
list lives in `src/genkei/data/watchlists.yml` under the `bea:` section.

## Datasets NOT covered (deferred)

BEA exposes 12 other datasets. None are in v1 scope:

- **MNE** (multinational enterprises), **FixedAssets**, **ITA**
  (international transactions), **IIP** (international investment
  position) — relevant to global-flow macro but downstream of
  what's already in the lake via CFTC + ETF + on-chain whale work.
- **Regional**, **GDPbyIndustry**, **UnderlyingGDPbyIndustry** — useful
  for sector / regional drill-downs but not a current research focus.
- **InputOutput**, **IntlServTrade**, **APIDatasetMetaData**, **IIPMaDM**
  — niche / metadata-only.

Reopen as separate backlog items if/when research demands them.

## Endpoint contract

- **Base URL**: `https://apps.bea.gov/api/data/`
- **Auth**: `UserID=<key>` query parameter. Free key — register at
  https://apps.bea.gov/API/signup/.
- **Rate limit**: undocumented. We use **2 req/s** as the "be polite"
  default; the full v1 watchlist (7 unique table fetches) finishes in
  <5 seconds.
- **Fetch shape**: one URL per unique `(table_id, frequency)` tuple.
  Multiple watchlist lines from the same table fold into one HTTP
  call — the normalizer filters down to watched lines at parse time.
  `T20100:1` + `T20100:24` + `T20100:35` = 1 fetch, not 3.
- **Response envelope**:
  ```json
  {
    "BEAAPI": {
      "Request": {...},
      "Results": {
        "Statistic": "Table 1.1.1...",
        "UTCProductionTime": "2024-04-25T08:30:00",
        "Dimensions": [...],
        "Data": [
          {
            "TableName": "T10101",
            "SeriesCode": "DGDSRL",
            "LineNumber": "1",
            "LineDescription": "Gross domestic product",
            "TimePeriod": "2024Q1",
            "METRIC_NAME": "Percent change at annual rate",
            "CL_UNIT": "Percent",
            "UNIT_MULT": "0",
            "DataValue": "3.4",
            "NoteRef": ""
          }
        ],
        "Notes": [...]
      }
    }
  }
  ```
- **TimePeriod** uses three documented formats:
  - `"2024Q1"`..`"2024Q4"` — quarterly
  - `"2024M01"`..`"2024M12"` — monthly (only a few NIPA lines support
    this)
  - `"2024"` — annual

## Schema

| Table | Purpose | PK |
|---|---|---|
| `bea.series` | Entity dim — line description, units, frequency, note refs | `series_id` (`<table_id>:<line_number>:<frequency>`) |
| `bea.observations` | Time-series fact, hypertable on `ts`, 90-day chunks, compression > 30d | `(series_id, ts, frequency)` |

**Frequency in the PK** is load-bearing. BEA returns the same NIPA line
at quarterly *and* annual cadences depending on the request — both are
research-useful (quarterly for high-frequency signals, annual for the
chart-friendly long view). The frequency is also part of `series_id`, so
cadence-specific series metadata cannot overwrite another cadence for the
same NIPA table line.

## v1 limitations (worth knowing before relying on the data)

1. **Latest-only, NOT vintage-aware**. BEA's API has no vintage-date
   parameter (unlike FRED's `realtime_start`); revisions overwrite in
   place at the source. The migration's PK omits a vintage column to
   match. v2 would add `fetched_at_date` to the PK so each ingest
   snapshots a private revision trail.

2. **No backfill flag** like FRED's `--backfill --since`. BEA's
   `Year=ALL` returns the entire available history in one call, so
   daily and "backfill" runs are the same code path. Each daily run
   upserts every observation; the latest-only schema doesn't grow
   unbounded.

3. **`"..."` is the missing-value sentinel** (not JSON `null`). The
   parser maps it to `value IS NULL` in `bea.observations` — the row
   still exists so consumers know BEA *had* a row, just no value.

4. **Thousands-separated `DataValue`** strings (e.g. `"23,128.3"`) are
   stripped before float conversion. Native numeric responses
   passthrough.

5. **200-OK-with-error envelope**: bad table id / missing key returns
   HTTP 200 with `{"BEAAPI": {"Error": ...}}` or
   `{"BEAAPI": {"Results": {"Error": ...}}}`. The collector detects
   both shapes and records them as partial-endpoint failures rather
   than treating them as success.

6. **API key in URL** — the collector redacts `UserID=<key>` to
   `UserID=***` before any URL lands in `meta.raw_blobs.url`. Same
   pattern as FRED's G-015 fix.

## How it runs

- **Cron**: daily at **11:30 UTC** (`.github/workflows/bea-daily.yml`)
  on the self-hosted Beelink runner. 30 minutes after FRED-daily so the
  two free-API macro pulls don't double-load the runner's morning slot.
- **Manual replay**:
  ```bash
  python -m genkei.ingest.bea     # collect
  python -m genkei.normalize.bea  # normalize (auto-finds latest collect)
  ```
- **Watchlist health**: registered in `PRIMARY_TABLES` + `RECURRING_ENDPOINTS`.
  `genkei watchlist health` surfaces OK / STALE / FAIL / MISSING / EMPTY
  for the `bea` source like every other ingester.

## Querying the data

Today the path is `genkei query` over `bea.observations`:

```bash
genkei query --sql "
  SELECT b.series_id, s.line_description, b.ts, b.value
  FROM bea.observations b
  JOIN bea.series s USING (series_id)
  WHERE b.series_id = 'T20804:25:Q'
  ORDER BY b.ts DESC LIMIT 4
"
```

A typed CLI surface (`genkei macro --source bea`, or a separate
`genkei bea`) is a natural follow-up but not in v1 scope. The lake
data is what matters most; the agent + the SQL escape hatch get the
job done in the meantime.

## Natural follow-ups

- **B-066** — surface the macro regime classifier (B-059) with BEA
  growth inputs once enough history accumulates. The current regime
  classifier reads 4 FRED series (rates/credit/vol/FX); BEA adds the
  growth + spending + inflation dimensions.
- **Vintage-aware v2** if a research session ever needs as-of
  backtests against historically-known BEA values.
- **CLI surface** — `genkei macro --source bea` or `genkei bea` for
  type-safe querying. Today's path is `genkei query` + raw SQL.
