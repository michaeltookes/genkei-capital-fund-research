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
- **Outcome:** `/server-info` skill loaded and the Genkei Capital-relevant pieces captured in `docs/infrastructure.md` (using `<beelink-host>` placeholders so no real IPs land in the repo). Existing container `genkeicapital-postgres` runs `postgres:16-alpine` on port 5440, network `mission_control_net`, mounted at `apps/mission-control/genkei-capital/postgres/` on the homelab. Beelink is behind double NAT — GitHub-hosted runners cannot reach it; a self-hosted runner on the Beelink is the chosen path (B-077 firmed up accordingly). TimescaleDB is **not** installed in the alpine image; B-007 narrowed to either a `timescale/timescaledb:latest-pg16` image swap or the plain-PG `pg_partman` fallback.
- **Evidence:** `docs/infrastructure.md`. Updated B-007 + B-077 acceptance criteria to reflect what's actually needed.
