# Resolved Items

Completed setup and implementation decisions for the Genkei Capital research pipeline.

## Resolved

### R-001 — DeFiLlama-only MVP scaffold merged to main
- **Resolved:** 2026-05-06
- **Outcome:** The DeFiLlama branch was merged into `main`.
- **Evidence:** GitHub `origin/main` includes `.github/workflows/defillama-daily.yml`, collection, normalization, reporting scripts, tests, and docs.

### R-002 — Local validation passed after merge
- **Resolved:** 2026-05-06
- **Outcome:** Local deterministic validation succeeded.
- **Evidence:** `python3 -m unittest discover -s tests` passed with 35 tests; `python3 -m compileall scripts tests` completed successfully.

### R-003 — Live DeFiLlama smoke run succeeded
- **Resolved:** 2026-05-06
- **Outcome:** The public DeFiLlama pipeline generated a raw manifest, normalized daily JSON, and Markdown daily brief.
- **Evidence:** Generated `data/normalized/defillama/daily-2026-05-06.json` and `reports/daily/defillama-daily-2026-05-06.md` locally.

### R-004 — Current generated artifacts remain ignored by git
- **Resolved:** 2026-05-06
- **Outcome:** Generated data and report artifacts are ignored by the current repository rules.
- **Evidence:** `git status --short --ignored data reports` reports generated artifacts as ignored.

