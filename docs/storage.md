# Storage decisions: Postgres + extensions, schema strategy, migrations

**Date:** 2026-05-07 · **Resolves:** B-007, B-008, B-009 · **Verified against homelab:** pending B-006

## Decisions (up front)

- **Extension**: **TimescaleDB** for hypertables, continuous aggregates, compression, retention. Fallback to plain Postgres + manual quarterly partitioning if the homelab can't host it.
- **Schema layout**: per-source schemas (`defillama.*`, `sec.*`, `fred.*`, …), `meta.*` for operational tables, `analytics.*` for cross-source materialized views. snake_case, plural tables.
- **Migration tool**: **Alembic** with hand-written migrations only (no autogen). Migrations in `migrations/versions/`, version tracked in `meta.alembic_version`.

---

## B-007 — Extensions and time-series strategy

### What we're storing

| Shape | Examples | Volume estimate (5y backfill) |
|---|---|---|
| Time-series facts | OHLCV, TVL snapshots, macro series, stablecoin supply, news event tone | 1k+ assets × 1825 days × N sources ≈ 10M+ rows per series class |
| Entity tables | Protocols, filings, companies, watchlist members | ~10k–100k rows |
| Operational | ingest_runs, raw_blobs, alerts, anomalies, signals | low |

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **TimescaleDB** (chosen) | Hypertables auto-partition by time. Continuous aggregates for rolling windows (B-067). Native compression (~10x on price/TVL data). Retention policies out of the box. Mature Python support via `psycopg`. | Requires installing the extension on the homelab. Community-license gating on a few enterprise features (none we need). |
| pg_partman | Lighter than Timescale; declarative partitioning over plain PG. | We re-implement what Timescale gives free (continuous aggregates, compression). |
| Plain PG, manual partitioning | Zero deps. Full control. | Tedious; we hand-write what Timescale automates. Real cost as sources grow. |
| Plain PG, no partitioning | Simplest. | Big tables will need partitioning eventually; retrofitting is harder than starting partitioned. |

### Why TimescaleDB

- Continuous aggregates *are* the multi-day momentum tables in B-067. Reusing the engine is cheaper than building it.
- Compression matters when 5–10 years of intraday data lands. ~10x on numeric time-series is typical.
- Hypertables look like regular tables to most code — minimal API friction for the CLI.
- The homelab is a single-tenant Postgres on a Beelink; installing an extension is a `CREATE EXTENSION timescaledb;` away once the package is on the box.

### Fallback

If the homelab can't run TimescaleDB (older PG version, `shared_preload_libraries` constraints, etc.), the trio downgrades to:
- Plain PG with manual partitioning by quarter on time-series tables.
- Hand-rolled rollup tables for the multi-day windows that would have been continuous aggregates.

That call is made in **B-006** when `/server-info` is loaded and we know the actual installed extensions and PG version.

---

## B-008 — Schema strategy

### Layout

```
defillama.*    -- raw + normalized DeFiLlama data
sec.*          -- SEC EDGAR (filings, XBRL facts)
fred.*         -- FRED macro series
bea.*          -- BEA accounts
treasury.*     -- Treasury Fiscal Data
cftc.*         -- CFTC Commitments of Traders
eia.*          -- EIA energy
gdelt.*        -- GDELT news/events
coingecko.*    -- CoinGecko prices
binance.*      -- Binance public market data

meta.*         -- ingest_runs, raw_blobs, alerts, anomalies, signals,
                  regimes, watchlists, schema_history, alembic_version
analytics.*    -- materialized views joining multiple sources
                  (e.g. analytics.equity_prices_with_filings)
```

### Why per-source schemas

