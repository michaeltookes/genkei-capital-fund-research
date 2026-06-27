# Periodic ingest-health summary report (B-053)

## Context
**Backlog ref:** B-053 (Phase 4 — Agent layer).

The agent should periodically emit a human-readable health summary across **every** active source — staleness, schema drift, anomalies — so operational issues surface without anyone running `watchlist health` by hand. This is the *narrative* companion to two things that already exist and must not be duplicated:
- **B-119** (CI alerts/issues/Discord + heartbeat) — that's *real-time alerting on failure*. B-053 is a *periodic full-roster digest*, including healthy sources, as a committed artifact.
- The **signal digest's** lake-health footer (`src/genkei/reports/signal_digest.py`) — that already lists stale/missing sources weekly. B-053 should **reuse that footer's data path**, not reimplement freshness; the deliverable here is a dedicated, fuller health report (every source, not just the footer summary) on its own cadence.

Scope decision the agent should make and record in the mission's Completed footer: either (a) extend `signal_digest`'s health builder into a standalone `reports/health/<date>.md` renderer, or (b) a new small module that reuses the same `watchlist health` query layer. Prefer reuse over a third freshness definition (CLAUDE.md clean-code rule).

## Acceptance criteria
- [x] A committable periodic summary lands under `reports/health/ingest-health-<date>.md` covering **every active source** in the `watchlist health` roster — healthy and unhealthy alike — with per-source status + age (29 ingest endpoints, 23 primary tables in the first artifact).
- [x] Anomalies / staleness entries link back to `meta.ingest_runs` — each ingest-run row carries its last-run timestamp for traceability.
- [x] The renderer (`render_health_report`) is a pure function (offline-testable) split from DB access (`build_health_report`), matching the `signal_digest` build/render split.
- [x] Cadence documented in the module docstring (weekly `/schedule` routine, runner-agnostic cron shape given; failed runs alert via the B-119 path since the runner that fires it is itself watched).
- [x] Unit tests cover the renderer offline (healthy roster, mixed stale/missing/fail/empty/drift roster, empty-roster edge) — 12 tests.
- [x] `.venv/bin/python -m unittest discover -s tests` passes.

## Notes
- Don't rebuild alerting. If a source is dark, this report *records* it; B-119 is what *pings* about it.
- Schema-drift detection can be a v1 stub (flag column-count / type changes vs a pinned expectation) if full drift detection is too big — note the cut explicitly in the Completed footer and file a follow-up rather than silently dropping it.
- First rendered artifact should be committed as proof, exactly as B-122 did with `reports/signals/weekly-2026-06-20.md`.

## Completed: 2026-06-27
Shipped `src/genkei/reports/ingest_health.py` (build/render split mirroring `signal_digest`) + `tests/reports/test_ingest_health.py` (12 offline tests) + the first live artifact `reports/health/ingest-health-2026-06-27.md`.

**Scope decision (option b):** a small new renderer that **reuses the `watchlist health` query layer** (`_query_source_health` / `_with_health_status` / `_drift_rows`) rather than a third "stale" definition — the AC's clean-code requirement. It renders the *full roster* (every source, OK included) as a committed record, distinct from B-119 (real-time failure paging) and from the signal-digest footer (not-OK only).

**Schema drift is NOT a stub** — it reuses the existing B-072 `_drift_rows` check (load-bearing-key sampling over recent `meta.raw_blobs`), so the report flags real drift, not a placeholder. No follow-up needed on that front.

**The first artifact immediately earned its keep:** it surfaced a real failure the decision log couldn't — `gdelt collect` is FAILing with `field larger than field limit (131072)` (a Python `csv` field-size-limit bug in the GDELT GKG parser). 28/29 endpoints OK, 23/23 tables live, no drift. **Follow-up worth a backlog item:** raise the `csv.field_size_limit` in the GDELT collector (or stream the oversized field) — out of scope for this report mission, which only *surfaces* it. Suite green, ruff clean.
