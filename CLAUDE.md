# CLAUDE.md

Project guide for any Claude session working in this repo. Keep it accurate — out-of-date claims here propagate.

## What this is

**Genkei Capital research-desk**: a queryable financial-data lake (equities + crypto) backing four use cases:

1. **Experiments** — event studies, signal/return analyses, regime classifiers.
2. **Trend analysis** — long histories.
3. **Inefficiency detection** — informs Michael's investing decisions.
4. **On-demand AI researcher** — Michael asks anything against the data; the agent answers via the CLI.

**The data lake is the asset.** Daily briefs and reports are emergent UIs.

## Investment context

Operating *as if* a real fund — data hygiene, archival, audit trail at fund-grade. **Actual capital is personal + close friends/family.** No fiduciary duty, no LP filings, but outputs must be defensible if scope expands later.

### Edge types pursued (all four)

- **Macro / regime-driven** — the spine. Equities and crypto are downstream of macro.
- **Event-driven** — filings (8-K, 10-Q, 13F, Form 4), earnings, token unlocks, protocol launches, news clusters.
- **Fundamentals / valuation** — revenue, fees, TVL vs market cap.
- **Technical / momentum / flow** — TVL drawdowns, exchange flows, momentum signals.

### Sleeves

- **Equity core** — long-only, buy-and-hold quality companies (Buffett mentality: buy great, never sell while income covers expenses). **No short-term equity sleeve.**
- **Crypto core (long-term hold)** — BTC, ETH, SOL, LINK, JUP. JUP (added 2026-06-17) is a deliberate DeFi-adoption bet — dominant Solana DEX aggregator + on-chain perps — tracked both as a token (price) and as a protocol (TVL/fees via the `protocols:` Solana section).
- **Crypto tactical (turnover-eligible)** — SUI (primary watchlist), PYTH, RENDER (secondary watchlist), and any future alts.

The **crypto watchlist** lives in `src/genkei/data/watchlists.yml`. Tier (primary/secondary) and sleeve (core/tactical) are orthogonal: tier is how much *coverage* the data lake gives an asset; sleeve is how the user *trades* it.

### Horizons

Mixed across sleeves; most signals inform mid-to-long-term decisions. **Every signal output should carry a horizon tag** so Michael knows which sleeve it informs.

## Architecture (locked decisions)

- **Storage**: Existing TimescaleDB-backed Postgres on Michael's homelab Beelink server (`genkeicapital-postgres`, `timescale/timescaledb:2.26.4-pg16`, port 5440 on `mission_control_net`). `shared_preload_libraries = 'timescaledb'` was set via `ALTER SYSTEM`, and `timescaledb 2.26.4` is installed against the `genkei_capital` database. Repo never holds raw vendor data. See `docs/infrastructure.md` for the full picture; full connection specs live in the user's `/server-info` skill (local-only).
- **Agent data interface**: Custom CLI tool (working name `genkei`) with typed subcommands per data domain. Agent composes CLI invocations via Bash. Every subcommand supports `--json` for the agent + human-readable output by default.
- **Backfill**: First-class. Each ingester ships a backfill mode pulling multi-year history (5–10 years where the source allows).
- **Repo visibility**: Single repo, free/open sources only. Paid APIs deferred until a private-data story exists.
- **Existing DeFiLlama MVP**: Refactored into the new architecture in Phase 1, **not preserved as-is**.

## Harness (hybrid)

| Mode | Tool | Use for |
|---|---|---|
| Scheduled ingest | GH Actions (`.github/workflows/`) | Daily/hourly raw-data pulls. Proven, free compute. |
| Autonomous agent-think | `/schedule` Routines (Claude Code) | Reviewing data, generating briefs, working the mission queue, running the reflection cycle. Where reasoning between steps matters. |
| Synchronous pairing & on-demand research | Local Claude Code | Weekend-long sessions, design, debugging, ad-hoc Q&A research over the lake. |

GH Actions handle deterministic ingest. **Claude Code is the agent harness** — both for synchronous pairing and for scheduled `/schedule` Routines (D-017 in `docs/architecture.md`). We do not build a separate Python LLM-calling framework; the Phase 4 agent layer is a structured methodology + decision-log + reflection cycle that runs *in Claude Code*, querying the lake via the Phase 3 CLI. Multi-agent shapes (TradingAgents, Pi) are deferred-not-rejected — re-evaluate if scope expands beyond single-agent Q&A.

## Working pattern

Three modes, all in active use:

- **Weekend-long sessions** — 1–3 hours of synchronous pairing, often crypto-heavy.
- **Weekday short bursts** — 15–60 min check-ins. Make progress, Michael steers.
- **Overnight autonomous** — Claude grinds through the **mission queue** until empty.

### Mission queue

Long-running task lists meant for async / overnight execution.

- Path: `missions/pending/` (queued) and `missions/done/` (completed).
- Each mission is **one markdown file** with: a clear title, the context, and a checklist of acceptance criteria.
- Agent picks the oldest file in `pending/`, works it through, moves it to `done/` with the checklist marked, picks the next, repeats.
- Stops when `pending/` is empty.
- Implementation tracked in backlog item **B-078**.

## Output channel

Briefs, alerts, and agent answers commit to the repo under `reports/` (D-024). The durable signal artifact is the **weekly signal digest** — `python -m genkei.reports.signal_digest` renders the trailing-week correlator output into `reports/signals/weekly-<date>.md`, grouped by horizon tag, with a lake-health footer. Cadence is weekly via a `/schedule` routine (runner-agnostic; a GH Actions cron works too); failed runs alert via the B-119 Discord/issue path rather than dropping silently. The legacy DeFiLlama daily markdown brief is **retired** (B-025) — the lake is the system of record and on-demand `genkei` queries cover the rest. Future migration to a nicer interface is possible but out of scope today.