- **Blast radius** is contained: dropping/resetting one source's tables doesn't risk siblings.
- **Permissions** can be scoped (an ingester role gets `INSERT, UPDATE` on its own schema only; the CLI's read role gets `SELECT` everywhere).
- **Search path** can be configured per ingester so its module code stays terse.
- **Cross-source joins** live explicitly in `analytics.*` rather than being implicit in queries — the dependency graph is visible in one place.

### Naming conventions

- Identifiers: `snake_case`, lowercase.
- Tables: **plural** (`prices`, `filings`, `protocols`, `chains`).
- Views and materialized views: same as tables — plural.
- Time columns:
  - `ts` (`TIMESTAMPTZ`) — the *event time* the row describes (e.g. price-as-of).
  - `fetched_at` (`TIMESTAMPTZ`) — when the ingester pulled it.
  - `valid_from` / `valid_to` — for SCD2-style history when an entity changes shape over time.
- Source provenance — every fact table carries:
  - `source_endpoint TEXT NOT NULL`
  - `fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()`
  - `ingest_run_id BIGINT NOT NULL REFERENCES meta.ingest_runs(id)`
- Primary keys:
  - **Composite natural** for time-series facts: `(asset_id, ts)`, `(chain, ts)`, `(series_id, ts)`. Plays well with TimescaleDB hypertables (time column must be in the PK).
  - **BIGSERIAL surrogate** for entity tables (filings, protocols, companies) where natural keys are unstable or ugly.
- Foreign keys: declared everywhere they make semantic sense; never `ON DELETE CASCADE` to a fact table.

### Operational tables (`meta.*`)

Sketched here; schemas finalized in the first migrations.

| Table | Purpose |
|---|---|
| `meta.ingest_runs` | One row per pipeline execution. Status, source, started_at, finished_at, rows_written, error. |
| `meta.raw_blobs` | (Optional) Raw API responses for audit, indexed by ingest_run_id. Decide per-source whether to land here or in object storage. |
| `meta.watchlists` | Resolves B-015 — single source of truth for which assets/tickers/protocols are tracked. |
| `meta.signals` | Cross-source correlations from B-064. |
| `meta.alerts` | Threshold-based events from B-068. |
| `meta.anomalies` | Per-series outliers from B-069. |
| `meta.regimes` | Macro regime labels per date from B-066. |
| `meta.api_usage` | Quota tracking per source for B-076. |
| `meta.alembic_version` | Migration tool's bookkeeping (managed by Alembic). |

---

## B-009 — Migration tool

### Choice: Alembic (hand-written only)

```
migrations/
  alembic.ini
  env.py
  versions/
    20260507_0001_create_meta_schema.py
    20260507_0002_create_ingest_runs.py
    ...
```

- Python-native — matches the rest of the codebase.
- Mature, well-documented, easy to find help.
- `op.execute("...")` lets us drop into raw SQL for TimescaleDB-specific DDL (`CREATE EXTENSION`, `SELECT create_hypertable(...)`, continuous aggregates, retention policies) without fighting the framework.
- Works whether we use SQLAlchemy ORM or raw psycopg downstream — we're not committing to ORM by picking it.

### Conventions

- **No autogenerate.** Every migration is hand-written. Autogen produces noisy diffs against TimescaleDB hypertables and continuous aggregates; debugging the autogen output is more work than writing the SQL.
- File naming: `YYYYMMDD_NNNN_<slug>.py` — date prefix sorts naturally; sequence breaks ties on the same day.
- One migration = one logical change. Don't bundle "create defillama schema" and "create sec schema" into one file — they get reverted independently.
- Every migration must implement `downgrade()`. If a downgrade is genuinely impossible (e.g. data conversion that loses information), the migration raises `NotImplementedError` with a comment explaining why.
- TimescaleDB-specific objects (hypertables, continuous aggregates) live in their own migration files separate from the table creation, so they can be removed without dropping the table.

### Alternatives considered

| Tool | Why not |
|---|---|
| sqitch | DB-agnostic, plain SQL — nice, but adds a non-Python tool to the stack and we don't need polyglot. |
| dbmate | Standalone Go binary, plain SQL. Same comment as sqitch. |
| Flyway | JVM dep. Hard pass. |
| Plain SQL files + custom runner | Tempting (zero deps), but we'd reinvent migration tracking, transactional safety, and downgrade plumbing. Not worth it. |

---

## Implications

- **B-006 unblocks Phase 0**: we still need to confirm TimescaleDB can run on the homelab. Until then, treat the extension choice as recommended-but-tentative.
- **B-010** (shared Postgres helpers) will assume Alembic-managed schema; the helper module knows which schemas to put on the search path.
- **B-012** (`pyproject.toml`) declares `alembic`, `psycopg[binary]`, and (TBD) `sqlalchemy` as deps. Keep `sqlalchemy` optional behind an extra (`pip install -e .[orm]`) until we actually need ORM models.
- **B-016+** (DefiLlama Postgres schema) becomes the first concrete migration set under this convention.
- **CI / Routines / homelab**: every environment runs `alembic upgrade head` before anything else. The homelab Postgres is the system of record; ephemeral CI databases get the same migrations applied at test setup.

## Cross-references

- `docs/repo-layout.md` — `src/genkei/common/` is where Postgres helpers + Alembic env wiring will live.
- `docs/backlog.md` — B-007/B-008/B-009 (resolved by this doc), B-010/B-012/B-016 (depend on this doc), B-006 (final extension verification).
