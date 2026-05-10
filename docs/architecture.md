# Architecture

**Living doc.** Two halves:

- **Snapshot** (top half) — what the system *is* today. Refresh in lockstep with shipped work.
- **Decision log + gotchas** (bottom half) — *append-only* record of consequential choices and surprises. Add entries as you make/hit them, not at PR time.

The point of the bottom half: when context gets cleared, the next session (Claude or human) can rebuild *why* we did what we did — not just *what* we did. Commit messages capture *what changed*; `docs/resolved.md` captures *what shipped*; this doc captures *what we learned*.

**Updating discipline:** any commit that makes a non-obvious choice (a tradeoff with a real alternative) or surfaces a non-obvious surprise (a thing future-you wouldn't predict) appends an entry below in the same commit. If the entry is missing, the commit is incomplete.

**Last updated:** 2026-05-10 (Phase 2 underway; B-028 FRED ingester landed on `fred-macro`)

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
External APIs (DeFiLlama today; SEC/FRED/etc. coming)
       │
       ▼  ┌─────────────────────────────────────────────────────────────┐
          │  Collector  src/genkei/ingest/<source>.py                    │
          │   - HttpClient (rate limit, retry, backoff)                  │
          │   - db.ingest_run() context (one row in meta.ingest_runs)    │
          │   - one INSERT per endpoint into meta.raw_blobs              │
          └─────────────────────────────────────────────────────────────┘
                                │
                                ▼
                     ┌─────────────────────┐
                     │  meta.raw_blobs     │  audit + replay
                     │  (JSONB payloads)   │  — system of record for raw
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
       │  defillama.* / sec.* / fred.* / ...                          │
       │   per-source schemas, hypertables on time-series facts       │
       │   provenance trio on every fact row                          │
       │       (source_endpoint, fetched_at, ingest_run_id FK)        │
       └─────────────────────────────────────────────────────────────┘
                                │
                ┌───────────────┼─────────────────┐
                ▼               ▼                 ▼
         (future) CLI    (future) Reports   (future) Agent
       genkei tvl ...   markdown briefs    on-demand researcher
```

The normalizer is **data-lake-shaped**, not report-shaped. It writes the raw shape of every endpoint; derived classifications (momentum, trend, zombie risk, etc.) live in the report layer (currently retired pending B-025).

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
| **Provenance trio** | Every fact row carries `source_endpoint TEXT NOT NULL`, `fetched_at TIMESTAMPTZ NOT NULL`, `ingest_run_id BIGINT NOT NULL REFERENCES meta.ingest_runs(id)`. | R-021 |
| **Migration tool** | Alembic, hand-written migrations only (no autogen). Files at `migrations/versions/YYYYMMDD_<slug>.py`. URL from `GENKEI_DATABASE_URL`. | R-008, `docs/storage.md` § B-009 |

### Code layer (`src/genkei/`)

```
src/genkei/
├── __init__.py
├── common/
│   ├── db.py        — pool, connection(), bulk_upsert, ingest_run() ctx
│   ├── http.py      — HttpClient with rate limit + retry/backoff + jitter
│   └── config.py    — stdlib .env loader
├── ingest/
│   └── defillama.py — collector → meta.raw_blobs
├── normalize/
│   └── defillama.py — meta.raw_blobs → defillama.*
├── reports/
│   └── defillama_daily.py — legacy markdown brief (broken pending B-025)
├── cli/             — empty; lands in Phase 3
└── experiments/     — empty; lands in Phase 5
```

| Module | What | Where to learn more |
|---|---|---|
| `genkei.common.db` | Lazy connection pool, `connection()` (commit/rollback), `bulk_upsert` (`INSERT ... ON CONFLICT`), `ingest_run()` (records meta.ingest_runs lifecycle). | R-009 |
| `genkei.common.http` | `HttpClient(source_name, rate_limit=RateLimit.per_second(N), retry=RetryPolicy(...))`. Handles 408/425/429/5xx with `Retry-After` support and exponential backoff + jitter. Source-tagged User-Agent. | R-010 |
| `genkei.common.config` | `load_env_file(path)` — stdlib-only `.env` loader, no python-dotenv dep. | R-013 |
| `genkei.ingest.defillama` | DeFiLlama collector + backfill (`--backfill --since YYYY-MM-DD --endpoint X`). Daily mode INSERTs raw blobs per endpoint; backfill mode walks daily timestamps for prices, iterates known slugs/asset_ids for protocols/stablecoins. Resumability via `meta.raw_blobs.url` lookup within a 14-day window. | R-017, R-023 |
| `genkei.normalize.defillama` | Reads raw blobs and `bulk_upsert`s. Daily mode (`normalize`) handles snapshot blobs into all four lake tables; backfill mode (`normalize_backfill`) dispatches by `endpoint_name` prefix into `defillama.prices`, `defillama.protocol_tvl`, `defillama.stablecoins`. Idempotent throughout. | R-018, R-023 |
| `genkei.ingest.fred` | FRED collector. Reads `macro_series:` from `config/watchlists.yml`, hits `/series` + `/series/observations` per series with `realtime_start=1776-07-04&realtime_end=9999-12-31` for full-vintage payloads. Lands two raw blobs per series (`series_<id>`, `observations_<id>`). Redacts the API key from the URL stored in `meta.raw_blobs`. Single-mode (D-014) — no separate `--backfill` flag. | R-027 |
| `genkei.normalize.fred` | Reads FRED raw blobs by source_run_id, dispatches by prefix into `fred.series` (one row per series) and `fred.observations` (one row per `(series_id, ts, realtime_start)`). Idempotent. | R-027 |

### Process layer

| Piece | What | Where to learn more |
|---|---|---|
| **Backlog hygiene** | `docs/backlog.md` (open items, 58 active) + `docs/resolved.md` (completed, 24 entries). Updated via the `update-backlog` skill after meaningful commits. | `docs/backlog.md` |
| **Mission queue** | `missions/pending/` and `missions/done/`. Async / overnight execution loop driven by the `run-missions` skill. | `docs/missions.md`, R-005 |
| **Test fixture** | `tests/_postgres.py` — singleton TimescaleDB testcontainer; `postgres_required` decorator gracefully skips when Docker absent. `truncate_all()` for cleanup between tests that go through real `db` helpers. | R-016 |
| **Test counts** | 125 total — 105 unit + 20 integration (skip locally when Docker absent; CI runs them all). | R-023 |
| **CI workflows** | `.github/workflows/tests.yml` (push to main + PRs) and `.github/workflows/defillama-daily.yml` (cron at 10:30 UTC on the self-hosted runner). | R-019, R-020 |
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
| **Phase 2** — Free-data ingesters with backfill | 🟡 in progress | 1/10 done — B-028 FRED landed (R-027). Next obvious move: B-027 SEC EDGAR (equity backbone) or B-034 CoinGecko (crypto cross-check). |
| **Phase 3** — Custom CLI | ⚪ not started | 11 items (B-037 through B-047). `genkei` is the working name. |
| **Phase 4** — Agent layer | ⚪ not started | 6 items (B-048 through B-053). Harness decision pending. |
| **Phase 5** — Experiments framework | ⚪ not started | 10 items (B-054 through B-063). Notebooks + reproducibility pattern + concrete experiments. |
| **Phase 6** — Inefficiency-detection signals | ⚪ not started | 6 items (B-064 through B-069). Cross-source correlation, scoring rubric, regime classifier integration. |
| **Phase 7** — Operations & hardening | 🟡 in progress | B-077 (self-hosted runner) done; B-070 backups, B-071 alerting, B-072 schema-drift, B-073 secrets, B-074 architecture diagram, B-075 license audit, B-076 quota tracking still open. |

See `docs/backlog.md` for the live list, `docs/resolved.md` for the chronicle.

### Open architectural decisions

Tracked as backlog items so they don't block forward motion:

- **B-025** — Fate of the legacy daily-brief markdown report. The retired `build_daily_report.py` reads JSON files that no longer exist; pending decision to either rewrite against Postgres or retire entirely.
- **B-037** — CLI tool name (working: `genkei`).
- **B-048** — Agent harness (Claude Code via GH Actions vs `/schedule` Routines vs hybrid).

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
| `docs/defillama-daily-review.md` | Acceptance gates for the legacy markdown brief (relevance pending B-025) |
| `docs/backlog.md` | Open items, 58 entries across 8 phases |
| `docs/resolved.md` | Completed milestones, 24 entries with evidence |

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
- **Reports:** commit to `reports/` (when re-enabled); brief is currently retired pending B-025.

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

### D-014 — FRED is single-mode: no `--backfill` flag, daily run pulls full history
**Date:** 2026-05-10 · **In:** R-027
**Decision:** `python -m genkei.ingest.fred` always fetches full-vintage observations for every configured series. There is no separate `--backfill` flag.
**Why:** FRED's `/series/observations` endpoint returns the entire history per call. No date-walker needed; daily and backfill are the same code path. The vintage-aware schema (D-013) means re-running just upserts any new revisions as new rows.
**Alternative:** Mirror DeFiLlama's `--backfill --since` flag for consistency. Rejected — adds a code path with no consumer; the FRED endpoint shape doesn't reward it. Per-source ingester shape can differ from per-source ingester shape; that's fine.

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
