# Surface stale-snapshot warnings in CLI output (B-023)

## Context
**Backlog ref:** B-023 (Phase 1 — Refactor DeFiLlama onto Postgres).

If the latest data a query reads is hours or days stale, the consumer (agent or human) should *see* that, not silently trust an old number. B-119 closed the **ops** side of staleness (CI alerts/issues when ingest stops). This item closes the **read** side: a query that returns stale data says so inline.

The freshness signal lives in `meta.ingest_runs` (each run stamps its source + completion time) and in the per-source primary tables already enumerated in `src/genkei/cli/watchlist.py` (`PRIMARY_TABLES` / `EXPECTED_ENDPOINTS`, used by `watchlist health`). Reuse that staleness logic — do NOT invent a second, divergent definition of "stale." The single source of truth for per-source freshness should stay in one place; if `watchlist health` already computes it, hoist the helper so both call it.

## Acceptance criteria
- [ ] A configurable threshold (`--max-snapshot-age-hours`, sensible default e.g. 24) is honored by the data-reading subcommands where staleness is meaningful (at minimum the price/tvl/macro read paths).
- [ ] When the freshest relevant row is older than the threshold, the human output shows a visible banner/warning and the `--json` output carries a structured `stale: true` + `age_hours` field (so the agent can branch on it).
- [ ] The staleness computation reuses/extends the existing `watchlist health` freshness helper rather than duplicating it — refactor to a shared helper if needed (CLAUDE.md clean-code rule).
- [ ] `meta.ingest_runs` staleness is queryable behind the helper (no raw SQL duplicated at call sites).
- [ ] Unit tests pin: under-threshold = no warning, over-threshold = warning + JSON flag, the default threshold value, and the threshold override.
- [ ] `.venv/bin/python -m unittest discover -s tests` passes.

## Notes
- Decide and document the default `--max-snapshot-age-hours` per data class if one global default is wrong (crypto updates daily; some macro series are weekly/monthly — a 24h default would false-positive on FRED weeklies). A per-source expected-cadence map likely already exists in `watchlist.py`; lean on it.
- This is read-path only. Don't touch ingest or alerting (that's B-119, already shipped).
- Keep the warning on stderr / a clearly-marked banner so it never corrupts captured `--json` stdout.
