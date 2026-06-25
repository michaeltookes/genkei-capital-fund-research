# Periodic ingest-health summary report (B-053)

## Context
**Backlog ref:** B-053 (Phase 4 — Agent layer).

The agent should periodically emit a human-readable health summary across **every** active source — staleness, schema drift, anomalies — so operational issues surface without anyone running `watchlist health` by hand. This is the *narrative* companion to two things that already exist and must not be duplicated:
- **B-119** (CI alerts/issues/Discord + heartbeat) — that's *real-time alerting on failure*. B-053 is a *periodic full-roster digest*, including healthy sources, as a committed artifact.
- The **signal digest's** lake-health footer (`src/genkei/reports/signal_digest.py`) — that already lists stale/missing sources weekly. B-053 should **reuse that footer's data path**, not reimplement freshness; the deliverable here is a dedicated, fuller health report (every source, not just the footer summary) on its own cadence.

Scope decision the agent should make and record in the mission's Completed footer: either (a) extend `signal_digest`'s health builder into a standalone `reports/health/<date>.md` renderer, or (b) a new small module that reuses the same `watchlist health` query layer. Prefer reuse over a third freshness definition (CLAUDE.md clean-code rule).

## Acceptance criteria
- [ ] A committable periodic summary lands under `reports/` (e.g. `reports/health/ingest-health-<date>.md`) covering **every active source** in the `watchlist health` roster — healthy and unhealthy alike — with per-source last-success age and status.
- [ ] Anomalies / staleness entries link back to `meta.ingest_runs` (run id or timestamp) so they're traceable.
- [ ] The renderer is a pure function (offline-testable) split from DB access, matching the `signal_digest` build/render split.
- [ ] Cadence is documented (weekly is reasonable; align with the Claude Code harness / `/schedule` decision, runner-agnostic) — wire it to a `/schedule` routine or note the cron shape; failed runs alert via the B-119 path, never silent-drop.
- [ ] Unit tests cover the renderer offline (healthy roster, mixed stale/missing roster, empty-roster edge).
- [ ] `python3 -m unittest discover -s tests` passes.

## Notes
- Don't rebuild alerting. If a source is dark, this report *records* it; B-119 is what *pings* about it.
- Schema-drift detection can be a v1 stub (flag column-count / type changes vs a pinned expectation) if full drift detection is too big — note the cut explicitly in the Completed footer and file a follow-up rather than silently dropping it.
- First rendered artifact should be committed as proof, exactly as B-122 did with `reports/signals/weekly-2026-06-20.md`.
