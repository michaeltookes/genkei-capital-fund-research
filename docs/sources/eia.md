# EIA Open Data v2 ingester (B-032)

The U.S. Energy Information Administration publishes oil prices,
inventories, natural-gas storage, production, and electricity
generation through the open
[EIA Open Data v2 API](https://www.eia.gov/opendata/). This ingester
is the **energy** companion to FRED (rates / credit / FX / vol,
B-028), BEA (real-economy growth, B-029), and Treasury (debt / cash /
cost-of-debt, B-030) already in the lake.

## Coverage v1

The watchlist seeds **11 series across 3 sleeves** spanning the major
US energy markets:

| Sleeve | series_id | Route | Cadence | Units |
|---|---|---|---|---|
| **Petroleum — spot prices** | `WTI_SPOT` | `petroleum/pri/spt` | Daily | USD/bbl |
|  | `BRENT_SPOT` | `petroleum/pri/spt` | Daily | USD/bbl |
| **Petroleum — inventories** | `CRUDE_INV_EXSPR` | `petroleum/stoc/wstk` | Weekly | thousand bbl |
|  | `GASOLINE_INV` | `petroleum/stoc/wstk` | Weekly | thousand bbl |
|  | `DISTILLATE_INV` | `petroleum/stoc/wstk` | Weekly | thousand bbl |
|  | `SPR_CRUDE` | `petroleum/stoc/wstk` | Weekly | thousand bbl |
| **Petroleum — production** | `CRUDE_PRODUCTION_US` | `petroleum/crd/crpdn` | Monthly | thousand bbl/day |
| **Natural gas** | `HH_SPOT` | `natural-gas/pri/fut` | Daily | USD/MMBtu |
|  | `NG_STORAGE_L48` | `natural-gas/stor/wkly` | Weekly | BCF |
|  | `NG_MARKETED_PROD_US` | `natural-gas/prod/sum` | Monthly | MMcf |
| **Electricity** | `ELEC_NET_GEN_US` | `electricity/electricity-power-operational-data` | Monthly | thousand MWh |

`series_id` is a friendly TEXT key curated in the watchlist. Each
entry binds a `route` + `facets` filter + `data_field` projection.
The full curated list lives in `src/genkei/data/watchlists.yml` under
the `eia:` section.

### Why these series

- **`WTI_SPOT` / `BRENT_SPOT`** — front-month US (Cushing OK) and
  global (Europe) crude benchmarks. The WTI-Brent spread informs US
  import/export dynamics and refinery sourcing.
- **`CRUDE_INV_EXSPR`** — weekly headline EIA inventory number; one of
  the largest scheduled oil-market events each Wed. SPR is excluded so
  policy-driven releases (e.g. 2022 SPR drawdown) don't pollute the
  commercial-stocks signal.
- **`GASOLINE_INV` / `DISTILLATE_INV`** — refined-product side of the
  weekly report. Gasoline is the summer-driving demand proxy; distillate
  (diesel + heating oil) is the freight + winter heating proxy.
- **`SPR_CRUDE`** — Strategic Petroleum Reserve crude stocks. Tracked
  separately so SPR refills / drawdowns are visible as an explicit
  policy signal rather than buried in commercial inventories.
- **`CRUDE_PRODUCTION_US`** — monthly US field production. Structural
  shale-trajectory signal; the OPEC-vs-US pivot point.
- **`HH_SPOT`** — Henry Hub daily spot price. The US natural-gas
  benchmark; weather-driven and the dominant power-burn cost signal.
- **`NG_STORAGE_L48`** — weekly EIA-914 working gas in storage, Lower
  48. Analog of crude inventories for gas markets; published Thursdays.
- **`NG_MARKETED_PROD_US`** — monthly US marketed gas production.
  Structural supply (associated gas from shale + dry-gas basins).
- **`ELEC_NET_GEN_US`** — monthly net electricity generation, all
  sectors / all fuels / US-wide. Real-economy demand proxy.

## Routes NOT covered (deferred to v2)

- **STEO short-term outlook** (`steo`) — forward forecasts, not
  observed history. Belongs in a separate forecast schema with
  `forecast_date` / `forecast_horizon` columns; the v1 hypertable
  is observation-only.
- **State / PADD-level petroleum series** (`petroleum/sum/snd`,
  weekly stocks by PADD) — useful for refinery + regional analysis
  but adds N × PADD-region cardinality to v1's scope.
- **Hourly electricity demand / generation by RTO**
  (`electricity/rto/region-data`) — sub-daily, high-volume, and
  geographically segmented. Right call once the data lake has a
  high-frequency electricity use case (renewables-vs-baseload
  analysis); not today.
- **Coal + nuclear + renewables generation by fuel type** — same
  electricity route as `ELEC_NET_GEN_US` but per-fuel facet
  projections. Trivial to add as new watchlist entries with
  `fueltype: COL` / `NUC` / `SUN` / `WND` etc.; deferred until a
  research session asks for them.

## Endpoint contract

- **Base URL** — `https://api.eia.gov/v2`.
- **Route** — slash-delimited path (e.g. `petroleum/stoc/wstk`,
  `natural-gas/stor/wkly`, `electricity/electricity-power-operational-data`).
  Always followed by `/data/` in the request.
- **Auth** — free API key in the `api_key` query param. Register at
  <https://www.eia.gov/opendata/register.php>. Set `EIA_API_KEY` in
  your local `.env` or as a GitHub Actions secret.
- **Pagination** — `offset` + `length` query params. Max
  `length=5000` per request. EIA returns `response.total` (often as a
  string) which the collector trusts as the loop bound; a short page
  is the fallback termination signal.
- **Frequency mapping** — single-char watchlist codes (`D`/`W`/`M`/`Q`/
  `A`) map to EIA's verbose form (`daily`/`weekly`/`monthly`/
  `quarterly`/`annual`) in `_eia_frequency()`.
