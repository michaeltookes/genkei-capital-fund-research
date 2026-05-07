# Decide repo layout — `src/{ingest,normalize,cli,experiments,reports}/` vs flat `scripts/`

## Context
Resolves **B-013**. Every future ingester depends on this layout decision; ambiguity here forks the codebase into two patterns and increases the cost of every future PR.

The current code uses a flat `scripts/` directory (the DefiLlama MVP — `scripts/collect_defillama.py`, `scripts/normalize_defillama.py`, `scripts/build_daily_report.py`). The ChatGPT-recommended layout is `src/{ingest,normalize,cli,experiments,reports}/` — a packaged Python project structure.

This mission is **doc-only**. Don't migrate any files. Migration of the existing DefiLlama scripts happens as part of Phase 1 (B-017+) when DefiLlama gets refactored onto Postgres.

## Acceptance criteria
- [ ] `docs/repo-layout.md` exists with these sections:
  - **Decision** (one-line: which layout we're adopting).
  - **Tradeoffs** considered (flat vs nested — discoverability, packaging, CLI entry points, test paths, import depth, friction for casual contributors vs project-scale needs).
  - **Rationale** (why this is right for a multi-source data lake with CLI + experiments + reports as separate concerns).
  - **Migration plan** (what moves to where during Phase 1 — table mapping current path → target path for the DefiLlama scripts).
  - **Implications** (`pyproject.toml` from B-012, CLI entry points from Phase 3, test discovery path).
- [ ] `CLAUDE.md`'s "Open architectural decisions" entry for B-013 updated to point at `docs/repo-layout.md` instead of being marked open.
- [ ] B-013 entry in `docs/backlog.md` updated with a `**Resolved by:** docs/repo-layout.md (YYYY-MM-DD)` line at the bottom; status remains `open` (the *layout decision* is resolved but the *migration work* lands in Phase 1, so don't move B-013 to `docs/resolved.md` yet — instead, narrow its scope to "carry out the migration per docs/repo-layout.md" and lower priority if it's now blocking nothing).
- [ ] Tests still pass: `python3 -m unittest discover -s tests`.
- [ ] No existing files moved or renamed in this mission.

## Notes
- Keep `docs/repo-layout.md` to ~1 page. It's a decision record, not a tutorial.
- "src/" implies a packaged Python project — flag the implications for B-012 (pyproject.toml) so we don't paint ourselves into a corner there.
- The CLI subcommands (Phase 3) and the experiments framework (Phase 5) both land under whatever layout this mission picks — design accordingly.
- The mission queue itself (`missions/`) is **not** a code directory; it stays at repo root regardless of the decision.