## Conventions

- **Branches**: feature branches, never push to `main`. Default branch is `main`.
- **Tests**: `python3 -m unittest discover -s tests` must pass before any push. Tests are deterministic and offline today; Postgres tests will use ephemeral fixtures (B-024).
- **PR descriptions**: short. `## Summary` (paragraph or 2–4 bullets) + `## Test plan` (1–2 lines). Skip enumerated change lists, branch/commit footers, "Generated with Claude Code" tags. Add `## Risks` only when something genuinely needs flagging.
- **Commit messages**: explain the *why*, not the *what*. Co-author trailer on automated commits is fine.
- **Secrets**: never in the repo. GH Actions secrets for CI; `.env` (gitignored) locally; `.env.example` lists every variable.
- **Raw vendor data**: never committed. `data/` may hold tiny fixtures only. Postgres is the system of record.
- **Backlog hygiene**: items in `docs/backlog.md`, completed work in `docs/resolved.md`. Use the `update-backlog` skill after meaningful commits.
- **Slash commands & skills**: `/pr` opens PRs (size guardrails); the `pr-body` skill drafts PR descriptions automatically when work on a non-main branch is done.

## Clean-code principles

The data lake will grow many sources; duplication compounds fast. Before adding a new ingester, CLI subcommand, or experiment, look for an existing shared helper. If one of these patterns is about to be copied, hoist it instead:

- **Watchlist loading** — `genkei.common.watchlist.load_watchlist()` is the single source of truth for crypto / equity / macro / protocol entries. Every ingester and CLI subcommand reads through it. Do **not** re-parse `watchlists.yml` inline; extend `MacroEntry` / `EquityEntry` / `CryptoEntry` / `ProtocolEntry` if you need a new field.
- **Raw blob writes** — `genkei.common.db.store_raw_blob(ingest_run_id, endpoint_name, url, payload)` is the only place that writes `meta.raw_blobs`. For resumable backfills, `db.copy_raw_blob_for_run(...)` preserves the original `fetched_at`. Do **not** redefine `RAW_BLOBS_INSERT` SQL strings or per-source `_store_blob` helpers.
- **CLI helpers** — `genkei.cli._helpers.json_default` (Decimal + isoformat) and `parse_date(raw, *, label)` (YYYY-MM-DD with friendly errors) are shared by every subcommand. Import them as `_json_default` / `_parse_date` to match call-site convention.
- **Ingest/CLI shape** — every collector wraps work in `db.ingest_run(source, endpoint=…)` and uses `db.bulk_upsert(...)` for writes. Every CLI subcommand supports `--json` and pairs human + JSON formatters in the same module.

When you spot the *third* near-copy of any pattern, that's the signal to extract a shared helper rather than copy again. Bias to the canonical-loader approach (typed dataclasses in `common/`) over inline parsers — it pays back the second time it's used and keeps a fix in one place from rotting elsewhere.

Don't, however, *premature*-abstract. Two similar lines are fine. Wait for the third — by then you know what stays constant and what varies.

## Open architectural decisions

Tracked as backlog items so they don't block forward motion:

- ~~**B-008/B-009** — Postgres schema / migrations~~ → resolved by `docs/storage.md` (2026-05-07).
- ~~**B-007** — Activate TimescaleDB~~ → resolved 2026-05-09: image swapped to `timescale/timescaledb:2.26.4-pg16`, `shared_preload_libraries = 'timescaledb'` set via `ALTER SYSTEM`, extension `timescaledb 2.26.4` installed against `genkei_capital` database.
- ~~**B-013** — Repo layout~~ → resolved by `docs/repo-layout.md` (2026-05-07): `src/genkei/{common,ingest,normalize,cli,experiments,reports}/`. Migration lands in Phase 1.
- ~~**B-015** — Watchlists~~ → resolved by `src/genkei/data/watchlists.yml` (2026-05-09): crypto, equities, and macro series landed.
- **B-037** — CLI name (working: `genkei`).

## References

- `docs/backlog.md` — 43 open items across 8 phases.
- `docs/resolved.md` — completed milestones.
- `docs/defillama-mvp.md` / `docs/defillama-daily-review.md` — existing slice's design + review standard.
- `notebooks/README.md` — reproducible experiments layer (B-054/B-055): `genkei.common.notebook` (`get_session`/`read_sql_df`/`snapshot_manifest`/`new_experiment`) + dated experiment folders. Needs the `[notebooks]` extra (`pip install -e ".[notebooks]"`).
- `docs/research/README.md` — investment-research decision log (append-only; `/research` + `/reflect-decisions` skills drive it).
- `prompts/research-methodology.md` — the structured checklist `/research` walks per session.
- `prompts/reflect-on-decisions.md` — the outcome-pairing cycle `/reflect-decisions` runs.
- `~/.claude/skills/server-info/` — homelab Postgres connection specs (load before infra work).
- `~/.claude/skills/pr-body/` — PR-body drafter (model-invocable, draft-only).
- `~/.claude/skills/pr/` — PR opener (user-invoked via `/pr`, size guardrails).
- `.claude/skills/research/` — project-local skill (B-049 / B-050) — disciplined investment-research session driver.
- `.claude/skills/reflect-decisions/` — project-local skill — outcome-pairing cycle.
