# CFTC COT ingester (B-031)

Weekly Commitments of Traders data — the CFTC's structured breakdown of
who's long, who's short, and by how much in regulated futures markets.
**Macro-aware positioning signal** complementing FRED's price-level
view: CFTC tells you *who* is positioned and how.

## Coverage v1

Five markets across two report formats:

| Symbol | Code | Report type | Market |
|---|---|---|---|
| `BTC` | 133741 | TFF | CME Bitcoin futures |
| `ETH` | 146021 | TFF | CME Ether futures |
| `ES` | 13874A | TFF | E-mini S&P 500 futures |
| `GC` | 088691 | Disaggregated | COMEX gold |
| `CL` | 067651 | Disaggregated | NYMEX crude oil (WTI) |

The `cot_markets:` block in `src/genkei/data/watchlists.yml` curates the
list. **TFF** (Traders in Financial Futures) carries Asset Manager /
Leveraged Funds / Dealer breakdowns; **Disaggregated** carries Managed
Money / Swap Dealer / Producer-Merchant breakdowns. Both shapes land in
the same fact table — `trader_category` is text + `report_type` flags
which schema to interpret it against.

## Endpoint contract

- **Base URL** — `https://publicreporting.cftc.gov/resource`.
- **Auth** — none (public Socrata). An app token reduces throttling but
  is optional; v1 runs token-less.
- **Rate limit** — Socrata generously throttles token-less requests.
  Collector defaults to 1 req/s.
- **Datasets** —
  `gpe5-46if` (TFF), `72hh-3qpy` (Disaggregated). One call per market
  via `$limit=50000` + SoQL filter on `cftc_contract_market_code`.

## Schema

- `cftc.cot_reports` — fact table, PK
  `(report_date, market_code, trader_category)`. Plain table
  (~80k rows steady-state, no need for a hypertable).

Column highlights: `long_positions`, `short_positions`,
`spreading_positions` (all `BIGINT` — whole contracts), `report_type`
(`tff` or `disaggregated`), plus provenance columns.

## v1 limitations & known issues

- **Tuesday snapshot, Friday 3:30pm ET publication** — `report_date` is
  the Tuesday position-as-of date. Downstream signal experiments must
  **lag-shift forward to Friday** before joining against prices to
  avoid lookahead bias.
- **Three overlapping formats** — TFF + Disaggregated + Legacy. v1
  covers TFF + Disaggregated; Legacy format is deferred. Queries
  filter on `report_type` to disambiguate per-row category meaning.
- **Single-stage** — collect + normalize fused (no separate
  `meta.raw_blobs` hop). Socrata's JSON-formatted response is the
  table shape; the parser maps directly to the upsert.
- **Incremental by default** — fetches only reports newer than the
  latest `report_date` in `cftc.cot_reports`. `--backfill` drops the
  filter and pulls all available history (~1,600 rows per market,
  going back to 2006 for TFF, 1995 for Legacy / Disaggregated).
- **No retry on Socrata 500s** — collector relies on the shared
  `HttpClient`'s default retry policy.

## How it runs

- **Weekly workflow** — `.github/workflows/cftc-weekly.yml`, cron
  `0 22 * * *` (22:00 UTC). Runs daily on the cron but the incremental
  ingester is no-op on days CFTC didn't publish; the de-facto cadence
  is weekly (Fridays).
- **Reads** — `GENKEI_DATABASE_URL`. No API key gate.
- **Backfill** — `python -m genkei.ingest.cftc --backfill` walks the
  full history for every watchlist market. One-shot; CFTC data is
  immutable once published.

## Query path

`genkei cot --market BTC --since 2024-01-01` is the typed entry point.
Use `--trader-category leveraged_funds` for category slices, or
`--list-markets` to inspect each configured market's `report_type`.

## Acceptance gates

Before consuming COT-driven positioning signals:

1. **Freshness** — `meta.ingest_runs.finished_at` for the latest
   `(cftc, collect)` row is within 8 days (publication is weekly,
   plus a generous buffer for cron drift).
2. **Every watchlist market covered** — `SELECT DISTINCT market_code
   FROM cftc.cot_reports` matches the `cot_markets:` watchlist set.
3. **Latest report_date within 14 days for each market** — anything
   longer indicates the CFTC weekly release skipped or our incremental
   filter is misbehaving.
4. **No partial-endpoint failures** — `metadata.partial_endpoints` is
   empty for the latest run.
5. **TFF + Disaggregated correctly differentiated** — `trader_category`
   under TFF includes `Asset_Manager_Inst`, `Lev_Money` etc.;
   Disaggregated includes `Managed_Money`, `Swap_Dealer` etc. A row
   tagged TFF but carrying Disaggregated categories signals a parser
   regression.

## Follow-ups

- **Legacy format support** — adds another decade of pre-2006 history
  on TFF markets; not in v1.
- **Market expansion** — bonds (10Y, 30Y notes), silver, ag complex,
  natural gas. One-row-per-market in `watchlists.yml`; no code change.
- **Cross-source overlay with CME OI** — pair with B-104 once that
  ingester unblocks; CFTC gives weekly positioning, daily OI from CME
  fills in between releases.
- **Friday-aligned ts column** — a derived view materializing
  `report_date + 4 days` as `ts_published` would let downstream
  joins skip the lag-shift boilerplate.
