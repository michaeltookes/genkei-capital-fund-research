# Architecture

**Living doc.** Two halves:

- **Snapshot** (top half) — what the system *is* today. Refresh in lockstep with shipped work.
- **Decision log + gotchas** (bottom half) — *append-only* record of consequential choices and surprises. Add entries as you make/hit them, not at PR time.

The point of the bottom half: when context gets cleared, the next session (Claude or human) can rebuild *why* we did what we did — not just *what* we did. Commit messages capture *what changed*; `docs/resolved.md` captures *what shipped*; this doc captures *what we learned*.

**Updating discipline:** any commit that makes a non-obvious choice (a tradeoff with a real alternative) or surfaces a non-obvious surprise (a thing future-you wouldn't predict) appends an entry below in the same commit. If the entry is missing, the commit is incomplete.

**Last updated:** 2026-06-09 (Phase 2 substantially complete — DeFiLlama, FRED, SEC (submissions + XBRL + Form 4 + Form 13F), CoinGecko, Yahoo equities + benchmarks, CFTC COT, iShares spot ETF, on-chain Etherscan staking + whale flow, Sui RPC validators + unlocks, Coinbase public market data, GDELT GKG (B-033); the per-source doc backfill (B-036) remains the largest open item. Phase 3 complete — 22/22; `news` (B-043) now queries `gdelt.gkg` with watchlist / theme / topic / tone filters and lightweight (date, source) clustering. Phase 4 foundation laid; B-051/052/053 still open. Phase 5: 9 detectors shipped — `insider_clusters`, `protocol_revenue`, `relative_strength`, `macro_regime`, `tvl_drawdown`, `eight_k_impact`, `crowding_monitor`, `watchlist_scoring`, `news_sentiment` (B-056 — first cross-source feedback-loop experiment consuming both B-033 and B-043). Phase 6 advanced — cross-source signal correlation engine (B-064) with five emitters wired (`insider_clusters`, `crowding`, `eight_k`, `tvl_drawdown`, `relative_strength` × {crypto, equity}); stack-outcome backtest (B-101) + SPY/BTC benchmark adjustment (B-100); `analytics.crypto_relative_strength` + `analytics.macro_regime_per_date` views. Phase 7 in progress — workflow-failure + ingest-staleness alerting (B-071), schema-drift detection (B-072), backup posture (B-070) all shipped.)

> **Read this first if you're new (or future-you after weeks away).** Then dive into the per-component docs at the bottom for depth.

---

## What this is

**Genkei Capital research-desk** — a queryable financial-data lake (equities + crypto) backing four use cases:

1. **Experiments** — event studies, signal/return analyses, regime classifiers.
2. **Trend analysis** across long histories.
3. **Inefficiency detection** — informs Michael's investing decisions.
4. **On-demand AI researcher** — ask anything against the data; the agent answers via the CLI.

**The data lake is the asset.** Daily briefs, reports, scoring rubrics — all emergent UIs over the lake.

Operating *as if* a real fund (data hygiene, archival, audit trail at fund-grade), but actual capital is personal + close friends/family. No fiduciary duty today; outputs must still be defensible if scope expands later.

### Edge types pursued

- **Macro / regime-driven** — the spine; equities and crypto are downstream of macro.
- **Event-driven** — filings (8-K, 10-Q, 13F, Form 4), earnings, token unlocks, protocol launches, news clusters.
- **Fundamentals / valuation** — revenue, fees, TVL vs market cap.
- **Technical / momentum / flow** — TVL drawdowns, exchange flows, momentum signals.

### Sleeves & watchlists

| Sleeve | Holdings | Where defined |
|---|---|---|
| Equity core (long-only, Buffett-style) | 28 names — mega-cap tech, semis, software, financials, crypto-adjacent | `config/watchlists.yml` |
| Crypto core (long-term hold) | BTC, ETH, SOL, LINK | `config/watchlists.yml` |
| Crypto tactical (turnover-eligible) | SUI (primary watchlist), PYTH, RENDER (secondary) | `config/watchlists.yml` |
| Macro series (FRED IDs) | 20 starter series — rates, inflation, growth/labor, liquidity, dollar, credit, vol, housing, sentiment, commodities | `config/watchlists.yml` |

Tier (primary/secondary) and sleeve (core/tactical) are orthogonal: tier = how much *coverage* the data lake gives an asset; sleeve = how Michael *trades* it.

---

## High-level data flow

```
External APIs (DefiLlama, CoinGecko, FRED, SEC EDGAR, Etherscan)
       │
       ▼  ┌─────────────────────────────────────────────────────────────┐
          │  Collector  src/genkei/ingest/<source>.py                    │
          │   - HttpClient (rate limit, retry, backoff, jitter)          │
          │   - db.ingest_run() context (one row in meta.ingest_runs)    │
          │   - db.store_raw_blob() per endpoint → meta.raw_blobs        │
          │   - source-specific modes: daily, single-mode, or --backfill │
          └─────────────────────────────────────────────────────────────┘
                                │
                                ▼
                     ┌─────────────────────┐
                     │  meta.raw_blobs     │  audit + replay
                     │  (JSONB payloads)   │  — 90-day retention policy
                     └─────────────────────┘
                                │
       ┌─────────────────────────────────────────────────────────────┐
       │  Normalizer  src/genkei/normalize/<source>.py                │
       │   - reads raw blobs by source_run_id                         │
       │   - bulk_upsert into per-source schema (ON CONFLICT DO UPD)  │
       │   - own meta.ingest_runs row with metadata.source_run_id     │
       └─────────────────────────────────────────────────────────────┘
                                │
                                ▼
       ┌─────────────────────────────────────────────────────────────┐
       │  Per-source schemas (hypertables on time-series facts):      │
       │   defillama.{chain_tvl, protocol_tvl, protocol_fees,         │
       │              stablecoins, prices, protocols}                 │
       │   coingecko.{coins, market_data}                             │
       │   yahoo.candles  (equity + benchmark OHLCV)                  │
       │   fred.{series, observations} — vintage-aware                │
       │   sec.{companies, filings, facts, insiders,                  │
       │        form4_transactions, form13f_holdings}                 │
       │   cftc.cot_reports                                           │
       │   etf.fund_snapshots                                         │
       │   onchain.{staking_events, eth_whale_flows,                  │
       │            sui_validators, sui_unlocks}                      │
       │   coinbase.*  (Coinbase Exchange public market data)         │
       │   analytics.{crypto_relative_strength,                       │
       │              macro_regime_per_date}  (derived views)         │
       │   meta.{ingest_runs, raw_blobs, signals, signal_events}      │
       │   signal rules: src/genkei/data/signal_rules.yml             │
       │  Provenance trio on every fact row:                          │
       │   (source_endpoint, fetched_at, ingest_run_id FK)            │
       └─────────────────────────────────────────────────────────────┘
                                │
              ┌─────────────────┼─────────────────────┐
              ▼                 ▼                     ▼
   ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────┐
   │  genkei CLI      │  │  Experiments +   │  │  Agent (Claude Code) │
   │                  │  │  signal emitters │  │                      │
   │  ~22 typed cmds  │  │                  │  │  /research walks the │
   │  + SQL escape    │  │  Pure detectors: │  │  methodology, lands  │
   │  hatch.          │  │  insider_clusters│  │  a decision file at  │
   │  Bash-composable │  │  protocol_revenue│  │  docs/research/      │
   │  + --json        │  │  relative_strength│ │  decisions/.         │
   │                  │  │  macro_regime    │  │                      │
   │  prices,filings, │  │  tvl_drawdown    │  │  /reflect-decisions  │
   │  tvl,macro,      │  │  eight_k_impact  │  │  closes loop later   │
   │  insiders,       │  │  crowding_monitor│  │  via realized prices │
   │  holdings,       │  │  watchlist_scoring│ │                      │
   │  crowding,       │  │  stack_backtest  │  │  Signal stacks via   │
   │  macro-regime,   │  │                  │  │  meta.signal_events  │
   │  tvl-drawdown,   │  │  emitters/* feed │  │  inform watchlist    │
   │  etf-flows,cot,  │  │  meta.signal_*   │  │  scoring + research  │
   │  signals,        │  │  tables for B-064│  │                      │
   │  backtest,query, │  │  correlator      │  │                      │
   │  watchlist,...   │  │                  │  │                      │
   └──────────────────┘  └──────────────────┘  └──────────────────────┘
```

The normalizer is **data-lake-shaped**, not report-shaped (D-006). Cross-source derived signals (relative-strength, revenue-divergence) live in either Postgres views under `analytics.*` or in `src/genkei/experiments/<name>.py` pure functions, paired with a CLI surface under `src/genkei/cli/<name>.py`. The legacy markdown daily brief is retired (B-025 / D-024); the durable report artifact is the weekly signal digest (`reports/signals/`).

---

## What's built today

### Infrastructure

| Piece | What | Where to learn more |
|---|---|---|
| **Postgres + TimescaleDB** | `genkeicapital-postgres` on the Beelink: `timescale/timescaledb:2.26.4-pg16`, port 5440, on `mission_control_net`. `timescaledb` extension installed. | `docs/infrastructure.md`, R-007/R-011/R-014 |
| **Self-hosted GH Actions runner** | `beelink-genkei-1` running `myoung34/github-runner:latest` on the Beelink, attached to `mission_control_net`. PAT-rotated registration. Labels: `self-hosted, beelink, linux, x64`. | `docs/infrastructure.md` § Self-hosted GitHub Actions runner, R-020 |
| **Network reachability** | Beelink behind double-NAT — GH-hosted runners can't reach it; self-hosted runner is the path. Cloudflare TCP tunnel deferred. | `docs/infrastructure.md` § Network reachability |
| **Docker network** | `mission_control_net` (bridge) — Postgres + runner share it; future ingester containers go here too. | server-info skill (local-only) |

### Data layer

| Piece | Schema / table | Where to learn more |
|---|---|---|
| **Per-source schema strategy** | `defillama.*`, `sec.*`, `fred.*`, ..., `meta.*` operational, `analytics.*` cross-source views, `public.alembic_version` for bootstrap. | `docs/storage.md` |
| **`meta.ingest_runs`** | Audit row per pipeline execution (source, endpoint, status, started_at, finished_at, rows_written, error, metadata JSONB). | R-008 in `docs/resolved.md` |
| **`meta.raw_blobs`** | One row per endpoint per collector run (ingest_run_id FK CASCADE, endpoint_name, url, payload JSONB, fetched_at). UNIQUE(ingest_run_id, endpoint_name). | R-017 |
| **`defillama.protocols`** | Slug-keyed entity dimension. BIGSERIAL surrogate, `chains TEXT[]` GIN-indexed, `first_seen_at` / `last_updated_at` lifecycle. | R-015 |
| **`defillama.chain_tvl`** | Time-series fact, hypertable, PK `(chain, ts)`, 30-day chunks. | R-015 |
| **`defillama.stablecoins`** | Time-series fact, hypertable, PK `(asset_id, chain, ts)`, 30-day chunks. | R-015 |
| **`defillama.prices`** | Time-series fact, hypertable, PK `(asset_key, ts)`, 7-day chunks (intraday-ready). | R-015 |
| **`defillama.protocol_tvl`** | Time-series fact, hypertable, PK `(slug, chain, ts)`, FK to `protocols(slug)`, 30-day chunks. Per-protocol per-chain TVL series — populated by backfill walking `/protocol/{slug}`. | R-023 |
| **Compression policies** | All four `defillama.*` hypertables compress chunks > 30 days old via TimescaleDB native compression (~10x savings). Segmentby = the most-filtered column per table; orderby = `ts DESC`. | R-023 |
| **`meta.raw_blobs` retention** | Daily TimescaleDB job (`add_job`) deletes blobs > 90 days old. The normalized lake tables are the system of record; raw is audit/replay only. | R-023 |
| **`fred.series`** | Entity dim for FRED macro series, PK `series_id`, holds `title` / `units` / `frequency` / `last_updated` / `popularity` / `observation_start_end` metadata. | R-027 |
| **`fred.observations`** | Time-series fact, hypertable, PK `(series_id, ts, realtime_start)`, 90-day chunks, compression on chunks > 30 days old. **Vintage-aware** — every FRED revision lands as a distinct row keyed on `realtime_start` so as-of backtests can reconstruct what was known on any given date. | R-027 |
| **`sec.companies`** | Entity dim for SEC EDGAR registrants, PK `cik` (zero-padded 10-char), holds `ticker` / `name` / `sic` / `exchanges` / `entity_type` / `fiscal_year_end` metadata from the submissions index. | R-028 |
| **`sec.filings`** | One row per SEC filing, PK `accession_number` (the SEC's own unique filing ID). Indexed on `(cik, filed_at DESC)` and `(form_type, filed_at DESC)`. Plain table — modest volume (~85k rows steady-state across 28 watchlist companies). | R-028 |
| **`sec.facts`** | XBRL fact table, hypertable on `period_end` (30-day chunks), compression on chunks > 30 days old, PK `(cik, concept, unit, period_start, period_end, accession_number)`. Same `(concept, period)` can appear in multiple filings (10-Q + subsequent 10-K); all rows land for query-side filtering. | R-028 |
| **`coingecko.coins`** | Entity dim for CoinGecko coins, PK `coingecko_id` (e.g. "bitcoin"), holds `symbol` / `name` / `market_cap_rank` / `genesis_date` / categories metadata. | R-029 |
| **`coingecko.market_data`** | Time-series fact, hypertable on `ts` (30-day chunks, compression > 30 days), PK `(coingecko_id, ts)`. One row per coin per day with `price_usd` / `market_cap_usd` / `volume_usd`. Second crypto price source alongside DeFiLlama — cross-check + market cap + volume that DeFiLlama doesn't expose. | R-029 |
| **`yahoo.candles`** | Time-series fact, hypertable on `ts`, PK `(ticker, ts)`. Per-equity + per-benchmark daily OHLCV. Watchlist equities + SPY / QQQ / IWM benchmarks share the table; benchmark routing handled at the CLI layer. | B-092, B-102 |
| **`sec.form13f_holdings`** | Per-position institutional holdings table. PK `(accession_number, holding_idx)`; `cusip` is indexed for holder/crowding lookups. Filer metadata joins through `sec.form13f_filings` / `sec.filers`. Powers `genkei holdings` + the 13F crowding monitor (B-061). | B-080 |
| **`cftc.cot_reports`** | Weekly CFTC Commitments of Traders by product (BTC / ETH / SPY / etc.) and report category (Asset Manager / Leveraged Funds / Dealer Intermediary). | B-031 |
| **`etf.fund_snapshots`** | Daily NAV + shares-outstanding + TNA per spot ETF. PK `(fund_ticker, snapshot_date)`. Schema designed to extend cleanly to additional issuers (B-113); 10-Q quarter-end backfill tracked as B-114. | B-105, B-107 |
| **`onchain.eth_whale_flows`** | Daily ETH net flow per curated whale wallet, plus per-category aggregates. Backed by Etherscan; curated wallet list in `config/watchlists.yml` (methodology in `docs/sources/eth-whale-addresses.md`). | B-106 |
| **`onchain.sui_validators`** | Sui mainnet validator + staking-flow per epoch (PK `(validator_address, epoch)`). APY preserved per validator across partial reruns. | B-088 |
| **`onchain.sui_unlocks`** | SUI token unlock events for the Community Reserves allocation. Same-day batches aggregated. Remaining 7 paywalled allocations tracked as B-115. | B-089 |
| **`analytics.macro_regime_per_date`** | Derived view: daily macro-regime label (`risk_on` / `risk_off` / `easing` / `tightening_stress` / `mixed`) per business date from FRED inputs (DGS10, BAMLH0A0HYM2, VIXCLS, DTWEXBGS). Surfaced via `genkei macro-regime`. | B-059 |
| **`meta.signals`** | Per-asset per-day composite watchlist score with `rubric_version` + per-component breakdown. v1 components per asset class documented in `docs/scoring.md`. | B-065 |
| **`meta.signal_events`** | Per-source signal-event store fed by `experiments/emitters/*`. Cross-source correlator (B-064) reads this table, applies the file-backed rule pack in `src/genkei/data/signal_rules.yml`, and emits signal stacks when ≥ N distinct sources fire inside a window. | B-064 |
| **Provenance trio** | Every fact row carries `source_endpoint TEXT NOT NULL`, `fetched_at TIMESTAMPTZ NOT NULL`, `ingest_run_id BIGINT NOT NULL REFERENCES meta.ingest_runs(id)`. | R-021 |
| **Migration tool** | Alembic, hand-written migrations only (no autogen). Files at `migrations/versions/YYYYMMDD_<slug>.py`. URL from `GENKEI_DATABASE_URL`. 31 applied as of 2026-06-08. | R-008, `docs/storage.md` § B-009 |

### Code layer (`src/genkei/`)

```
src/genkei/
├── __init__.py
├── common/
│   ├── db.py            — pool, connection(), bulk_upsert, ingest_run() ctx
│   ├── http.py          — HttpClient with rate limit + retry/backoff + jitter
│   ├── config.py        — stdlib .env loader
│   ├── watchlist.py     — canonical watchlist loader (crypto / equities / macro / protocols / benchmarks)
│   └── schema_drift.py  — schema-drift detection helpers (B-072)
├── data/
│   ├── watchlists.yml   — single source of truth for assets the lake tracks
│   └── signal_rules.yml — declarative rule pack consumed by the B-064 correlator
├── ingest/                       — one collector per source
│   ├── defillama.py              — DefiLlama (daily + --backfill)
│   ├── fred.py                   — FRED (vintage-aware, single-mode)
│   ├── sec.py                    — SEC EDGAR submissions + companyfacts
│   ├── sec_form4.py              — SEC Form 4 insider transactions
│   ├── sec_form13f.py            — SEC Form 13F institutional holdings (B-080)
│   ├── coingecko.py              — CoinGecko market_chart (daily + Pro --backfill)
│   ├── yahoo.py                  — Yahoo equities + benchmarks OHLCV (B-092 / B-102)
│   ├── cftc.py                   — CFTC Commitments of Traders weekly (B-031)
│   ├── ishares.py                — iShares spot ETF product-screener (B-105 / B-107)
│   ├── onchain_staking.py        — Etherscan v2 logs for Chainlink staking (B-082 / B-086)
│   ├── etherscan_whale_flow.py   — Top-N ETH whale wallet daily net flow (B-106)
│   ├── sui_staking.py            — Sui mainnet validator + staking flow per epoch (B-088)
│   ├── sui_unlocks.py            — SUI unlock schedule (Community Reserves, B-089)
│   └── coinbase.py               — Coinbase public market data (B-035 family)
├── normalize/                    — one normalizer per ingester, raw blobs → tables
├── reports/
│   └── signal_digest.py          — weekly cross-source signal digest → reports/signals/ (D-024)
├── cli/
│   ├── prices, filings, tvl, macro, macro-regime, watchlist, query
│   ├── insiders, insider-clusters, holdings, crowding, eight-k-impact
│   ├── revenue-divergence, relative-strength, tvl-drawdown
│   ├── etf-flows, cot, stablecoin-flow, whales
│   ├── news (B-043 — GDELT GKG clusters)
│   ├── news-sentiment (B-056 — GDELT tone vs forward returns)
│   ├── signals (B-064 + B-065), backtest (B-101)
│   └── _helpers.py (json_default, parse_date)
└── experiments/                  — pure detectors paired with CLI commands
    ├── insider_clusters.py       — Form 4 cluster detection (B-060)
    ├── protocol_revenue.py       — price-vs-fundamentals divergence (B-062)
    ├── relative_strength.py      — asset-vs-peer return math (B-090)
    ├── macro_regime.py           — daily macro regime classifier (B-059)
    ├── tvl_drawdown.py           — TVL drawdown early warning (B-058)
    ├── eight_k_impact.py         — 8-K filing impact study (B-057)
    ├── crowding_monitor.py       — 13F crowding monitor (B-061)
    ├── watchlist_scoring.py      — per-asset composite scoring rubric (B-065)
    ├── news_sentiment.py         — GDELT tone vs forward returns (B-056)
    ├── stack_backtest.py         — stack-outcome backtest (B-101)
    ├── signal_store.py           — meta.signal_events store + correlator (B-064)
    ├── signal_rules.py           — rule pack loader (data/signal_rules.yml)
    ├── signal_benchmark.py       — SPY/BTC benchmark adjustment for stack scoring (B-100)
    └── emitters/                 — adapters from each experiment to meta.signal_events
        ├── insider_clusters_emitter.py
        ├── crowding_emitter.py
        ├── eight_k_emitter.py
        ├── tvl_drawdown_emitter.py
        ├── relative_strength_emitter.py            — crypto vs BTC (B-098)
        └── equity_relative_strength_emitter.py     — equity vs SPY/QQQ (B-111 / B-112)
```

| Module | What | Where to learn more |
|---|---|---|
| `genkei.common.db` | Lazy connection pool, `connection()` (commit/rollback), `bulk_upsert` (`INSERT ... ON CONFLICT`), `ingest_run()` (records meta.ingest_runs lifecycle). | R-009 |
| `genkei.common.http` | `HttpClient(source_name, rate_limit=RateLimit.per_second(N), retry=RetryPolicy(...))`. Handles 408/425/429/5xx with `Retry-After` support and exponential backoff + jitter. Source-tagged User-Agent. | R-010 |
| `genkei.common.config` | `load_env_file(path)` — stdlib-only `.env` loader, no python-dotenv dep. | R-013 |
| `genkei.ingest.defillama` | DeFiLlama collector + backfill (`--backfill --since YYYY-MM-DD --endpoint X`). Daily mode INSERTs raw blobs per endpoint; backfill mode walks daily timestamps for prices, iterates known slugs/asset_ids for protocols/stablecoins. Resumability via `meta.raw_blobs.url` lookup within a 14-day window. | R-017, R-023 |
| `genkei.normalize.defillama` | Reads raw blobs and `bulk_upsert`s. Daily mode (`normalize`) handles snapshot blobs into all four lake tables; backfill mode (`normalize_backfill`) dispatches by `endpoint_name` prefix into `defillama.prices`, `defillama.protocol_tvl`, `defillama.stablecoins`. Idempotent throughout. | R-018, R-023 |
| `genkei.ingest.fred` | FRED collector. Reads `macro_series:` from `config/watchlists.yml`, hits `/series`, then `/series/observations`. Daily mode uses a bounded realtime window starting at the latest stored `fred.observations.realtime_start` per series and drops rows clipped to that inclusive boundary unless they close a currently open stored interval or carry a same-day value update; closure rows are rewritten to the stored open `realtime_start` before normalization. Fresh-series/bootstrap and `--backfill` use `/series/vintagedates` plus chunked `output_type=3` observation calls, converted from wide vintage columns into the long `(date, realtime_start, realtime_end, value)` shape. Lands two raw blobs per series (`series_<id>`, `observations_<id>`). Redacts the API key from the URL stored in `meta.raw_blobs`. | R-027 |
| `genkei.normalize.fred` | Reads FRED raw blobs by source_run_id, dispatches by prefix into `fred.series` (one row per series) and `fred.observations` (one row per `(series_id, ts, realtime_start)`). Idempotent. | R-027 |
| `genkei.ingest.sec` | SEC EDGAR collector. Reads CIKs from `equities:` in `config/watchlists.yml`; dedupes by CIK so multi-class listings (GOOG/GOOGL share Alphabet's CIK) fetch once. Hits `/submissions/CIK{cik}.json`, follows `filings.files[]` references for older history pages, and `/api/xbrl/companyfacts/CIK{cik}.json` per company. 8 req/sec rate limit (under SEC's 10/sec cap). User-Agent identifies the user via `SEC_USER_AGENT` env var. | R-028 |
| `genkei.normalize.sec` | Reads SEC raw blobs and dispatches by `endpoint_name` prefix into `sec.companies` (upsert, FK target), `sec.filings` (one row per filing, recent + history), `sec.facts` (one row per XBRL `taxonomy:concept` × unit × `(period_start, period_end)` × accession; upsert key matches PK `(cik, concept, unit, period_start, period_end, accession_number)`). | R-028 |
| `genkei.ingest.coingecko` | CoinGecko collector. Reads `coingecko_id` from `crypto:` in `config/watchlists.yml` (primary + secondary tiers). Daily mode fetches `/coins/{id}` metadata + `/coins/{id}/market_chart?days=365&interval=daily` for the rolling Public/Demo historical window. Keyless mode is first-class: missing/blank `COINGECKO_API_KEY` uses the public host, no auth header, and `KEYLESS_RATE_LIMIT = per_minute(5)`; Demo keys use `x-cg-demo-api-key` and `per_minute(25)`. `--backfill --since YYYY-MM-DD` requires `COINGECKO_API_TIER=pro` plus a key, and uses `/market_chart/range` in 365-day chunks. `collect(api_key=_USE_ENV)` reads env; explicit `api_key=None` forces keyless. | R-029 |
| `genkei.normalize.coingecko` | Reads CoinGecko raw blobs and dispatches by prefix into `coingecko.coins` (upsert) and `coingecko.market_data` (zips the three parallel `prices` / `market_caps` / `total_volumes` arrays by timestamp, emits rows only where all three align — G-024). | R-029 |
| `genkei.cli` | Typer-based CLI. Top-level commands per data domain (D-019): `prices`, `filings`, `tvl`, `macro`, `news`, `watchlist`, `query`, plus the 15+ analysis surfaces (insiders / insider-clusters / holdings / crowding / eight-k-impact / revenue-divergence / relative-strength / tvl-drawdown / etf-flows / cot / stablecoin-flow / whales / macro-regime / signals / backtest). All 22 commands are live as of B-043 (Phase 3 complete). Reads `GENKEI_DATABASE_URL` via `genkei.common.db`. `--json` per-subcommand for agent consumption. Watchlist resolution centralized in `genkei.common.watchlist`. | R-031 (B-037+B-038+B-039) |

### Process layer

| Piece | What | Where to learn more |
|---|---|---|
| **Backlog hygiene** | `docs/backlog.md` (43 open) + `docs/resolved.md` (78 entries). Updated via the `update-backlog` skill after meaningful commits. | `docs/backlog.md` |
| **Mission queue** | `missions/pending/` and `missions/done/`. Async / overnight execution loop driven by the `run-missions` skill. | `docs/missions.md`, R-005 |
| **Test fixture** | `tests/_postgres.py` — singleton TimescaleDB testcontainer; `postgres_required` decorator gracefully skips when Docker absent. `truncate_all()` for cleanup between tests that go through real `db` helpers. | R-016 |
| **Test counts** | ~1,465 total — most unit + a growing integration tail (skip locally when Docker absent; CI runs them all). | R-023 |
| **CI workflows** | `tests.yml` (push to main + PRs) plus per-source daily crons on the self-hosted runner: `defillama-daily.yml`, `coingecko-daily.yml`, `fred-daily.yml`, `sec-daily.yml`, `yahoo-daily.yml`, `cftc-weekly.yml`, `ishares-daily.yml`, `sui-staking-daily.yml`, `sui-unlocks-daily.yml`, `onchain-staking-daily.yml`, `etherscan-whales-daily.yml`, `coinbase-daily.yml`. Reliability monitors: `workflow-failure-alert.yml` (B-071), `ingest-staleness-check.yml` (B-071), and `ingest-heartbeat.yml` (B-119). | R-019, R-020 |
| **PR conventions** | Short PRs. `## Summary` + `## Test plan`. No enumerated change lists, no footers. | `CLAUDE.md` |

---

## How it runs

### Daily pipeline (production)

Triggered by GitHub Actions `defillama-daily.yml` at **10:30 UTC** on the self-hosted runner:

```
1. checkout repo
2. setup-python@v5  (3.12)
3. pip install -e .
4. alembic upgrade head            (apply any new migrations)
5. python -m genkei.ingest.defillama    (raw blobs → meta.raw_blobs)
6. python -m genkei.normalize.defillama (raw blobs → defillama.*)
```

Each step writes to `meta.ingest_runs`. The normalizer's run carries `metadata.source_run_id` pointing at the collector run it consumed — the audit chain reads:

```
defillama.<table>.ingest_run_id → meta.ingest_runs (normalizer run)
                                  metadata.source_run_id → meta.ingest_runs (collector run)
                                                            ← meta.raw_blobs.ingest_run_id
```

Manual re-trigger: **Actions → DeFiLlama Daily Brief → Run workflow** in the GitHub UI, or `gh workflow run "DeFiLlama Daily Brief"` from a terminal.

### Local development

```bash
# .env (gitignored)
GENKEI_DATABASE_URL=postgresql+psycopg://<user>:<password>@<beelink-host>:5440/<db>

# in repo root
.venv/bin/pip install -e ".[dev]"
.venv/bin/alembic upgrade head

# run the pipeline locally (writes to the same homelab Postgres)
.venv/bin/python -m genkei.ingest.defillama
.venv/bin/python -m genkei.normalize.defillama

# tests
.venv/bin/python -m unittest discover -s tests
```

Integration tests need Docker — they spin up an ephemeral TimescaleDB container via testcontainers. When Docker isn't available, those 16 tests skip cleanly and the unit suite still runs.

### Inspecting the lake

```sql
-- Latest ingest activity
SELECT id, source, endpoint, status, rows_written, started_at
FROM meta.ingest_runs
ORDER BY started_at DESC LIMIT 10;

-- Row counts across the lake
SELECT
  (SELECT count(*) FROM defillama.protocols)   AS protocols,
  (SELECT count(*) FROM defillama.chain_tvl)   AS chain_tvl,
  (SELECT count(*) FROM defillama.stablecoins) AS stablecoins,
  (SELECT count(*) FROM defillama.prices)      AS prices;

-- Replay a normalizer run from raw blobs
SELECT endpoint_name, length(payload::text) FROM meta.raw_blobs
WHERE ingest_run_id = <collector_run_id>;
```

### Mission queue (async / overnight)

```bash
# The run-missions skill picks the oldest file in missions/pending/,
# works it through, moves it to missions/done/, and repeats until empty.
# Trigger via the skill or via /schedule for overnight execution.
```

Each mission is one markdown file: title, context, checklist of acceptance criteria. See `docs/missions.md` and the `_template.md` in the mission queue dir.

---

## Where we are on the roadmap

| Phase | Status | Notes |
|---|---|---|
| **Phase 0** — Foundation: Postgres + project scaffolding | ✅ complete | All 11 items resolved (R-005 through R-013, R-016, R-019). |
| **Phase 1** — Refactor DeFiLlama onto Postgres | ✅ effectively complete | 9/9 high-priority items done. Three medium items remain: B-020 (config-driven exclusion keywords) and B-023 (freshness check) are follow-ups when consumers need them; B-025 (daily brief fate) is a deferred decision. |
| **Phase 2** — Free-data ingesters with backfill | 🟡 substantially complete | Shipped: B-028 FRED, B-027 SEC EDGAR option B, B-034 CoinGecko, B-079 Form 4, B-080 Form 13F, B-081 DefiLlama protocol TVL, B-082 LINK on-chain staking, B-083 DefiLlama protocol fees + revenue, B-035 Coinbase public market data (Binance pivot), B-031 CFTC COT, B-085 stablecoin sparsity diagnosed, B-092 Yahoo equity OHLCV, B-102 SPY/QQQ/IWM benchmarks, B-105 spot ETF activity (Yahoo pivot), B-107 spot ETF true net flow (iShares product-screener), B-106 ETH whale-flow tracker, B-088 Sui validator + staking flow, B-089 SUI Community Reserves unlock schedule, B-086 Chainlink v0.2 staking surface mapped, B-029 BEA NIPA (previously shipped), B-030 Treasury Fiscal Data. Still open: EIA (B-032), GDELT (B-033), per-source docs (B-036), oracle market share (B-084 — paid), CME futures OI (B-104 — TOS-blocked), multi-issuer ETF expansion (B-113), 10-Q quarter-end ETF shares (B-114), the 7 paywalled SUI allocations (B-115), Chainlink v0.1 legacy staking pool (B-116). |
| **Phase 3** — Custom CLI | ✅ 22/22 done | `prices`, `filings`, `tvl`, `macro`, `macro-regime`, `watchlist`, `insiders`, `insider-clusters`, `holdings`, `crowding`, `eight-k-impact`, `revenue-divergence`, `relative-strength`, `tvl-drawdown`, `etf-flows`, `cot`, `stablecoin-flow`, `whales`, `news` (B-043 — GDELT GKG clusters by `(date, source)` with watchlist / theme / topic / tone filters), `signals`, `backtest`, `query`. `--json` on every command for agent consumption. |
| **Phase 4** — Agent layer | 🟡 foundation laid | `/research` skill walks `prompts/research-methodology.md` and lands decision files in `docs/research/decisions/`; `/reflect-decisions` walks resolved entries. Multiple decisions on file (LINK, CRM activist, SUI position assessment, ETH, SOL). B-051/052/053 (delivery surface, open-questions log, ingest-health summary) still open. |
| **Phase 5** — Experiments framework | 🟢 9/11 done | `insider_clusters` (B-060), `protocol_revenue` / `revenue-divergence` (B-062), `relative_strength` (B-090), `macro_regime` (B-059), `tvl_drawdown` (B-058), `eight_k_impact` (B-057), `crowding_monitor` (B-061), `watchlist_scoring` (B-065), `news_sentiment` (B-056 — first experiment consuming the GDELT data + `genkei news` surface from B-033/B-043; reports `insufficient_data` until the cron accumulates ≥30 aligned (sentiment, return) pairs). Notebook reproducibility pattern (B-054/055) deferred — CLI + experiments modules have absorbed the experiment shape. Open: B-063 experiment template, B-066 regime view exposed via CLI as a query flag. |
| **Phase 6** — Inefficiency-detection signals | 🟢 substantially advanced | Cross-source correlation engine (B-064) with store + rules + correlator, plus five emitters wired (insider_clusters, crowding, eight_k, tvl_drawdown, relative_strength × {crypto, equity via B-111 / B-112}); `analytics.crypto_relative_strength` (B-090) + `analytics.macro_regime_per_date` (B-059) views. Stack-outcome backtest (B-101) + SPY/BTC benchmark adjustment (B-100). Watchlist scoring rubric (B-065) with `meta.signals` store. Still open: B-066 regime classifier surfaced in `genkei macro --regime`, B-067 multi-day trend Postgres views, B-068 alert engine, B-069 anomaly detection, B-096 macro-regime emitter, B-097 watchlist-scoring emitter, B-099 correlator age-decay weighting. |
| **Phase 7** — Operations & hardening | 🟢 substantially advanced | B-077 self-hosted runner done; B-074 architecture diagram + README refresh done; B-070 Postgres backup posture documented; B-071 workflow-failure + ingest-staleness alerting shipped (`workflow-failure-alert.yml`, `ingest-staleness-check.yml`); B-072 schema-drift detection shipped (`genkei.common.schema_drift`). Still open: B-073 secrets policy, B-075 license audit, B-076 quota tracking. |

See `docs/backlog.md` for the live list, `docs/resolved.md` for the chronicle.

### Open architectural decisions

Tracked as backlog items so they don't block forward motion:

- **B-025** — Fate of the legacy daily-brief markdown report. The retired `build_daily_report.py` reads JSON files that no longer exist; pending decision to either rewrite against Postgres or retire entirely.
- **B-037** — CLI tool name (working: `genkei`).

---

## Reference

### Per-component deeper docs

| Doc | What |
|---|---|
| `docs/storage.md` | Schema strategy, Alembic conventions, naming + provenance rules |
| `docs/repo-layout.md` | Why `src/genkei/{common,ingest,normalize,cli,experiments,reports}/` |
| `docs/infrastructure.md` | Homelab Postgres, network reachability, self-hosted runner runbook |
| `docs/missions.md` | Mission queue format, manual + scheduled invocation, monitoring |
| `docs/defillama-mvp.md` | DeFiLlama-specific pipeline notes (legacy; predates Postgres refactor) |
| `docs/defillama-daily-review.md` | Acceptance gates for the legacy markdown brief — historical; brief retired in B-025 / D-024 |
| `docs/backlog.md` | Open items, 43 entries across 8 phases |
| `docs/resolved.md` | Completed milestones, 78 entries with evidence |
| `docs/scoring.md` | Watchlist scoring rubric (B-065) — per-asset composite scoring formula + components |
| `docs/experiments/macro-regime.md` | B-059 design notes — inputs, regime label definitions, view shape |
| `docs/experiments/tvl-drawdown.md` | B-058 design notes |
| `docs/experiments/8k-filing-impact.md` | B-057 design notes |
| `docs/experiments/13f-crowding-monitor.md` | B-061 design notes |
| `docs/experiments/cross-source-signals.md` | B-064 correlator + rule pack + emitter contract |
| `docs/experiments/stack-outcome-backtest.md` | B-101 + B-100 backtest harness |
| `docs/sources/eth-whale-addresses.md` | B-106 ETH whale wallet curation methodology |
| `docs/sources/spot-etf-net-flow.md` | B-105 / B-107 spot ETF investigation notes |
| `docs/sources/sui-unlocks.md` | B-089 + B-115 SUI unlock-source survey |
| `docs/ingesters/coinbase.md` | Coinbase public market data ingester |
| `docs/ingesters/yahoo.md` | Yahoo equities + benchmarks ingester (B-092 / B-102) |
| `docs/backups.md` | B-070 Postgres backup posture |
| `docs/research/README.md` | Investment-research decision log + frontmatter contract |
| `prompts/research-methodology.md` | The checklist `/research` walks per session |
| `prompts/reflect-on-decisions.md` | The outcome-pairing cycle `/reflect-decisions` runs |

### External references

- `~/.claude/skills/server-info/` — homelab Postgres connection specs, network topology, container inventory (local-only, never committed).
- `~/.claude/skills/pr-body/` — PR body drafter (model-invocable).
- `~/.claude/skills/pr/` — PR opener (user-invoked via `/pr`).
- `~/.claude/skills/run-missions/` — mission queue runner.
- `~/.claude/skills/update-backlog/` — backlog/resolved hygiene.

### Conventions cheat sheet

- **Git identity:** `Michael Tookes <michaeltookes92@gmail.com>` (set via `git config user.name/.email` on each fresh worktree).
- **Branches:** feature branches; never push to `main`. Default branch is `main`.
- **Commits:** explain the *why*, not the *what*. Co-author trailer fine on automated commits.
- **Tests gate:** `python3 -m unittest discover -s tests` must pass before any push.
- **Secrets:** never in repo. `.env` (gitignored) locally, GH Actions secrets for CI, `.env.example` lists every variable.
- **Raw vendor data:** never committed. Postgres is the system of record.
- **Reports:** commit markdown to `reports/`. The durable artifact is the weekly signal digest at `reports/signals/weekly-<date>.md` (D-024); the legacy DeFiLlama daily brief is retired (B-025).

---

# Decision log

Append-only. Each entry: **what**, **why**, **alternative considered**, **what would change our mind**. Newest at the bottom.

### D-001 — TimescaleDB over plain PG + manual partitioning
**Date:** 2026-05-07 · **In:** R-007/R-014, `docs/storage.md`
**Decision:** TimescaleDB hypertables for every time-series fact table.
**Why:** Native compression (~10x on numeric series), continuous aggregates exactly map to the rolling-window tables Phase 6 will need (B-067), retention policies built in. Hypertables look like regular tables to most code — minimal API friction.
**Alternative:** Plain PG with `pg_partman` + hand-rolled rollup tables. Rejected because we'd reinvent what Timescale gives free.
**What would change our mind:** Timescale licensing changes that affect features we use, or compression turning out worse than 5x in practice.

### D-002 — Per-source schemas (`defillama.*`, `sec.*`, ...) over a single schema
**Date:** 2026-05-07 · **In:** R-006, `docs/storage.md`
**Decision:** Each source gets its own schema; `meta.*` for operational tables; `analytics.*` for cross-source views.
**Why:** Blast radius — dropping/resetting one source's tables doesn't risk siblings. Permissions can scope per-source. Cross-source joins are explicit in `analytics.*` rather than implicit in queries.
**Alternative:** Single `public` schema with table-name prefixes. Rejected — flat namespace doesn't scale to 10+ sources.

### D-003 — Hand-written Alembic migrations only (no autogen)
**Date:** 2026-05-08 · **In:** R-008, `docs/storage.md`
**Decision:** Every migration in `migrations/versions/YYYYMMDD_<slug>.py` is hand-written. `target_metadata = None` in `env.py`.
**Why:** Autogen produces noisy diffs against TimescaleDB hypertables and continuous aggregates; debugging the autogen output is more work than writing the SQL.
**Alternative:** SQLAlchemy ORM models + autogen. Deferred — `sqlalchemy` is not even a runtime dep yet (we use raw psycopg).

### D-004 — `src/genkei/` layout instead of flat `scripts/`
**Date:** 2026-05-07 · **In:** R-019, `docs/repo-layout.md`
**Decision:** Single top-level package `src/genkei/{common,ingest,normalize,cli,experiments,reports}/`. CLI is a console script via `pyproject.toml`.
**Why:** The CLI is the primary user-facing artifact and must be installable as a real binary. The `src/` (vs `genkei/` at repo root) choice forces tests to import from the *installed* package, catching packaging bugs early.
**Alternative:** Flat `scripts/`. Rejected — doesn't accommodate the CLI binary or shared `common/` cleanly.

### D-005 — Postgres `meta.raw_blobs` over filesystem `data/raw/` for audit trail
**Date:** 2026-05-09 · **In:** R-017
**Decision:** Raw API payloads land as JSONB rows in `meta.raw_blobs` with FK back to `meta.ingest_runs`.
**Why:** GH-hosted runners would lose `data/raw/` files between jobs (we don't commit raw vendor data). Postgres is already the system of record per CLAUDE.md. Co-locating raw with `meta.ingest_runs` makes audit/replay a single SQL query.
**Alternative:** Object storage (S3/R2/B2) for the raw blobs. Rejected today — adds a new dependency and no immediate need; revisit if blob volume passes ~10 GB or we want public hostable raw.

### D-006 — Data-lake-shaped normalizer, not report-shaped
**Date:** 2026-05-09 · **In:** R-018
**Decision:** The normalizer writes the raw shape of each endpoint into `defillama.*` tables. No derived classifications (momentum, trend, zombie risk) at this layer.
**Why:** Those classifications were report-specific and gummed up the normalizer with consumer logic. Lake stays general; downstream consumers (CLI, experiments, reports) re-derive what they need from raw values.
**Tradeoff:** The legacy markdown brief (`build_daily_report.py`) is broken until B-025 rewrites it against Postgres. That's an explicit choice — we don't want the lake shape to be hostage to one consumer.
**What would change our mind:** If we end up duplicating the same derived classification across 3+ consumers, lift it into a Postgres view or `analytics.*` materialized view (don't put it back in the normalizer).

### D-007 — testcontainers harness for Postgres integration tests, not a dedicated test DB
**Date:** 2026-05-09 · **In:** R-016
**Decision:** Spin up an ephemeral `timescale/timescaledb:2.26.4-pg16` container per test process. Singleton, reused across tests in a session for speed.
**Why:** Zero coupling to homelab; same fixture works in CI; full schema reset between runs is trivial. Skip cleanly when Docker isn't present so the offline mock-based suite still runs everywhere.
**Alternative:** Dedicated `genkei_capital_test` database on the Beelink. Rejected — couples local tests to homelab reachability, harder to parallelize, CI can't reach it (B-077 territory).

### D-008 — Self-hosted GH Actions runner over Cloudflare TCP tunnel
**Date:** 2026-05-09 · **In:** R-020, `docs/infrastructure.md`
**Decision:** Run a self-hosted runner on the Beelink with direct Docker-network access to `genkeicapital-postgres` via `mission_control_net`.
**Why:** Lowest moving parts, no public exposure of Postgres, simplest mental model. Beelink already runs the workload anyway.
**Alternative:** Cloudflare TCP tunnel exposing Postgres at a public hostname behind an Access policy. Building blocks exist (`cloudflared` already on the Beelink). Adds blast-radius (any auth misconfig exposes the DB).
**What would change our mind:** If we want CI to run on cloud runners (faster, parallelizable), the tunnel becomes worth the cost.

### D-009 — `myoung34/github-runner:latest` over a pinned version
**Date:** 2026-05-10 · **In:** R-020 (codified in commit `353f01d`)
**Decision:** Track the `:latest` tag for the runner image.
**Why:** GitHub deprecates older runner versions on a rolling cadence. A pinned tag silently goes Offline once deprecated (we hit this with `2.320.0` on first install — registered fine but couldn't receive jobs). `DISABLE_AUTO_UPDATE=true` still prevents the runner self-updating mid-job; image-level updates only happen on `docker compose pull`.
**Alternative:** Pin to a specific recent version and accept periodic deprecation breakage. Rejected — too easy to forget the rotation.
**What would change our mind:** If `:latest` ever ships a backwards-incompatible config change that breaks our compose template, switch to N-1 pinning.

### D-010 — Compression policy + raw-blob retention as Phase 1 hygiene (not Phase 7 hardening)
**Date:** 2026-05-10 · **In:** B-019 design (this branch)
**Decision:** When B-019 backfill lands, also enable TimescaleDB compression on hypertable chunks > 30 days old, and add 90-day retention on `meta.raw_blobs`.
**Why:** Backfill takes worst-case raw size from ~150 MB to ~6 GB. Compression brings it back to ~1 GB. Without these policies the homelab disk would still be fine (67 GB free) but data hygiene gets worse over time and harder to retrofit later.
**Alternative:** Defer to B-070/B-074 in Phase 7. Rejected — the moment to add compression is when the data shape stabilizes, which is now.

### D-011 — Backfill resumability via `meta.raw_blobs.url` + 14-day window
**Date:** 2026-05-10 · **In:** R-023, `genkei.ingest.defillama._cached_blob`
**Decision:** Each prospective backfill fetch checks `meta.raw_blobs` for a row with the same URL fetched within the last 14 days; if found, skip the HTTP call and copy the cached blob into the current run.
**Why:** Backfill runs are long (~25 min for protocols at 5 req/sec). A crashed run that re-runs from scratch is wasteful and burns rate-limit budget. URL-based resume means we don't need a separate progress table or per-blob cursor — `meta.raw_blobs` is already the system of record for "what we've fetched." Copying skipped blobs into the active ingest run keeps `normalize_backfill(source_run_id=...)` complete even when every HTTP call was skipped.
**Alternative:** A side `meta.backfill_progress` table tracking per-(endpoint, key, date) tuples. Rejected — adds a second source of truth for what's already on disk; the URL is unique enough.
**14-day window why:** Long enough to span a multi-day backfill restart; short enough that a deliberate refresh (re-running the same `--since` weeks later) actually re-pulls fresh data.

### D-012 — Decoupled backfill: collector writes blobs, normalizer dispatches
**Date:** 2026-05-10 · **In:** R-023
**Decision:** `python -m genkei.ingest.defillama --backfill --since X` only fetches and lands raw blobs (with backfill-specific `endpoint_name` prefixes). A second invocation `python -m genkei.normalize.defillama --backfill` reads those blobs and dispatches by prefix into the lake tables.
**Why:** Mirrors the existing collect/normalize split — same mental model for daily and backfill flows. Lets you re-normalize without re-fetching, debug normalizer issues against fixed input, and parallelize fetching across multiple invocations if rate-limit headroom exists later.
**Alternative:** One-shot `backfill` that collects + normalizes in a single pass. Rejected — couples two concerns whose failure modes differ (network failure vs schema mismatch vs upsert error).

### D-013 — FRED observations are vintage-aware (PK includes `realtime_start`)
**Date:** 2026-05-10 · **In:** R-027, `migrations/versions/20260510_create_fred_schema.py`
**Decision:** `fred.observations` PK is `(series_id, ts, realtime_start)`. Each FRED revision lands as its own row keyed on the date the value first became current. `realtime_end` carries forward (`9999-12-31` for the current value).
**Why:** Backtest correctness. FRED revises macro values constantly — Q1 GDP first published in late April gets revised in May, June, and again at the annual revision. A latest-only schema silently corrupts as-of backtests by leaking revised data into historical "knowledge." For a research-desk operating *as if* a real fund (CLAUDE.md), this matters.
**Alternative:** Latest-only PK `(series_id, ts)` with overwrites. Rejected — smaller storage but lossy; retrofitting from latest-only to vintage-aware requires re-fetching everything.
**What would change our mind:** If FRED storage outgrows the homelab (unlikely — revisions are sparse), we could prune old vintages older than N years.

**Amendment (2026-05-16, G-031):** the schema decision stays vintage-aware. The collector no longer relies on an all-history realtime window or FRED's implicit current-vintage default; it fetches vintage dates explicitly and requests observations in bounded `vintage_dates` chunks so `realtime_start` remains a real revision key.

### D-014 — FRED is single-mode: no `--backfill` flag, daily run pulls full history
**Date:** 2026-05-10 · **In:** R-027
**Decision:** `python -m genkei.ingest.fred` always fetches full-vintage observations for every configured series. There is no separate `--backfill` flag.
**Why:** FRED's `/series/observations` endpoint returns the entire history per call. No date-walker needed; daily and backfill are the same code path. The vintage-aware schema (D-013) means re-running just upserts any new revisions as new rows.
**Alternative:** Mirror DeFiLlama's `--backfill --since` flag for consistency. Rejected — adds a code path with no consumer; the FRED endpoint shape doesn't reward it. Per-source ingester shape can differ from per-source ingester shape; that's fine.

**Amendment (2026-05-16, G-031):** "full history per run" is implemented as a vintage-date chunk walk. Daily and backfill remain the same code path, and the no-`--backfill`-flag decision stands.

### D-015 — SEC EDGAR scope: option B (submissions + XBRL company facts) now, Form 4/13F as follow-ups
**Date:** 2026-05-10 · **In:** R-028, B-079, B-080
**Decision:** B-027's first cut lands two API surfaces — `/submissions/CIK{cik}.json` (filing index + company metadata) and `/api/xbrl/companyfacts/CIK{cik}.json` (XBRL fact history). Per-filing structured payloads (Form 4 insider transactions, 13F institutional holdings) are split into separate backlog items B-079 and B-080.
**Why:** XBRL facts are *self-describing* — concepts like `us-gaap:Revenues` and `us-gaap:NetIncomeLoss` mean what they say across companies. Form 4 and 13F payloads, by contrast, are *opinionated* — Form 4 has ~15 fields per insider transaction, 13F has cusip/value/shares/putCall plus the 13F-HR vs 13F-NT distinction. Without a concrete experiment driving the schema, we'd guess wrong about which fields to pull and how to shape them. Pick those parsers up driven by B-060 (insider buying) and B-061 (13F crowding) so the schemas are shaped by concrete queries.
**Alternative:** Build all three surfaces in one PR. Rejected — triples the API surface (= triples the live-smoke gotcha exposure) for code with no consumer; storage hit for Form 4/13F backfill across 28 equities × decades is millions of rows queried against speculative schemas.
**What would change our mind:** If B-060/B-061 get prioritized to land before any other Phase 5 experiment, lift them ahead of the other Phase 2 sources.

### D-016 — XBRL facts stored as `(cik, concept, unit, period_start, period_end, accession_number)` PK, not collapsed by latest filing
**Date:** 2026-05-10 · **In:** R-028
**Decision:** `sec.facts` PK includes `accession_number`. The same `(concept, period)` reported by both a 10-Q and the subsequent 10-K lands as two rows.
**Why:** Restatements + as-of backtests. SEC permits restatements of prior-period facts; if we collapsed to "latest filing wins," the same kind of vintage-loss problem D-013 solves for FRED would bite us here. With accession_number in the PK, every reported version is preserved; consumers can filter to the most recent filing per `(concept, period)` at query time when desired.
**Alternative:** PK without accession_number, latest filing overwrites prior. Rejected for the same reason as the FRED vintage decision: lossy and not retrofittable without a re-pull.

### D-017 — Claude Code is the agent harness; we are not building a custom Python framework
**Date:** 2026-05-10 · **In:** R-030 (resolves B-048), `~/.claude/plans/noble-twirling-nygaard.md`
**Decision:** The Phase 4 agent layer runs *in Claude Code*. We do not build a Python LLM-calling framework, do not adopt LangGraph, do not adopt TradingAgents wholesale, do not adopt Pi as a replacement harness. The "agent" is a Claude Code session reading a structured methodology and querying the lake via the CLI (Phase 3) and the Bash tool.
**Why:** The user already runs Claude Code daily in this repo. Building a parallel LLM-calling system means rebuilding session management, tool invocation, and conversation state — and paying API tokens for capability already covered by their Claude Code subscription. The Q&A research use case (D-018 below) is fundamentally single-step; multi-agent orchestration adds infrastructure without payoff.
**Alternatives considered and rejected:**
- **TradingAgents (LangGraph + 11-agent framework)**: real and well-engineered, but its agents fetch live data per run, invalidating the data-lake investment. Multi-agent debate also adds verbose token spend without clear lift for our use case.
- **Pi Agent (TypeScript multi-provider CLI with markdown agent registry, 48K stars)**: genuinely good for *coding* task decomposition (scout → planner → worker → reviewer) and would enable a TradingAgents-style markdown-personas setup. Skipped because (a) switching harnesses costs daily-workflow disruption, (b) Pi's wheelhouse is coding tasks, not research Q&A, (c) the multi-agent shape isn't currently in scope.
- **Custom Python agent (Anthropic SDK direct calls)**: rebuilds what Claude Code already does, costs API tokens, no benefit at our scope.
**What would change our mind:** If/when scope expands to multi-agent task decomposition (e.g., scheduled deep-dives that scout → analyze in parallel → synthesize), Pi re-enters as the leading candidate. The deferred-not-rejected stance keeps the door open without committing now.

### D-018 — Three patterns borrowed from TradingAgents: structured methodology, decision log + reflection, two-phase analysis
**Date:** 2026-05-10 · **In:** D-017's plan; will land alongside Phase 3 CLI in B-049/B-050
**Decision:** Even with Claude Code as the harness, three patterns from TradingAgents are worth adopting:
1. **Structured research methodology.** A `prompts/research-methodology.md` checklist Claude reads at the start of any research session: frame the question → macro context → asset fundamentals → flow/positioning → cross-source signals → counter-thesis check → conclusion + horizon tag → decision-log entry. Replaces ad-hoc reasoning.
2. **Append-only decision log + outcome reflection.** Each research session ends by appending to `docs/research/decisions/<date>-<topic>.md` with status `pending`. A separate `prompts/reflect-on-decisions.md` periodically pairs past conclusions with realized returns (alpha vs SPY for equities, vs BTC for crypto) and updates each entry with an outcome block + 2-3 sentence reflection. Future sessions inject the most recent reflections into context.
3. **Two-phase analysis → risk separation in the methodology.** Even within a single agent, the methodology has two distinct phases: (a) "what's the case for/against this?" and (b) "what could make this thesis wrong?" Prevents premature consensus.
**Why:** These three are TradingAgents' actual contributions — the multi-agent debate is mostly theatre. The methodology + decision log + reflection cycle works regardless of whether you have one agent or eleven.
**What we're explicitly NOT borrowing:** the multi-agent framework, the per-run live API fetching, the LangGraph orchestration. All three are antithetical to a Claude-Code + data-lake setup.
**Sequencing:** lands after Phase 3 CLI (B-037 → ~B-044) is built enough for Claude to query the lake ergonomically. The CLI is the actual prerequisite; without it, the methodology has no useful tools to invoke.

### D-019 — Typer over Click for the CLI
**Date:** 2026-05-10 · **In:** B-038, `src/genkei/cli/__init__.py`
**Decision:** The `genkei` CLI is built on Typer (which sits on Click). Subcommands are top-level commands registered via `app.command(...)`, not nested sub-apps with callbacks.
**Why:** Type-hint-driven Typer matches the rest of the codebase's style (we use type hints everywhere). Auto-generates `--help` from docstrings + signatures with no boilerplate. Click is more battle-tested but its decorator API is verbose by comparison and we'd hand-write things Typer derives from annotations.
**Alternative:** Plain Click. Rejected — extra boilerplate without clear benefit at our scope. We get Click's stability transitively (Typer is built on it).
**Pattern note:** Real subcommands export a callable function (e.g. `prices_cmd`) and `__init__.py` registers it via `app.command("prices")(prices.prices_cmd)`. Sub-apps with callbacks (`app.add_typer(sub_app, ...)`) work for grouped subcommands but produce confusing option-binding behaviour for single-action commands. Top-level command registration is the canonical shape; reserve `add_typer` for actual subcommand groups (e.g. `genkei query sql ...` later).

### D-020 — CoinGecko collector supports a first-class keyless mode
**Date:** 2026-05-16 · **In:** `src/genkei/ingest/coingecko.py`, fix-ingest-pipelines
**Decision:** `COINGECKO_API_KEY` is optional. Unset / blank / whitespace-only env values fall through to **keyless mode**: public `api.coingecko.com` host, no auth header, `KEYLESS_RATE_LIMIT = per_minute(5)`. Demo mode (with key) keeps `per_minute(25)`. Pro and `--backfill` still require a key and fail fast otherwise.
**Why:** The user opted to stay on the free tier (no key registration). The previously-merged collector hard-required a key and silently failed every daily run for ~3 days, leaving `coingecko.market_data` empty. Keyless is supported by CoinGecko's public API at a stricter rate; 14 daily calls take ~3 min keyless vs ~30s demo — acceptable for daily ingest. Documented in docstring + module constants.
**Alternative:** Register a free demo key. Rejected for now per user preference; the keyless path is intentionally cheap to revert if/when a key is added.
**Sentinel:** `collect(api_key=_USE_ENV)` distinguishes "look up env" from caller-passed `None` (explicit keyless) so unit tests can force keyless without env juggling.

### D-021 — Cross-source derived signals: pure-function detector + Postgres view + CLI surface, always paired
**Date:** 2026-05-20 · **In:** B-062 (`revenue-divergence`), B-090 (`relative-strength`)
**Decision:** Every Phase 5/6 derived signal lands in three places at once:
1. **Pure functions** in `src/genkei/experiments/<name>.py` — operate on plain dataclasses, no DB. The math, unit-tested on synthetic series.
2. **Postgres view** in the `analytics.*` schema (when the math fits SQL cleanly) — computed live against the lake; queryable by `genkei query` without writing Python.
3. **CLI subcommand** in `src/genkei/cli/<name>.py` — resolves human tickers (BTC, SUI) to coingecko_ids via the watchlist, calls the lake-loader, formats output. `--json` always.
**Why:** Dual implementation (SQL + Python) lets us cross-check on synthetic data — if the view's output diverges from the pure-function's, one of them is wrong. The CLI gives the agent + human one consistent surface. The pure-function path also keeps the methodology in `experiments/` where future experiments can compose it.
**Alternative:** Just the SQL view, or just the Python pure function. Rejected — SQL-only loses unit-testability; Python-only loses the ad-hoc `genkei query` surface.
**Materialized vs live views:** Both `analytics.crypto_relative_strength` (B-090) and any future Phase 6 view start as **live** views (computed on each query). Promote to `MATERIALIZED VIEW` with a refresh job only when the math gets expensive enough to matter — premature materialization adds a refresh-policy headache no current view earns.

### D-022 — Daily collector vs one-shot backfill is the canonical dual-mode for "current state + history" endpoints
**Date:** 2026-05-21 · **In:** B-085 (`src/genkei/ingest/defillama.py` docstring runbook)
**Decision:** When an external API splits its data into a "current state" endpoint (cheap, daily) and a "per-entity history" endpoint (expensive, multi-year), the collector ships both paths in the same module:
- Daily collector writes one row per (entity, sub-key) snapshot at `ts = now()` — keeps the latest known state fresh.
- `--backfill --since YYYY-MM-DD --endpoint <name>` walks the per-entity history endpoint for every known asset id (from the daily collector's prior runs), lands per-day rows with the historical date as `ts`.
Both write to the same destination table; the natural `(entity, …, ts)` PK keeps re-runs idempotent. If a new entity first appears in the daily collector after the historical backfill ran, re-run the backfill to land its history too.
**Why:** Surfaced as a recurring root cause — B-085 was the second research session blocked by this gap. Codifying the runbook + the dual-mode in the module docstring makes the next gap (a new endpoint with the same shape) impossible to miss.
**Applies to:** DefiLlama `/stablecoins` + `/stablecoin/{id}`, `/protocols` + `/protocol/{slug}`, `/prices/current` + `/coins/prices/historical/{ts}`. The pattern is general — every future ingester that has both shapes follows it.

### D-023 — `coingecko_id` mappings on `ProtocolEntry` are picked up by the CoinGecko collector, decoupled from "tradeable assets in `crypto:`"
**Date:** 2026-05-20 · **In:** B-091, `src/genkei/ingest/coingecko.py::load_coins`
**Decision:** The CoinGecko collector's `load_coins(path)` returns the **union** of every `coingecko_id` found in the watchlist's `crypto:` section AND the `protocols:` section, deduped by id. A protocol-derived `CoinTarget` carries an empty `symbol` (the protocols section is keyed on the DefiLlama slug, not the token symbol) — the authoritative symbol + name still land in `coingecko.coins` from the API response itself.
**Why:** B-087 added 6 Sui-native protocols with `coingecko_id` mappings; B-091's smoke caught that 12 of 14 protocol-side mappings returned `insufficient-data` in `revenue-divergence` because the tokens weren't being fetched. Two reasonable shapes considered: (a) extend `load_coins` to union; (b) add a new `crypto.data-only` tier to the watchlist. Picked (a) — pure data-flow change, no investment intent implied. "We want the data" and "we want to trade the asset" are different concerns; the watchlist's `crypto:` section is for trade-eligible assets, `protocols:` is for tracked protocols. The union just says "fetch whatever has a coingecko_id mapping anywhere."
**Alternative considered:** Add tokens to `crypto:` directly. Rejected — that implies investment intent on assets we only want for data observability.

### D-024 — Output channel: weekly signal digest in `reports/signals/`; legacy daily brief retired
**Date:** 2026-06-19 · **In:** B-122 (resolves B-051; closes B-001/B-002/B-025), `src/genkei/reports/signal_digest.py`
**Decision:** Research/signal output lands as **markdown committed in-repo under `reports/`**, on a **weekly** cadence. `genkei.reports.signal_digest` renders the trailing-week correlator output (`genkei signals`) into `reports/signals/weekly-<date>.md`, grouped by horizon tag (`<asset_class>:<sleeve>`), with a lake-health footer. Cadence is weekly via a `/schedule` routine — the module is runner-agnostic, so a GH Actions cron works equally. Failure-mode: generation is idempotent (dated filename, re-runnable), and a failed run surfaces through the existing B-119 Discord/issue alerting rather than silently dropping. The **legacy DeFiLlama daily markdown brief is retired** (it was already broken — read pre-Postgres JSON that no longer exists); the lake is the system of record, and on-demand `genkei` queries plus the weekly digest supersede a per-source daily brief.
**Why:** `reports/` was empty, so the research decision log was the lake's *only* durable artifact — a miscalibrated reflection loop (B-117/B-118) had no second artifact to catch it, and Phase 6 emitters were writing signals to `meta.signal_events` with nowhere human-readable to land. Weekly (not daily) matches the "lake is the asset, reports are emergent UIs" philosophy and the weekend-session rhythm; the very first artifact surfaced 11 stale/missing sources the decision log wouldn't have caught. Horizon tags already existed in the events (`<asset_class>:<sleeve>`), so the digest just renders them.
**Alternative considered:** (a) Daily cadence — rejected as noise for a long-horizon book and a return to the daily-brief model we'd moved away from. (b) Rebuild the DeFiLlama daily brief Postgres-backed — rejected as a second report surface to maintain with no clear consumer. (c) External surface (Mission Control / GitHub Discussions) — Mission Control is dead (OpenClaw, D-017); deferred, revisit if in-repo markdown becomes limiting.
**What would change our mind:** If signal volume grows enough that a weekly digest buries fresh stacks, add an event-driven per-stack artifact or shorten cadence; the renderer already separates pure formatting from delivery, so a different surface is a small change.

---

# Gotchas & lessons learned

Append-only. Each entry: **what bit us**, **how we resolved it**, **how to avoid it next time** (or: it's load-bearing now, here's the workaround).

### G-001 — Beelink is behind double-NAT; GH-hosted runners can't reach it
**Hit:** 2026-05-09 (during B-006 server-info loading)
**Symptom:** Any workflow targeting the homelab Postgres fails to connect from a GH-hosted runner.
**Resolution:** Self-hosted runner on the Beelink (R-020 / D-008).
**Avoid next time:** Default to self-hosted for any workload that touches the homelab. Cloud-hosted runners are fine for offline tests and pure-CPU work.

### G-002 — Local `.venv` is Python 3.9 but `pyproject.toml` requires `>=3.10`
**Hit:** 2026-05-09 (B-024 testcontainers install)
**Symptom:** `pip install -e ".[dev]"` fails with `Package 'genkei' requires a different Python: 3.9.6 not in '>=3.10'`.
**Resolution:** Install testcontainers directly into the 3.9 venv without the editable install (`pip install "testcontainers[postgres]"`). Tests still run because the modules just import.
**Why we tolerate it:** Recreating the venv against a newer Python is a separate task; CI uses 3.12 and exercises the install path properly. If you need to bump locally, `brew install python@3.12 && python3.12 -m venv .venv`.

### G-003 — Local Mac mini doesn't have Docker, so integration tests never run locally
**Hit:** 2026-05-09 (B-024 verification)
**Symptom:** `tests/_postgres.py::postgres_required` skips all 16 integration tests on the workstation; CI is the first place they execute.
**Resolution:** Document the gap explicitly in commit messages when shipping branches that add integration tests. Trust CI as the validation gate.
**Avoid next time:** Same — installing Docker on the Mac mini would unblock local validation but isn't on the critical path.

### G-004 — `myoung34/github-runner:2.320.0` was deprecated by GitHub on first install
**Hit:** 2026-05-10 (B-077 install)
**Symptom:** Runner registered successfully, then logged `Runner version v2.320.0 is deprecated and cannot receive messages` in a tight restart loop. GitHub UI showed it as Offline.
**Resolution:** Switch to `myoung34/github-runner:latest`, clear the persisted runner state volume, remove the stale runner from GitHub UI, re-bring up. Codified as D-009.
**Avoid next time:** Don't pin runner image versions; treat the image as a moving target.

### G-005 — DeFiLlama `/stablecoins` payload uses `chainCirculating`, not `chainBalances`
**Hit:** 2026-05-10 (B-077 smoke test)
**Symptom:** Workflow ran end-to-end but `defillama.stablecoins` landed 0 rows. Other tables populated correctly.
**Resolution:** The legacy `scripts/normalize_defillama.py` had `chainBalances` hardcoded — wrong field name carried into the new normalizer without verification. Fixed in commit `5365936` (read `chainCirculating`, fall back to `chainBalances` for safety).
**Avoid next time:** When refactoring, re-verify field names against a real API response — don't assume the legacy code's field names were ever correct.

### G-006 — Hypertables can't be reverted in place
**Hit:** 2026-05-09 (B-016 design)
**Symptom:** TimescaleDB doesn't expose a `drop_hypertable_in_place` — you'd need to copy rows to a plain table and swap.
**Resolution:** The hypertable migration's `downgrade()` raises `NotImplementedError` with a comment pointing at the parent schema migration (which drops the underlying table outright). To "remove the Timescale layer," roll back the schema migration.
**Avoid next time:** Same pattern for any future TimescaleDB-specific migration — own table creation in one revision, hypertable conversion in the next, and accept that downgrade goes via the parent.

### G-007 — `psycopg+SQLAlchemy` URL prefix needs stripping for plain libpq
**Hit:** 2026-05-08 (B-009 / B-010 wiring)
**Symptom:** `psycopg.connect("postgresql+psycopg://...")` fails with `missing "=" after ...` — `+psycopg` is a SQLAlchemy dialect prefix, not a libpq scheme.
**Resolution:** `genkei.common.db._resolve_url` strips the prefix. Alembic uses the SQLAlchemy form; everything else uses the plain form.
**Avoid next time:** Never copy a URL between Alembic config and direct-psycopg code without thinking about the prefix.

### G-008 — `meta.ingest_runs` lacks a `partial` status code path in `db.ingest_run()`
**Hit:** 2026-05-09 (B-017 collector design)
**Symptom:** The schema's CHECK constraint allows `('running', 'success', 'failed', 'partial')`, but the `ingest_run()` context manager only writes `success` or `failed`.
**Resolution:** Stash partial-endpoint info in `meta.ingest_runs.metadata.partial_endpoints` JSONB instead of a status code. The status remains `success` if no required endpoint failed; consumers query the metadata if they care about partial-ness.
**Avoid next time:** If we ever build a UI that depends on a status enum, we'll add a `mark_partial()` method on the IngestRun handle. For now, JSONB metadata is good enough.

### G-009 — `actions/checkout@v4` and `actions/setup-python@v5` use Node.js 20, deprecated by GitHub June 2026
**Hit:** 2026-05-10 (B-077 smoke test annotation)
**Symptom:** Workflow runs surface a non-fatal annotation: "Node.js 20 actions are deprecated. ... Actions will be forced to run with Node.js 24 by default starting June 2nd, 2026."
**Resolution:** Bump action versions whenever a new major releases that supports Node 24. Non-blocking — workflow keeps running until the cutoff.
**Avoid next time:** Periodically audit pinned action versions in `.github/workflows/`.

### G-010 — Daily-brief workflow step is removed pending B-025; cron still runs without it
**Hit:** 2026-05-09 (R-019 refactor design)
**Symptom:** The Postgres-backed workflow finishes successfully but produces no markdown report — the `build_daily_report.py` step was removed because it reads JSON files that no longer exist.
**Resolution:** Documented in the workflow YAML as an explicit comment. B-025 will rewrite the report against Postgres or retire it outright.
**Avoid next time:** When deleting a downstream consumer of a refactored module, file the follow-up B-item explicitly so the gap doesn't quietly become forgotten.

### G-011 — TimescaleDB renamed compression policies to "Columnstore Policy" in tooling output
**Hit:** 2026-05-10 (B-019 hygiene migrations)
**Symptom:** Verifying the new compression policies via `SELECT * FROM timescaledb_information.jobs WHERE application_name LIKE 'Compression%'` returns empty — but the policies *are* registered. They show up as `application_name = 'Columnstore Policy [<job_id>]'`, `proc_schema = '_timescaledb_functions'`, `proc_name = 'policy_compression'`.
**Resolution:** Filter by `proc_name = 'policy_compression'` (the SQL function name didn't change) when querying for diagnostic purposes. The user-facing API is still `add_compression_policy(...)`, `compress_segmentby`, `compress_orderby` — only the surface naming in `timescaledb_information.jobs` shifted.
**Avoid next time:** Don't filter by `application_name` when querying TimescaleDB job state; use `proc_schema + proc_name` instead. The `application_name` column is meant to be human-readable and the maintainers reserve the right to rename it.

### G-012 — `defillama.chain_tvl` already had 106 chunks before compression policy landed
**Hit:** 2026-05-10 (B-019 hygiene migrations applied)
**Symptom:** Expected protocol_tvl/stablecoins/prices to have 0–1 chunks each (greenfield); chain_tvl had 106. Not actually a problem — those chunks accumulated organically because the daily collector pulls full history from `/v2/historicalChainTvl/{chain}` on every run. The compression policy will auto-compress chunks > 30 days old next time the background worker runs (every 12h).
**Lesson:** The daily collector's "always full history" behaviour for chain TVL means there's already 5+ years of focus-chain TVL in the lake without any explicit backfill. Worth knowing: when B-019 runs, it'll add price/stablecoin/protocol history but skip chain TVL as a no-op (already covered).

### G-013 — `chainTvls` from `/protocol/{slug}` includes synthetic sub-buckets (`Ethereum-borrowed`, etc.)
**Hit:** 2026-05-10 (B-019 normalizer design)
**Symptom:** `normalize_protocol_history` would otherwise emit rows under chain names like `Ethereum-borrowed`, `Ethereum-staking` — DeFiLlama mixes these accounting sub-buckets into the main `chainTvls` dict alongside real chains. They'd pollute `defillama.protocol_tvl(chain)` with non-chain values.
**Resolution:** Filter out any `chain_name` containing `-`. We want plain TVL only; the borrowed/staking views can be re-derived from the lending/staking-specific endpoints if we ever want them as their own facts.
**Avoid next time:** When parsing DeFiLlama nested dicts whose keys *look like* dimension values, audit a real response for synthetic keys before trusting the structure. Same pattern likely lurks in other DeFiLlama surfaces.

### G-014 — Pre-existing legacy `reports/defillama_daily.py` exceeded ruff line-length once we widened the lint surface
**Hit:** 2026-05-10 (B-019 lint pass)
**Symptom:** `ruff check src/ tests/` (broad surface) flagged 7 lines too long in `src/genkei/reports/defillama_daily.py`. The file came over from `scripts/build_daily_report.py` via `git mv` in R-019 and was never re-formatted because we'd been running ruff on touched files only.
**Resolution:** Refactored the long lines (split format strings, hoisted templates). The module is awaiting B-025's decision on retire-vs-rewrite, but it's still alive in the package and lint should apply uniformly.
**Avoid next time:** Run `ruff check src/ tests/` (not just changed files) periodically — at minimum at the start of each branch — to catch latent issues before they compound.

### G-015 — FRED API key in URL: must be redacted before landing in `meta.raw_blobs`
**Hit:** 2026-05-10 (B-028 collector design)
**Symptom:** FRED authenticates via `?api_key=<KEY>` query param. The literal URL going into `meta.raw_blobs.url` would expose the key to anyone with read access to the audit table — and the audit table is supposed to be safe to share for replay/debug.
**Resolution:** `_redact_key(url, api_key)` replaces the key with `***` before the URL is INSERTed. Tests assert the literal key never appears in any stored URL.
**Avoid next time:** Any future ingester that puts auth in the URL needs the same treatment. Header-based auth (Bearer tokens, etc.) is preferred when available because the URL stays clean by default.

### G-016 — FRED `last_updated` uses non-ISO short timezone offsets like `-05`
**Hit:** 2026-05-10 (B-028 normalizer)
**Symptom:** FRED returns timestamps like `"2026-05-09 15:18:01-05"` — the offset has no minutes, so `datetime.fromisoformat()` rejects it.
**Resolution:** `parse_fred_datetime` normalises by appending `:00` when it detects a 3-character offset, then parses. Falls back to date-only parsing if the string is just `YYYY-MM-DD`.
**Avoid next time:** When parsing third-party timestamps, sniff the actual format from a real response — don't assume strict ISO 8601. Probably safer to use `dateutil.parser.parse` for anything outside our own data.

### G-017 — FRED `value` field is `"."` for missing observations, not `null`
**Hit:** 2026-05-10 (B-028 normalizer)
**Symptom:** FRED's JSON serializes missing observations as a string `"."` rather than JSON `null`. A naive `float()` on the value would throw; a coercion that defaults to 0 would silently corrupt the data.
**Resolution:** `parse_fred_value` returns `None` for `"."`, empty string, or any non-numeric input. Missing observations land as `value IS NULL` in `fred.observations` — the (series_id, ts, realtime_start) row still exists so consumers know FRED *had* a row, just no value.
**Avoid next time:** Always check the source's missing-value sentinel before the first ingest. `.`, `"--"`, `""`, `-9999`, `NaN`, `null` — every API picks a different one.

### G-018 — FRED's payload shape uses `"seriess"` (sic) as the array key for series metadata
**Hit:** 2026-05-10 (B-028 normalizer)
**Symptom:** The `/series` endpoint wraps its single result in `{"seriess": [...]}` — note the double-s. Easy to miss when reading the API docs and easier to typo when writing the parser.
**Resolution:** `normalize_series` reads `payload["seriess"]` (the typo is canonical and stable across the FRED API).
**Avoid next time:** When the FRED docs say a key looks weird, trust them — don't "correct" it. Same goes for any similar quirks in other sources.

### G-019 — FRED's JSON file type caps responses at 2000 vintage dates
**Hit:** 2026-05-10 (B-028 first live smoke test against FRED)
**Symptom:** The first scheduled run failed for 6 observation endpoints: every daily-frequency series with decades of history (T10Y2Y, DGS2, DGS10, DGS30, DFF, VIXCLS) returned `400 Bad Request` with the message `"There are 3033 vintage dates in the specified real-time period: 1776-07-04 to 9999-12-31. This exceeds the maximum number of vintage dates allowed for this file type (2000)."` The `build_observations_url` default passes `realtime_start=1776-07-04` and `realtime_end=9999-12-31` to grab every vintage; FRED's JSON serializer can't fit that many in one response for those long daily series.
**Current state:** G-031 replaced both the all-history realtime window and implicit-default request with explicit `/series/vintagedates` pagination plus bounded `vintage_dates` observation chunks.
**Avoid next time:** Smoke-test against the real upstream API on the *first* live run, not just mocked-HTTP integration tests. Mocks can't surface upstream-side limits like vintage-count caps. Same lesson applies to any future ingester whose API has per-response limits we won't hit until the live data hits them.

### G-020 — FRED retired `GOLDAMGBD228NLBM` (London PM gold fix); no clean spot-gold replacement on FRED
**Hit:** 2026-05-10 (B-028 first live smoke test)
**Symptom:** The watchlist's gold series returned `400 Bad Request` with `"The series does not exist."` on both `/series` and `/series/observations`. The London Bullion Market Association data feed FRED used to host appears to have been retired.
**Resolution:** Dropped from `config/watchlists.yml` with a comment explaining what we tried. The closest live FRED alternatives (`GVZCLS` gold volatility, `IQ12260` monthly gold export-price index) aren't spot prices. Plan: re-add a real spot-gold series via a commodities feed when one lands in Phase 2 or beyond.
**Avoid next time:** Periodically audit watchlist series IDs for retirements — FRED occasionally sunsets feeds when the source provider changes terms. Worth wiring into B-072 (schema-drift detection) when that lands.

### G-021 — SEC EDGAR's 10 req/sec fair-access cap is per-host (data.sec.gov), not per-API
**Hit:** 2026-05-10 (B-027 design)
**Symptom:** SEC's documented rate limit is 10 req/sec across `data.sec.gov` *as a whole* — submissions, companyfacts, frames, concepts all share the same budget. Two ingesters running on the same runner could split the budget naively and each think they're fine. SEC throttles via 403 + an HTML response (not JSON), which httpx error handling doesn't decode helpfully.
**Resolution:** `genkei.ingest.sec.DEFAULT_RATE_LIMIT = RateLimit.per_second(8)` — stays under the cap with headroom for any future SEC-using ingester sharing the runner. If we ever land a second SEC-touching workload (e.g., the eventual Form 4 or 13F ingesters), they need to share the limiter, not each create their own.
**Avoid next time:** When stacking two ingesters that hit the same upstream host, share the rate-limiter instance instead of defaulting each to its own per_second(N).

### G-022 — SEC EDGAR requires identification in User-Agent (no key, but enforced)
**Hit:** 2026-05-10 (B-027 design)
**Symptom:** SEC.gov returns `403 Forbidden` for requests without a `User-Agent` that includes a real name + contact email. There's no API key — the User-Agent IS the auth/identification. SEC's docs are explicit: "Sample User-Agent: Sample Company Name AdminContact@<sample company domain>.com". Our default `httpx` UA gets blocked.
**Resolution:** `SEC_USER_AGENT` env var → `genkei.ingest.sec.resolve_user_agent()` → passed through to `HttpClient(..., user_agent=...)`. CI reads it from a same-named GH Actions secret. Local dev sets it in `.env`. If unset, the collector logs a warning and falls back to a placeholder string that SEC may reject.
**Avoid next time:** When an "open" API has no key, look for User-Agent or Referer requirements in the docs before assuming defaults will work. SEC, Wikipedia, OpenStreetMap all enforce identification this way.

### G-023 — CoinGecko Demo requests require an API key
**Hit:** 2026-05-10 (B-034 design)
**Symptom:** CoinGecko's Demo API docs mark `x-cg-demo-api-key` as required. Letting the collector run without `COINGECKO_API_KEY` starts an ingest run and then fails every authenticated `/coins/*` call.
**Resolution:** Superseded by D-020. Demo-keyed requests still send `x-cg-demo-api-key` and use `DEMO_RATE_LIMIT = RateLimit.per_minute(25)`, but missing/blank keys now intentionally select public keyless mode with no auth header and `KEYLESS_RATE_LIMIT = RateLimit.per_minute(5)`.
**Avoid next time:** Separate authenticated Demo API requirements from public keyless endpoints before deciding whether missing credentials should fail fast or choose a constrained unauthenticated mode.

### G-025 — CoinGecko Demo historical charts are a rolling 365-day window
**Hit:** 2026-05-11 (B-034 follow-up)
**Symptom:** The initial collector requested `/coins/{id}/market_chart?days=max&interval=daily` and documented it as full history. CoinGecko's Demo/Public docs limit historical chart access to the past 365 days, including `/market_chart/range`, so `days=max` cannot provide a complete long-horizon backfill on the Demo plan.
**Resolution:** Daily Public/Demo collection requests `days=365&interval=daily` and docs describe it as a rolling snapshot. Historical backfill is explicit: `python -m genkei.ingest.coingecko --backfill --since YYYY-MM-DD` requires `COINGECKO_API_TIER=pro`, switches to `pro-api.coingecko.com` with `x-cg-pro-api-key`, and fetches `/market_chart/range` in 365-day chunks. Complete history still requires a paid Pro API configuration or another source with the required historical range.
**Avoid next time:** Verify historical range limits separately from endpoint shape. A `days=max` parameter does not imply full source history on every plan.

### G-024 — CoinGecko `market_chart` returns three parallel arrays that don't always align by index
**Hit:** 2026-05-10 (B-034 normalizer design)
**Symptom:** `/coins/{id}/market_chart` returns `prices`, `market_caps`, and `total_volumes` as three separate lists of `[unix_ms, value]` pairs. They look parallel but the timestamps don't always match across all three (especially at the start/end of a coin's history, or for newer coins where one series starts before the others). Naively zipping by index produces rows with mismatched timestamps.
**Resolution:** `normalize_market_chart` builds three `{ts: value}` dicts, intersects the timestamp sets, and only emits rows for timestamps present in all three. Drops cleanly when arrays have head/tail offsets. Unit test asserts a missing timestamp in any series drops that row.
**Avoid next time:** Any API that returns "parallel arrays" — verify the alignment assumption by checking sample data, especially at the boundaries. Zip by key, not by index.

### G-027 — Typer evaluates parameter annotations at runtime; `X | None` syntax fails on Python 3.9
**Hit:** 2026-05-10 (B-038 / B-039)
**Symptom:** The local venv is Python 3.9 (G-002), but `pyproject.toml` requires `>=3.10`. With `from __future__ import annotations` the modern `str | None` syntax works as a stringified annotation almost everywhere — except Typer, which calls `get_type_hints()` to read parameter types and evaluates the strings at runtime. On Python 3.9 the evaluation hits `TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'`.
**Resolution:** CLI files use `Optional[T]` instead of `T | None`. Removing `from __future__ import annotations` from those files lets Typer parse `Annotated[str, typer.Option(...)]` as a real Option (otherwise Typer treats required `Annotated[str, ...]` as a positional argument and silently ignores the Option metadata). `pyproject.toml` adds `[tool.ruff.lint.per-file-ignores]` for `src/genkei/cli/*.py = ["UP045", "UP007"]` so ruff doesn't auto-rewrite our `Optional` form back to `X | None`.
**Avoid next time:** Bump the local venv to Python 3.10+ (matches `pyproject.toml`). Until then, any new Typer/Pydantic/argparse-style runtime-evaluated annotations need the `Optional` form.

### G-026 — `unittest.TestCase.enterContext` requires Python 3.11+
**Hit:** 2026-05-10 (CLI tests on Python 3.9 venv)
**Symptom:** Test setup using `tmp = Path(self.enterContext(TemporaryDirectory()))` fails with `AttributeError: 'TestCase' object has no attribute 'enterContext'` on Python 3.9.
**Resolution:** Use the older pattern: `ctx = TemporaryDirectory(); self.addCleanup(ctx.cleanup); tmp = Path(ctx.name)`. CI runs 3.12 where `enterContext` exists; the older pattern works on both.
**Avoid next time:** Same root cause as G-027 — local venv is 3.9. Either upgrade the venv or stick to stdlib APIs that predate 3.11.

### G-028 — psycopg3 can't auto-adapt a bare dict to a JSONB column
**Hit:** 2026-05-15 (first live SEC normalize, run id 33)
**Symptom:** `bulk_upsert` for `sec.companies` fails with `psycopg.errors.ProgrammingError: cannot adapt type 'dict' using placeholder '%s' (format: AUTO)`. Three consecutive nightly normalize runs (28, 33, 38) failed identically; `sec.companies` stayed empty.
**Resolution:** Wrap dict/list values destined for JSONB columns with `psycopg.types.json.Jsonb(...)` before they reach `executemany`. Applied to `_maybe_jsonable` in `src/genkei/normalize/sec.py` — handles the `former_names` field. The bare list-of-strings `exchanges` column is `text[]` (not JSONB) and adapts fine without wrapping.
**Avoid next time:** Any normalizer column typed JSONB in the migration needs `Jsonb()` wrapping. Grep new normalizers for `_maybe_jsonable` / raw dict assignment when introducing a JSONB column. Live smoke tests catch this; offline unit tests don't (no psycopg adapter involved).

### G-029 — FRED full-vintage realtime window regressed via a "restore" commit
**Hit:** 2026-05-12 onward (4 consecutive daily collect failures); resolved 2026-05-16
**Symptom:** Same 400 Bad Request shape as G-019 on the 6 daily series (DFF, DGS2, DGS10, DGS30, T10Y2Y, VIXCLS). All ran 6+ daily failures with `FRED fetch failed for 6 endpoint(s)`. Investigation traced to a "Restore FRED realtime observation window" commit that re-added `realtime_start=1776-07-04` / `realtime_end=9999-12-31` to `build_observations_url`, undoing the original G-019 fix.
**Resolution:** Superseded by G-031. Simply omitting realtime params avoids the 2000-vintage cap but changes the request to FRED's current-vintage default, which is not compatible with D-013.
**Avoid next time:** Tests must assert the actual request semantics, not just the absence of a broken parameter set.

### G-030 — `sec.facts` ON CONFLICT key set must match the PK exactly
**Hit:** 2026-05-16 (revealed after fixing G-028)
**Symptom:** `psycopg.errors.InvalidColumnReference: there is no unique or exclusion constraint matching the ON CONFLICT specification`. Normalizer passed 5 `conflict_keys` (`cik, concept, unit, period_end, accession_number`); the actual PK is 6 cols (`cik, concept, unit, period_start, period_end, accession_number`).
**Resolution:** Include `period_start` in `conflict_keys` in `src/genkei/normalize/sec.py` so the conflict target matches the migration's PK exactly.
**Avoid next time:** Bulk-upsert helpers should validate `conflict_keys` against the live table's unique constraints before issuing the statement. Tracked as backlog work — for now, a comment in the normalizer pins the column set to the PK.

### G-031 — FRED omitted realtime params means current vintage only
**Hit:** 2026-05-16 (review of the G-029 fix)
**Symptom:** Omitting `realtime_start` / `realtime_end` from `/series/observations` does not return proper per-observation revision history. FRED defaults the realtime period to today, so each daily collector run returns the current vintage tagged with that day's `realtime_start`. Because `fred.observations` keys on `(series_id, ts, realtime_start)`, this inflates rows as daily snapshots rather than real revisions.
**Resolution:** Keep D-013's vintage-aware schema and request explicit vintages. The collector now fetches `/series/vintagedates`, chunks the returned dates, and calls `/series/observations` with `output_type=3&vintage_dates=...` for each chunk.
**Avoid next time:** Any FRED URL test must verify one coherent mode: full-window realtime pagination, explicit `vintage_dates`, or latest-only normalization. Absence of `realtime_start` / `realtime_end` alone is not a valid invariant.

### G-032 — Ingest modules were reading the legacy `config/watchlists.yml` for weeks after the simplify refactor
**Hit:** 2026-05-20 (surfaced by B-091's live smoke against homelab)
**Symptom:** Daily CoinGecko collector kept fetching the original 7 crypto-core coins for weeks even though `src/genkei/data/watchlists.yml` had grown 14 `coingecko_id` mappings on protocol entries (added in B-062 and B-087). Same for SEC and FRED daily crons — they had their own stale `DEFAULT_WATCHLIST_PATH = Path("config/watchlists.yml")` constant (CWD-relative) pointing at the pre-simplify-refactor file.
**Resolution:** All three ingesters now `from genkei.common.watchlist import DEFAULT_WATCHLIST_PATH` (resolved via `__file__`-relative path, works in dev + installed). The legacy `config/watchlists.yml` was deleted entirely (it was a strict subset of the canonical file — no removals needed).
**Avoid next time:** When a refactor moves a config file under the package (e.g., simplify branch moving `cli/_watchlist.py` → `common/watchlist.py`), audit every module that defines its own path constant. CWD-relative defaults silently bypass package-bundled assets.

### G-033 — DefiLlama's `/protocols` payload has `gecko_id = NULL` for many slugs, especially smaller protocols
**Hit:** 2026-05-20 (B-087 watchlist setup)
**Symptom:** Tried to populate `coingecko_id` for 7 Sui-native protocols by reading DefiLlama's `/protocols` JSON `.gecko_id` field. NULL across all 7 candidates (NAVI / Suilend / Cetus / Scallop / Bluefin / DeepBook / Aftermath).
**Resolution:** Use CoinGecko's `/api/v3/search?query=<symbol>` endpoint to resolve symbol → coingecko_id. Cross-check by symbol + name match to avoid colliding with unrelated coins (see G-035).
**Avoid next time:** Treat DefiLlama's `gecko_id` field as a best-effort hint, not authoritative. CoinGecko's `/search` is the source of truth.

### G-034 — DefiLlama splits multi-product protocols into per-product slugs
**Hit:** 2026-05-20 (B-087: aspirational slug names didn't exist)
**Symptom:** B-087's backlog text proposed candidate slugs `cetus`, `aftermath-finance`, `navi-protocol`, `bluefin`. None existed verbatim on DefiLlama. The real slugs were `cetus-clmm` / `cetus-dlmm` / `cetus-aggregator` (multiple Cetus products); `aftermath-amm` / `aftermath-afsui` / `aftermath-aflp`; `navi-lending`; `bluefin-spot` / `bluefin-pro` / `bluefin-legacy`.
**Resolution:** Always verify candidate slugs live against `defillama.protocols` before adding to the watchlist. Pick the *primary product slug* per protocol (e.g., `cetus-clmm` for Cetus, since CLMM is the dominant DEX surface); add additional product slugs separately when their separate TVL/fees stream is itself a signal (e.g., `bluefin-spot` + `bluefin-pro` for spot-vs-derivatives split).
**Avoid next time:** Before B-087-shaped work (adding any protocol slug set), run `SELECT slug, name, chains, category FROM defillama.protocols WHERE 'X' = ANY(chains) ORDER BY slug` against the live lake and pick from the actual catalog.

### G-035 — CoinGecko `deepbook` is an unrelated coin (symbol ADB); the Sui DEEP token's id is `deep`
**Hit:** 2026-05-21 (B-091 smoke, post-B-087)
**Symptom:** B-087 watchlist had `coingecko_id: deepbook` for the DeepBook V3 entry — intuitive but wrong. CoinGecko's `deepbook` is a different project with symbol "ADB", name "DeepBook". The Sui DEEP token's coingecko id is just `deep`.
**Resolution:** Fixed in B-091 with an inline NOTE in the rationale field warning the next reviewer. The watchlist entry now has `coingecko_id: deep` + a comment explaining the gotcha.
**Avoid next time:** When CoinGecko `/search` returns multiple candidates with similar names, cross-check by **symbol + name + chain** before picking. A protocol with multiple unrelated coins sharing similar names is more common than you'd expect.

### G-036 — `Decimal (a * b) / c` ≠ `a * (b / c)` under default precision
**Hit:** 2026-05-18 (B-062 unit tests, surfaced during development)
**Symptom:** `compute_relative_strength`'s annualization math expected `(trailing * 365) / 30 = 36500` for `trailing=3000`, but Python's Decimal returned `36500.00000000000000000001` for `trailing * (365 / 30)` because the intermediate `365/30` was a 28-digit irrational.
**Resolution:** Always multiply before dividing in monetary math: `(a * b) / c`, not `a * (b / c)`. The tests now pin the exact expected Decimal value to catch any future operator-order regression.
**Avoid next time:** When porting an SQL formula to Python, parenthesize explicitly. SQL's left-associativity matches Python's, but the moment of forgetting matters.

### G-037 — Backfill's `--since YYYY-MM-DD` is required by argparse but only affects the `prices` endpoint
**Hit:** 2026-05-21 (B-085 stablecoin backfill)
**Symptom:** Ran `python -m genkei.ingest.defillama --backfill --endpoint stablecoins` without `--since` — argparse rejected. Re-ran with `--since 2020-01-01`; the stablecoin path ignored the date entirely (it iterates every known asset_id and pulls the full per-stablecoin history per call). `--since` only constrains the prices backfill (which walks daily timestamps).
**Resolution:** Pass any valid date for `--since` when backfilling protocols / stablecoins. The codified runbook in `src/genkei/ingest/defillama.py`'s docstring captures the gotcha.
**Avoid next time:** Either make `--since` optional when `--endpoint` is one that doesn't need it, or document loudly that `--since` is a required flag with endpoint-specific semantics. Today the second is in place; the first is worth doing if any future endpoint adds a third semantic.

### G-038 — FRED `output_type=3` vintage-chunk approach (G-029 successor) silently starved the lake for 34 days
**Hit:** 2026-05-16 → 2026-06-19 (surfaced by the first weekly signal digest, D-024).
**Symptom:** FRED collect runs sat in `meta.ingest_runs` as `running` forever (never `success`/`failed`); `fred.normalize` was 829h stale, FRED data frozen at the 2026-05-16 vintage. Two compounding bugs from the same 2026-05-16 commits (`9065a4c` + `13a7493`) that switched observations to `output_type=3` + explicit `vintage_dates` chunks: (1) the collector re-fetched **every** historical vintage each run — ~5,000 per daily series (DGS10/DFF) — so collect blew past the 15-min workflow timeout and was killed mid-run (hence the stuck `running` rows); (2) even when it completed, `output_type=3` returns a **wide vintage-column** shape (`{date, DGS10_20260518: '4.59'}`) that `normalize_observations` (expecting `{date, value, realtime_start, realtime_end}`) couldn't parse, so 0 rows landed. The hang hid bug (2) for a month — normalize never ran to reveal it.
**Resolution:** Reverted `output_type=3` for daily incremental collection. The collector is now **incremental** over a *bounded realtime window*: `realtime_start = max(realtime_start) in fred.observations` for the series → `realtime_end = 9999-12-31`. That spans only the handful of new vintages since the last collect (far under FRED's 2000-vintage full-window cap that drove G-029) and returns the standard long format the normalizer already understands. Because FRED labels values already current at the window boundary with the request's `realtime_start`, the collector drops clipped boundary rows unless they shorten `realtime_end` for a currently open stored interval or carry a same-day value update; closure rows are rewritten to the stored open `realtime_start` so the normalizer updates the correct `(series_id, ts, realtime_start)` key instead of inserting a pseudo-vintage at the cursor. Fresh-series/bootstrap and `--backfill` avoid the capped full-window request by fetching `/series/vintagedates`, chunking those dates under FRED's JSON vintage cap, calling `output_type=3`, and converting the wide vintage columns into long rows with derived `realtime_end`. First incremental run recovered the 34-day gap in ~40s.
**Avoid next time:** Raw `output_type=3` is structurally wrong for the `(series_id, ts, realtime_start, realtime_end)` schema unless the collector converts the wide vintage columns and derives `realtime_end` before normalization. Never re-fetch immutable historical vintages on a daily cadence; key incremental ingest off what's already stored. And: a collector that's killed mid-run leaves a `running` row, not a `failed` one — the heartbeat/staleness monitors (B-119) catch the staleness, but the *weekly digest's health footer* (D-024) is what actually surfaced this, validating that artifact. This supersedes G-029.

### G-039 — GDELT moved `data.gdeltproject.org` to a GCS bucket whose cert doesn't cover the custom domain
**Hit:** surfaced 2026-06-20 (every `gdelt-daily.yml` run failing; flagged by the D-024 weekly digest health footer alongside the BEA/EIA missing-key failures).
**Symptom:** `httpx.ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Hostname mismatch, certificate is not valid for 'data.gdeltproject.org'`. GDELT now serves the v2 firehose from a Google Cloud Storage bucket fronted by the `data.gdeltproject.org` CNAME, but the bucket presents the GCS wildcard cert (`CN=*.storage.googleapis.com`, SANs cover `*.storage.googleapis.com` / `storage.googleapis.com` / `commondatastorage.googleapis.com` — **not** `data.gdeltproject.org`). GCS doesn't terminate custom-domain HTTPS without a load balancer, so HTTPS to the vanity host fails TLS verification. Plain `http://data.gdeltproject.org/...` still works (200) but downgrades to cleartext.
**Resolution:** Pointed `GDELT_BASE_URL` at the bucket's real GCS hostname — `https://storage.googleapis.com/data.gdeltproject.org/gdeltv2` — which serves identical bytes under a cert that validates, with no insecure-http downgrade and no `verify=False`. Only the one constant needed changing: `latest_gkg_timestamp` parses just the *filename timestamp* out of `lastupdate.txt` (it ignores the host in those lines — and GDELT's `lastupdate.txt` still lists the old `data.gdeltproject.org` URLs, which is fine), and `url_for_timestamp` rebuilds every fetch URL from `GDELT_BASE_URL`. Verified live: 1h window fetched 5 files over valid TLS, wrote 12 watchlist rows.
**Avoid next time:** When a public data host suddenly fails TLS with a *hostname mismatch* (not an expiry), suspect a move to object storage behind a vanity CNAME. Inspect the presented cert's SANs (`openssl s_client -connect host:443 | openssl x509 -ext subjectAltName`) and hit the storage backend's canonical hostname directly rather than disabling verification or downgrading to http.

### G-040 — EIA renamed the `fueltype` facet to `fueltypeid` on the electric-power route
**Hit:** surfaced 2026-06-20, once the `EIA_API_KEY` secret was finally provisioned and the collector got far enough to actually call the API.
**Symptom:** `400 Bad Request` for series `eia_elec_net_gen_us` only (the other EIA series were fine), with the body `{"error":"Invalid facet 'fueltype' provided. The only valid facets are 'location', 'sectorid', and 'fueltypeid'.","code":400}`. EIA renamed the fuel-type facet on `electricity/electric-power-operational-data` from `fueltype` to `fueltypeid`; the watchlist still bound the old key. Both the request builder and the normalizer's `_row_matches_facets` read the facet name straight from the watchlist `facets:` dict, and the response column renamed in lockstep — so the one rename in `watchlists.yml` fixes both sides.
**Resolution:** Changed the `ELEC_NET_GEN_US` facet key `fueltype: ALL` → `fueltypeid: ALL`. Verified live: collect + normalize both green (303 monthly rows available). Note EIA's 400 body names the offending param precisely — but httpx's `HTTPStatusError` doesn't include the response body, so reproduce the failing URL with `curl -g` (the `[...]` facet params are curl URL-globs) to read it.
**Avoid next time:** EIA facet names drift per-route and aren't globally stable; when one series 400s while others pass, it's almost always a route-specific param rename, not the key. Pull the JSON error body before guessing.

### G-041 — BEA NIPA Table T20804 (PCE price index) is monthly-only; a `Q` request returns a generic "Error retrieving NIPA data"
**Hit:** surfaced 2026-06-20, same trigger as G-040 (BEA key provisioned, collector reached the API).
**Symptom:** `BEA returned error for bea_t20804_q: Error retrieving NIPA data.` — only the T20804 target failed; the other 6 BEA tables fetched fine (proving the key was valid). NIPA Table 2.8.4 ("Price Indexes for PCE by Major Type of Product") publishes **monthly only**; the watchlist tagged both its lines `frequency: Q`, and BEA answers an unsupported table/frequency combo with its catch-all "Error retrieving NIPA data" rather than a specific message. BEA returns a 200-OK error *envelope* (not an HTTP error), which `_is_bea_error_envelope` already catches.
**Resolution:** Changed both T20804 watchlist entries `frequency: Q` → `M`. Monthly is also the *correct* cadence for the PCE price index (the Fed's monthly inflation read). Verified live: collect + normalize both green. Confirm a NIPA table's supported frequencies before adding it — `GetData?TableName=Tnnnnn&Frequency=M` vs `Q` against the live API is the fastest check.
**Avoid next time:** BEA's "Error retrieving NIPA data" is generic — it covers bad table id, unsupported frequency, and bad line number alike. When it fires for one target only, suspect a table/frequency mismatch first (the `x.8.x` price-index tables are monthly).

### G-042 — API keys in query strings leak via httpx's INFO request logging
**Hit:** noticed 2026-06-20 while debugging BEA — the terminal showed `GET https://apps.bea.gov/api/data/?UserID=0C0BB4BD-…` in plaintext.
**Symptom:** httpx logs every request at INFO as `HTTP Request: GET <full-url>`. Sources that authenticate via query string (BEA `UserID`, EIA/FRED `api_key`, Etherscan `apikey`, …) therefore emit the live secret to stdout — and into GitHub Actions logs, which are readable by anyone with repo read. The codebase carefully redacts keys in `meta.raw_blobs` (`_redact_key`) but that redaction never touched the httpx logger. Three ingesters (gdelt, etherscan, onchain_staking) each carried their own `logging.getLogger("httpx").setLevel(WARNING)` line; the other ten didn't, so BEA/EIA/FRED/etc. all leaked.
**Resolution:** Hoisted the suppression to `genkei.common.http` module load (every ingester imports `HttpClient` from there), and removed the three now-redundant per-module copies. WARNING still surfaces 4xx/5xx/network errors; only the routine request-completion line is silenced. Verified: BEA run emits 0 key-bearing log lines.
**Avoid next time:** Never rely on per-module discipline for a cross-cutting secret-hygiene concern — pin it once in the shared client. If a new source needs request-level logging for debugging, log a `_redact_key`'d URL explicitly rather than re-raising the httpx logger to INFO.

### G-043 — B-072 drift checker flagged the backfill-only stablecoin endpoint as stale, and required a key real payloads don't carry
**Hit:** surfaced 2026-06-20 by the D-024 weekly digest health footer (`defillama / stablecoin_<id> / NO_RECENT_SAMPLES`).
**Symptom:** Two compounding spec bugs, both isolated to the `stablecoin_<id>` `EndpointSchema`. (1) *False staleness.* The drift checker's freshness window (`check_recent_blobs`, 72h) treated `/stablecoin/{id}` as a daily endpoint, but it's **backfill-only** — the daily collector keeps the forward per-chain supply series current through the aggregate `stablecoins` blob (verified: `defillama.stablecoins` fresh to yesterday, 947k rows / 306 assets), while the per-id history blobs were a one-time backfill (last fetched 2026-05-21). So they legitimately aged past 72h and false-alarmed forever — unlike `/protocol/{slug}`, which B-081 genuinely *does* pull daily. (2) *Wrong required key.* Once the freshness exemption let the latest (aged) blob get shape-checked, it revealed the spec required `chainCirculating`, but live `/stablecoin/{id}` payloads carry the per-chain series under `chainBalances`. The normalizer accepts either (and silently yields **zero rows** if neither resolves), but the spec's AND-list `required_keys` couldn't express the either/or, so it demanded the variant real blobs lack. (The old test even encoded the inverted "G-005 lesson" that the endpoint uses `chainCirculating` not `chainBalances` — the reverse of reality.)
**Resolution:** Added two narrow capabilities to `EndpointSchema`: `backfill_only=True` (freshness check samples the latest blob of any age and shape-checks it, but skips `NO_RECENT_SAMPLES`; a never-backfilled endpoint with zero blobs stays silent), and ordered `any_of_keys` with optional selected-value guards (the first present interchangeable key in normalizer precedence must resolve to the normalizer-consumable shape/content, emitting `MISSING_ANY_OF_KEYS` when it does not). The stablecoin spec now uses `required_keys=("symbol","name","pegType")` + `any_of_keys=("chainCirculating","chainBalances")` + `any_of_value_type="object"` + `any_of_value_content="stablecoin_history"` + `backfill_only=True`. `watchlist drift` is clean; the either/or canary still fires if both keys vanish, both values become unparseable, the selected preferred value becomes unparseable while a fallback key is also present, or the selected chain container has no usable history point.
**Avoid next time:** A drift/health spec must encode the endpoint's real *cadence* (daily vs backfill-only) and the normalizer's real *tolerance* (either/or keys), not an idealized shape. When a normalizer is defensive (`get()` with a fallback, returns `[]` on a miss), the silent-zero-rows path is precisely what the canary must guard — don't let the spec be stricter on one side and blind on the other.