- **Sort** — every request sorts ascending on `period` so page
  boundaries don't reshuffle rows across runs.

## Schema

- `eia.series` — entity dimension, PK `series_id`. One row per
  curated watchlist entry; holds the route, frequency, data_field,
  JSONB facets, and human-readable description.
- `eia.observations` — time-series fact, hypertable on `ts`, PK
  `(series_id, ts)`. 90-day chunks, compressed > 30 days, segmentby
  `series_id`.

Vintage: **latest-only**. PK has no vintage column; revisions
overwrite on upsert, matching EIA's revise-in-place semantics.

## v1 limitations & known issues

- **Backfill depth** — collector defaults to 10 years (`Date.today -
  3650 days`). Override with `--start YYYY-MM-DD` for deeper history;
  most petroleum + gas series go back to the 1990s or earlier at the
  source.
- **Electricity facets** — `ELEC_NET_GEN_US` pins `fueltype=ALL` +
  `location=US` + `sectorid=99` (all sectors). Per-fuel or per-state
  breakdowns are trivial new entries but not in v1.
- **API key redaction** — the key never lands in `meta.raw_blobs.url`
  or in failure-message metadata; both the storage path and the
  partial-endpoint logger redact via `_redact_key()`.
- **Rate limit** — anonymous tier is 5,000 req/hour. We're well under
  the cap with 11 series × ~1 page each; 2 req/s default rate limit
  matches the BEA / Treasury politeness defaults.

## How it runs

- **Daily workflow** — `.github/workflows/eia-daily.yml`, cron
  `30 12 * * *` (12:30 UTC) on the self-hosted Beelink runner.
  Sequences after Treasury (12:00 UTC) so the macro pulls
  (FRED → BEA → Treasury → EIA) don't stack.
- **Collect → normalize chained** — workflow runs the collector,
  parses the printed `ingest_runs id=NNN`, and passes it as
  `--source-run-id` to the normalizer. Treasury / BEA pattern.
- **Idempotent** — re-running on the same calendar day re-fetches +
  upserts cleanly; the PK swallows duplicates and revisions overwrite.

## Query path (Phase 3)

A `genkei eia --series WTI_SPOT --since 2024-01-01` subcommand mirrors
the `genkei fred` / `genkei bea` / `genkei treasury` shape. Defer the
Typer subcommand until a research session needs it — the data lands
in `eia.observations` regardless and is queryable via the standalone
`genkei query` SQL pass-through today.

## Follow-ups

- **Per-fuel electricity facets** — add `ELEC_NET_GEN_COAL` /
  `ELEC_NET_GEN_NUC` / `ELEC_NET_GEN_SUN` / `ELEC_NET_GEN_WND`
  entries when a renewables-vs-baseload research question warrants
  them. No code change; just watchlist additions.
- **STEO forecast schema** — separate `eia.forecast_observations`
  hypertable with `(series_id, forecast_date, observation_date)`
  PK if forward EIA forecasts ever become useful.
- **State / PADD breakdown** — `petroleum/sum/snd` by PADD when
  regional refinery analysis needs it.
- **`genkei eia` CLI subcommand** — typed query path. Defer until
  the agent or a research session asks for it.
