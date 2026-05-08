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
