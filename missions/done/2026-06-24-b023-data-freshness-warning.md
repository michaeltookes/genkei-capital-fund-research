# Surface stale-snapshot warnings in CLI output (B-023)

## Context
**Backlog ref:** B-023 (Phase 1 — Refactor DeFiLlama onto Postgres).

If the latest data a query reads is hours or days stale, the consumer (agent or human) should *see* that, not silently trust an old number. B-119 closed the **ops** side of staleness (CI alerts/issues when ingest stops). This item closes the **read** side: a query that returns stale data says so inline.

The freshness signal lives in `meta.ingest_runs` (each run stamps its source + completion time) and in the per-source primary tables already enumerated in `src/genkei/cli/watchlist.py` (`PRIMARY_TABLES` / `EXPECTED_ENDPOINTS`, used by `watchlist health`). Reuse that staleness logic — do NOT invent a second, divergent definition of "stale." The single source of truth for per-source freshness should stay in one place; if `watchlist health` already computes it, hoist the helper so both call it.

## Acceptance criteria
- [x] A configurable threshold (`--max-snapshot-age-hours`, default 36h) is honored by the data-reading subcommands where staleness is meaningful (prices/tvl/macro).
- [x] When the freshest relevant row is older than the threshold, the human output shows a visible banner/warning and the structured `{stale, age_hours, max_age_hours}` object is emitted as JSON (on **stderr** — see Completed note) so the agent can branch on it.
- [x] The staleness computation reuses/extends the existing `watchlist health` freshness helper rather than duplicating it — the shared `freshness.age_hours()` now backs both `watchlist health`/`gaps` and the read path.
- [x] `meta.ingest_runs` staleness is queryable behind the helper (`freshness.ingest_run_freshness`, used by macro; no raw SQL at the call site).
- [x] Unit tests pin: under-threshold = no warning, over-threshold = warning + JSON flag, the default threshold value, and the threshold override.
- [x] `.venv/bin/python -m unittest discover -s tests` passes.

## Notes
- Decide and document the default `--max-snapshot-age-hours` per data class if one global default is wrong (crypto updates daily; some macro series are weekly/monthly — a 24h default would false-positive on FRED weeklies). A per-source expected-cadence map likely already exists in `watchlist.py`; lean on it.
- This is read-path only. Don't touch ingest or alerting (that's B-119, already shipped).
- Keep the warning on stderr / a clearly-marked banner so it never corrupts captured `--json` stdout.

## Completed: 2026-06-26
Shipped `src/genkei/common/freshness.py` as the single source of truth for "how old is this data, and is it stale?" — `age_hours` (the one place the `(now - ts)` arithmetic lives, now also backing `watchlist health`/`gaps`), `snapshot_freshness` (freshest-row age, for prices/tvl), and `ingest_run_freshness` (last successful `meta.ingest_runs` row, for macro). `--max-snapshot-age-hours` (default 36h, matching `watchlist health`) added to `prices`, `tvl`, `macro`.

**Key design call — warning rides on stderr, not in the stdout JSON.** AC#2 said "the `--json` output carries a structured `stale` field," but the mission's own Note said keep it on stderr so it "never corrupts captured `--json` stdout" — and the reflection cycle (`prompts/reflect-on-decisions.md`, the B-117/B-118-protected loop) parses `genkei prices --json` as a *bare row list*. Wrapping stdout into an envelope would break that contract. Reconciled by keeping stdout the unchanged bare list and emitting the structured `{"freshness": {stale, age_hours, max_age_hours, source}}` object to **stderr** in JSON mode (a one-line banner in human mode) — the agent can still capture stderr and branch on it. This honors both the Note and the protected contract; flagged here because it's a deliberate reading of an internally-tense AC.

**Macro subtlety:** observation ts is the wrong staleness signal (DGS10 daily, CPIAUCSL monthly, GDPC1 quarterly — a weeks-old monthly observation isn't stale), so macro judges freshness on the last successful `fred`/`normalize` run instead. The freshness probe is wrapped defensively — it must never break the primary query — so a `meta.ingest_runs` lookup failure degrades to "no warning," not a crash. Live-verified: BTC fresh (13.4h) silent at 36h; forced 1h threshold warns on stderr with stdout intact; macro warns on the fred run (13.8h), not the 3-day-old DGS10 observation. +23 tests (`tests/common/test_freshness.py`, `tests/cli/test_freshness_cli.py`); full suite green, ruff clean.