### R-005 — Mission queue for autonomous task lists (B-078)
- **Resolved:** 2026-05-08 (PR #6, merged via fund-build-out)
- **Outcome:** End-to-end mission queue scaffold landed: `missions/pending/` and `missions/done/` directories, `missions/_template.md`, `.claude/skills/run-missions/SKILL.md` defining the runner protocol (pre-flight, per-mission loop, blocked-mission handling, stop condition), and `docs/missions.md` documenting format, manual + scheduled invocation, and monitoring. Smoke-tested by running the first queued mission (B-013 repo-layout decision) end-to-end on the same branch.
- **Evidence:** Commits `b6e47f1`, `ee0565c`, `b690085`, `2ed5180`. Mission file at `missions/done/2026-05-07-repo-layout.md`. Tests still 35/35 after the queue ran.

### R-006 — Postgres schema strategy defined (B-008)
- **Resolved:** 2026-05-08 (PR #7, merged via postgres-trio)
- **Outcome:** Per-source schemas (`defillama.*`, `sec.*`, `fred.*`, …), `meta.*` for operational tables, `analytics.*` for cross-source materialized views, `public.alembic_version` outside `meta` for bootstrap safety. Conventions documented: snake_case, plural tables, composite natural PKs for time-series facts, `BIGSERIAL` surrogates for entity tables, source-provenance columns (`source_endpoint`, `fetched_at`, `ingest_run_id`) on every fact table.
- **Evidence:** `docs/storage.md` §B-008. Commits `2129958`, `b6a4181`, `86710e5`.

### R-007 — pyproject.toml + dependency management (B-012)
- **Resolved:** 2026-05-08 (branch postgres-helper)
- **Outcome:** Hatchling build backend, `src/genkei/` package layout, console-script entry point (`genkei = "genkei.cli:main"`), runtime deps (`psycopg[binary]`, `psycopg_pool`, `alembic`), `[dev]` extras (`ruff`, `testcontainers[postgres]`). Local install via `pip install -e .` (no separate lock file yet — will add when complexity justifies).
- **Evidence:** `pyproject.toml`. Commit `526407e`.

### R-008 — Migration tool + first migration (B-009)
- **Resolved:** 2026-05-08 (branch postgres-helper)
- **Outcome:** Alembic configured per `docs/storage.md` conventions: hand-written migrations only (autogen disabled), URL sourced from `GENKEI_DATABASE_URL`, file naming `YYYYMMDD_<slug>.py`. First migration (`20260508_create_meta_schema_and_ingest_runs.py`) creates `meta` schema and `meta.ingest_runs` table with status CHECK constraint and indexes on `(source, started_at DESC)` and `(status, started_at DESC)`. Idempotent via `CREATE SCHEMA IF NOT EXISTS`; downgrade implemented.
- **Evidence:** `alembic.ini`, `migrations/env.py`, `migrations/versions/20260508_create_meta_schema_and_ingest_runs.py`. Commits `aa9cd5c`, `94a4e80`.

### R-009 — Shared Postgres helper module (B-010)
- **Resolved:** 2026-05-08 (branch postgres-helper)
- **Outcome:** `src/genkei/common/db.py` exposes the lazy connection pool (`get_pool` / `reset_pool` / `set_pool`), `connection()` context manager (commits on success, rolls back on exception), `bulk_upsert()` using `INSERT ... ON CONFLICT DO UPDATE` via `executemany` with safe SQL identifier composition, and `ingest_run()` context manager that records `meta.ingest_runs` rows in three short transactions with error truncation at 8000 chars. Sixteen mock-based unit tests cover URL resolution, pool lifecycle, transaction safety, upsert defaults, ingest_run insert/success/fail/error-truncation/null-arg paths.
- **Evidence:** `src/genkei/common/db.py`, `tests/common/test_db.py`. Commits `d304177`, `d8f4fb6`, `5ccf52e`. Real-Postgres integration tests deferred to B-024 (testcontainers); per-source provenance columns wiring deferred to per-source migrations (B-016+).

### R-010 — Shared HTTP client (B-011)
- **Resolved:** 2026-05-08 (branch http-client)
- **Outcome:** `src/genkei/common/http.py` exposes `RateLimit` (with `per_second` / `per_minute` factories), `RetryPolicy` (4 attempts, 1s→30s exponential, 50% jitter, retries on 408/425/429/5xx by default), and `HttpClient` wrapping `httpx.Client`. Sliding-window rate limiter, source-tagged User-Agent (`genkei/<ver> (+<source>)`), `Retry-After` header honored on 429 with fallback to backoff for invalid values, network exceptions retried up to `max_attempts` then re-raised. Test-friendly via `httpx.BaseTransport` injection plus pluggable `sleep` / `clock` callables. Twenty-two unit tests cover rate-limit factories, sliding-window behavior, backoff math (deterministic + jittered), User-Agent defaults/overrides, retry on retryable status, retry on network exceptions, max-attempts ceiling, `Retry-After` parsing, `get_json` happy path + error path, and end-to-end rate-limit enforcement.
- **Evidence:** `src/genkei/common/http.py`, `tests/common/test_http.py`. Commits `dd1ea73`, `b2fca19`, `aa82d1b`, `ecf559c`. Follow-ups noted (not blockers): retry currently fires for any HTTP method (consider GET-only allowlist if non-idempotent verbs land), `RemoteProtocolError` not in retry catch, HTTP-date `Retry-After` falls back to backoff, limiter is single-threaded.

### R-011 — Homelab Postgres connection specs documented (B-006)
- **Resolved:** 2026-05-09 (branch postgres-config)
- **Outcome:** `/server-info` skill loaded and the Genkei Capital-relevant pieces captured in `docs/infrastructure.md` (using `<beelink-host>` placeholders so no real IPs land in the repo). Existing container `genkeicapital-postgres` runs `postgres:16-alpine` on port 5440, network `mission_control_net`, mounted at `apps/mission-control/genkei-capital/postgres/` on the homelab. Beelink is behind double NAT — GitHub-hosted runners cannot reach it; a self-hosted runner on the Beelink is the chosen path (B-077 firmed up accordingly). TimescaleDB is **not** installed in the alpine image; B-007 narrowed to either a `timescale/timescaledb:2.26.4-pg16` image swap or the plain-PG `pg_partman` fallback.
- **Evidence:** `docs/infrastructure.md`. Updated B-007 + B-077 acceptance criteria to reflect what's actually needed.

### R-012 — Initial watchlists defined (B-015)
- **Resolved:** 2026-05-09 (branch postgres-config)
- **Outcome:** `config/watchlists.yml` is the source of truth for assets the data lake tracks. Crypto: 5 primary (BTC/ETH/SOL/LINK/SUI) + 2 secondary (PYTH/RENDER). Equities: 28 entries across mega-cap tech, semis, software, financials, crypto-adjacent, mobility/energy/materials — all `core` sleeve per the long-only Buffett-style stance in CLAUDE.md (DXY moved to macro since it's an index, not a tradable equity). Macro: 20 starter FRED series IDs covering rates, inflation, growth/labor, liquidity, dollar, credit spreads, vol, housing, sentiment, and commodities. Each entry has a one-line rationale. DeFi-protocol coverage stays implicit via chain focus — no explicit `protocols:` section yet.
- **Evidence:** `config/watchlists.yml`. Meaningful commits: `743bee4` (crypto), `5da6f25` (equities + macro). Adds `pyyaml>=6.0` to runtime deps. Backlog tracking moved from `docs/backlog.md` into this resolved entry following the update-backlog process.

### R-013 — `.env.example` + secret-loading pattern (B-014)
- **Resolved:** 2026-05-09 (branch phase-0-cleanup)
- **Outcome:** `.env.example` documents every variable an ingester might need (`GENKEI_DATABASE_URL` required; FRED / BEA / EIA / CoinGecko keys uncommented as their ingesters land). README's new "Setup" section walks through venv install, the three load options (shell `set -a`, direnv, or `genkei.common.load_env_file`), and `gh secret set` for CI — including the heads-up that GH-hosted runners can't reach the homelab Postgres directly (B-077). `src/genkei/common/config.py` is a stdlib-only loader (~30 lines, no python-dotenv dep) with ten unit tests covering missing files, comments/blanks, malformed lines, single + double quotes, value preservation, blank keys, and the "existing env vars take precedence" contract.
- **Evidence:** `.env.example`, `README.md`, `src/genkei/common/config.py`, `tests/common/test_config.py`. Commits `105fd98`, `f9c5c38`, `ce80cf3`. Tests now 107/107.

### R-014 — TimescaleDB activated on the homelab (B-007)
- **Resolved:** 2026-05-09 (branch phase-0-cleanup)
- **Outcome:** Container image swapped from `postgres:16-alpine` to `timescale/timescaledb:2.26.4-pg16` on the Beelink (`docker compose down/up`, external `genkeicapital_postgres_data` volume preserved — no data loss). `ALTER SYSTEM SET shared_preload_libraries = 'timescaledb'` applied + container restarted to load the timescale shared library (the inherited PG data dir didn't have this set since it was created by plain Alpine PG). Alembic migrations applied cleanly: `meta` schema + `meta.ingest_runs` from `7d9d845497ae`, then Timescale activation from `69f3fe427252`. Verified end-to-end — `\dx timescaledb` shows version 2.26.4 installed in `public`. The on-homelab compose backup at `docker-compose.yml.bak-20260509-152146` allows rollback to plain Alpine PG if ever needed.
- **Evidence:** Existing migration `migrations/versions/20260509_install_timescaledb_extension.py`. Beelink: `~/homelab/apps/mission-control/genkei-capital/postgres/docker-compose.yml` updated. `pg_available_extensions` and `\dx` confirm timescaledb 2.26.4 active against database `genkei_capital`.

### R-015 — DeFiLlama Postgres schema designed (B-016)
- **Resolved:** 2026-05-09 (branch postgres-schema, commit `9ef8833`)
- **Outcome:** Two hand-written Alembic migrations land the per-source `defillama` schema and four tables under the conventions in `docs/storage.md`. `defillama.protocols` is a slug-keyed entity dimension with `BIGSERIAL` surrogate, `chains TEXT[]` GIN-indexed, and standard `first_seen_at` / `last_updated_at` lifecycle fields. `defillama.chain_tvl` (PK `(chain, ts)`), `defillama.stablecoins` (PK `(asset_id, chain, ts)`), and `defillama.prices` (PK `(asset_key, ts)`) are time-series facts; the second migration converts each to a TimescaleDB hypertable (30-day chunks for TVL/stablecoins, 7-day chunks for prices to leave room for intraday). Every fact row carries the provenance trio (`source_endpoint`, `fetched_at`, `ingest_run_id` → `meta.ingest_runs`). Stablecoin metadata (symbol/name/peg_type) denormalized inline rather than introducing a premature dimension table; protocol metadata is upserted in place (no SCD2). Hypertable migration intentionally raises `NotImplementedError` on downgrade — per `docs/storage.md`, peel-back the schema migration to drop the underlying tables instead.
- **Evidence:** `migrations/versions/20260509_create_defillama_schema.py`, `migrations/versions/20260509_create_defillama_hypertables.py`. Applied end-to-end against the homelab Postgres: `alembic upgrade head` advanced to revision `6d578bda9706`; `information_schema.tables` and `timescaledb_information.hypertables` confirm all four tables and three hypertables in place. Tests stay 107/107.

### R-016 — Postgres-aware test fixtures (B-024)
- **Resolved:** 2026-05-09 (branch migrate-tests, commit `1dd39d7`)
- **Outcome:** Decision on the testcontainers-vs-rollback question landed on the testcontainers route (already pre-declared in `[dev]` extras under R-007). `tests/_postgres.py` is a singleton harness that spins up `timescale/timescaledb:2.26.4-pg16` once per test process, applies `alembic upgrade head`, and exposes `connection()` (auto-rolling-back psycopg connection for raw-SQL tests), `truncate_all()` (cleanup for tests that go through the real `genkei.common.db` helpers, which commit on their own), and a `postgres_required` decorator that skips integration tests cleanly when Docker isn't available so the offline mock-based suite still runs everywhere. `tests/migrations/test_defillama_schema_integration.py` (7 tests) pins down the live B-016 schema shape — tables present, hypertables registered, slug uniqueness, FK enforcement, `chains TEXT[]` round-trip, NOT NULL on `prices.price_usd`. `tests/common/test_db_integration.py` (5 tests) exercises `connection()` commit/rollback at the SQL boundary, `bulk_upsert` insert-then-update-on-conflict semantics, and the full `ingest_run` lifecycle (running → success / failed) including JSONB metadata persistence. `.github/workflows/tests.yml` runs `unittest discover` on every push to `main` and every PR; GH-hosted Ubuntu runners include Docker, so the integration tests actually exercise Postgres there. Test count goes from 107 → 119 (12 skipped on a Docker-less workstation).
- **Evidence:** `tests/_postgres.py`, `tests/migrations/test_defillama_schema_integration.py`, `tests/common/test_db_integration.py`, `.github/workflows/tests.yml`. Locally: 119 tests, 12 skipped (Docker absent on the author's workstation, so first end-to-end execution against a live container is the CI run on the resulting PR — flagged in the commit message). Backlog acceptance criteria's "35 existing tests" line was already stale (107 at start of work); satisfied via the 119-after count.
