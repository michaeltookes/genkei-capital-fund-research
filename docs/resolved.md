# Resolved Items

Completed setup and implementation decisions for the Genkei Capital research pipeline.

## Resolved

### R-001 — DeFiLlama-only MVP scaffold merged to main
- **Resolved:** 2026-05-06
- **Outcome:** The DeFiLlama branch was merged into `main`.
- **Evidence:** `origin/main` includes `.github/workflows/defillama-daily.yml`, collection, normalization, reporting scripts, tests, and docs.

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
