# Backlog

This backlog tracks setup and productization work for the Genkei Capital research pipeline while the data flow is being built out.

## Vision

The repo is becoming a **queryable financial-data lake** (equities + crypto) backing four use cases:

1. **Experiments** on the data — event studies, signal/return analyses, regime classifiers.
2. **Trend analysis** across long histories.
3. **Inefficiency detection** to inform investing decisions.
4. An **on-demand AI researcher** that can be asked anything against that data.

Daily briefs and reports are emergent UIs; **the data lake is the asset**.

### Architecture decisions (locked)

- **Storage**: User's existing Postgres on a homelab Beelink server. Repo never holds raw vendor data — code, configs, samples, experiments, and reports only.
- **Agent data access**: A custom CLI tool (working name `genkei`) with typed subcommands per data domain. Agent composes CLI invocations via Bash. Each subcommand supports `--json` for the agent and human-readable output by default.
- **Backfill**: First-class. Each ingester ships a backfill mode pulling multi-year history (5–10 years where the source allows).
- **Repo visibility**: Single repo, free/open sources only. Paid APIs deferred until a private-data story exists.
- **Existing DeFiLlama MVP**: Refactored into the new architecture in Phase 1, not preserved as-is.

### Decisions still open (tracked as backlog items)

- Agent harness (Claude Code via GH Actions vs `/schedule` Routines vs manual vs hybrid) — see B-048.
- CLI tool name — see B-037.
- ~~Postgres schema/migration tool/extensions~~ → resolved 2026-05-07 (`docs/storage.md`); first migration lands with B-010/B-016.
- ~~Repo layout~~ → resolved 2026-05-07 (`docs/repo-layout.md`); migration interleaved with Phase 1 (B-013).

## Open items

### B-001 — Persist generated DeFiLlama reports back to the repository
- **Status:** open
- **Priority:** high
- **Context:** The current GitHub Action uploads generated reports as workflow artifacts only. Michael wants daily outputs available in the repo as well.
- **Acceptance criteria:**
  - Daily Markdown reports are committed to an agreed repo path, likely `reports/daily/`.
  - Normalized daily JSON is committed to an agreed repo path, likely `data/normalized/defillama/`.
  - Raw API snapshots remain uncommitted unless explicitly approved.
  - The workflow avoids noisy duplicate commits when output has not changed.
- **Note:** Re-evaluate after Phase 1 lands — if normalized data goes to Postgres, only the markdown brief may need committing.

### B-002 — Publish DeFiLlama reports to Mission Control Research tab
- **Status:** open
- **Priority:** high
- **Context:** Michael wants generated reports stored on the Mission Control site under Research.
- **Acceptance criteria:**
  - Mission Control has a Research destination for these reports.
  - The pipeline can create or update a Research entry with the daily Markdown brief.
  - Each Mission Control entry links back to the repo artifact or workflow run.
  - Failures are visible without silently losing the repo artifact.
- **Note:** Mission Control was tied to the OpenClaw harness. Reconsider once the agent-harness decision (B-048) is made — delivery may shift to GitHub Discussions, an external channel, or a different surface entirely.

### B-003 — Add manual run instructions for the DeFiLlama Daily Brief Action
- **Status:** open
- **Priority:** medium
- **Context:** The workflow supports `workflow_dispatch`, but the repo should document the UI and CLI paths for triggering it.
- **Acceptance criteria:**
  - README includes GitHub UI steps.
  - README includes `gh workflow run` command.
  - README mentions where to find generated artifacts after the run.

### B-004 — Watch first scheduled runs for data quality
- **Status:** open
- **Priority:** medium
- **Context:** The initial live smoke test succeeded, but stablecoin chain data was unavailable in the generated snapshot.
- **Acceptance criteria:**
  - First 3 scheduled runs are reviewed.
  - Any stablecoin-data gaps, schema drift, or missing target-chain rows are logged.
  - Tuning items are added to this backlog when needed.

### B-005 — Define daily report retention policy
- **Status:** open
- **Priority:** low
- **Context:** Reports can accumulate quickly once committed to repo and Mission Control.
- **Acceptance criteria:**
  - Decide retention duration for repo artifacts.
  - Decide whether Mission Control keeps all reports or summarized monthly rollups.
  - Document the policy in README or docs.

## Phase 0 — Foundation: Postgres + project scaffolding

The data lake doesn't exist yet; this phase makes it possible to land a single row.

## Phase 1 — Refactor DeFiLlama onto Postgres

Migrate the existing MVP into the new foundation; it becomes the canonical pattern for every future ingester.

### B-019 — Add backfill mode for DeFiLlama
- **Status:** open
- **Priority:** high
- **Context:** Backfill is first-class. DeFiLlama exposes historical chain TVL, stablecoin supplies, and protocol history.
- **Acceptance criteria:**
  - `--backfill --since YYYY-MM-DD` flag on the collector.
  - Resumable — re-running after partial failure picks up where it left off.
  - Documented per-endpoint history depth (some endpoints expose 5+ years, some less).

### B-020 — Move Bitcoin CEX/custody exclusion keywords to config
- **Status:** open
- **Priority:** medium
- **Context:** Currently hardcoded in `scripts/normalize_defillama.py` (~19 name keywords, 4 category keywords). DeFiLlama relabels currently require code changes.
- **Acceptance criteria:**
  - `config/defillama.sources.json` gains a `bitcoin_excluded_keywords` section (name + category lists).
  - Normalizer reads keywords from config.
  - Existing tests still pass.

### B-021 — Add provenance columns to normalized rows
- **Status:** open
- **Priority:** medium
- **Context:** Analyst trust + debugging require knowing whether a value came from `historicalChainTvl` or the rolled-up `chains` endpoint, and when it was fetched.
- **Acceptance criteria:**
  - Normalized rows include `source_endpoint`, `fetched_at`, `change_7d_source: 'history'|'api'`.
  - CLI exposes provenance via `--show-source`.

### B-022 — Add retry + backoff to the DeFiLlama collector
- **Status:** open
- **Priority:** medium
- **Context:** Single transient HTTP error currently fails the entire snapshot.
- **Acceptance criteria:**
  - Use the shared HTTP client from B-011.
  - Per-endpoint retry budget (e.g. 3 retries, exponential).
  - Failed endpoints don't tank the run unless required.

### B-023 — Add data-freshness check + visible warning
- **Status:** open
- **Priority:** medium
- **Context:** If the latest snapshot is >N hours stale, the report (or any CLI query) should make that visible.
- **Acceptance criteria:**
  - Configurable `--max-snapshot-age-hours` (default e.g. 24).
  - Report banner / CLI warning when stale.
  - `meta.ingest_runs` queryable for staleness.

### B-025 — Decide fate of existing daily-brief markdown report
- **Status:** open
- **Priority:** medium
- **Context:** With CLI-driven on-demand briefs, the auto-generated daily brief may become redundant. Or it may stay as one example downstream artifact.
- **Acceptance criteria:**
  - Decision recorded in `docs/defillama-mvp.md`.
  - If retired: workflow + script removed, B-001/B-002 closed or rescoped.
  - If kept: it now reads from Postgres via the CLI helpers.

### B-026 — Update `defillama-daily.yml` GH Action to new pipeline
- **Status:** open
- **Priority:** medium
- **Context:** Workflow currently runs the file-based pipeline; needs to point at the Postgres-backed one and may need a self-hosted runner if cloud runners can't reach the homelab.
- **Acceptance criteria:**
  - Workflow updated to invoke the refactored collector + normalizer.
  - Runner choice resolved (depends on B-006).
  - Smoke run successful end-to-end.

## Phase 2 — Free-data ingesters with backfill

One backlog item per source. Each follows the DeFiLlama-refactored pattern: collect → land in Postgres → normalize → tests → backfill mode.

### B-027 — SEC EDGAR ingester
- **Status:** open
- **Priority:** high
- **Context:** Filings + XBRL facts are the backbone of equity research. Forms 10-K, 10-Q, 8-K, S-1, Form 4, 13F.
- **Acceptance criteria:**
  - Honors 10 req/sec fair-access limit.
  - Tables for company submissions, individual filings, XBRL facts.
  - Backfill: full filing history per ticker on the watchlist.
  - Tests covering filing-type filtering, XBRL parse, error paths.

### B-028 — FRED macro-series ingester
- **Status:** open
- **Priority:** high
- **Context:** Rates, CPI, unemployment, GDP, yields — the macro spine for regime analysis.
- **Acceptance criteria:**
  - API key in GH Actions secrets.
  - `config/macro_series.yml` drives the series list.
  - Backfill: full series history per series.
  - Tests covering series fetch + revision handling.

### B-029 — BEA ingester
- **Status:** open
- **Priority:** medium
- **Context:** GDP, personal income, industry data, regional accounts.
- **Acceptance criteria:**
  - API key in secrets.
  - Backfill full available history.
  - Documented dataset coverage in `docs/sources/bea.md`.

### B-030 — Treasury Fiscal Data ingester
- **Status:** open
- **Priority:** medium
- **Context:** Debt issuance, auctions, Treasury cash balance, interest costs.
- **Acceptance criteria:**
  - Free public API; no key required.
  - Backfill where exposed.
  - Daily refresh schedule.

### B-031 — CFTC Commitments of Traders ingester
- **Status:** open
- **Priority:** medium
- **Context:** Positioning data — rates, metals, energy, equity index, FX. Weekly cadence.
- **Acceptance criteria:**
  - Weekly schedule.
  - Multi-year backfill.
  - Tables aligned with reportable position classes.

### B-032 — EIA energy data ingester
- **Status:** open
- **Priority:** medium
- **Context:** Oil inventories, natural gas storage, electricity demand — useful for energy-sector context.
- **Acceptance criteria:**
  - API key (free).
  - Full series backfill.
  - Configurable series list in `config/macro_series.yml` or sibling.

### B-033 — GDELT news/event ingester
- **Status:** open
- **Priority:** medium
- **Context:** Global news firehose for topic monitoring + geopolitical risk.
- **Acceptance criteria:**
  - Rolling window storage with documented retention (full backfill is huge).
  - Topic + entity + tone fields preserved.
  - Per-watchlist filtering option.

### B-034 — CoinGecko crypto market-data ingester
- **Status:** open
- **Priority:** high
- **Context:** Live + historical crypto prices, liquidity, volume — required for any crypto experiment.
- **Acceptance criteria:**
  - Free-tier rate limits respected.
  - Backfill 5–10 years where free tier allows.
  - Watchlist-driven asset list.

### B-035 — Binance public market-data ingester
- **Status:** open
- **Priority:** medium
- **Context:** Exchange-specific OHLCV cross-checks — useful when CoinGecko aggregates look suspicious.
- **Acceptance criteria:**
  - No API key required for public endpoints.
  - Backfill what's free; document what isn't.
  - Tables aligned with kline structure.

### B-036 — Per-source ingest documentation
- **Status:** open
- **Priority:** medium
- **Context:** Each source gets its own doc explaining endpoints, schema mapping, freshness expectations, known quirks.
- **Acceptance criteria:**
  - `docs/sources/<name>.md` for every ingester (DeFiLlama first as the template).
  - Acceptance gates included (mirroring `docs/defillama-daily-review.md` pattern).

## Phase 3 — Custom CLI

The interface the agent (and human user) uses to query the lake.

### B-037 — Choose CLI tool name and surface
- **Status:** open
- **Priority:** medium
- **Context:** Working name `genkei`. Subcommand grouping defines the agent's mental model.
- **Acceptance criteria:**
  - Name decided.
  - Top-level subcommand list documented (e.g. `prices`, `filings`, `tvl`, `macro`, `news`, `watchlist`, `query`).
  - Global flags documented (`--json`, `--since`, `--until`, `--ticker`, `--limit`).

### B-038 — Build CLI scaffold (Click or Typer)
- **Status:** open
- **Priority:** medium
- **Context:** Need an entry point with consistent help output, error handling, and config loading.
- **Acceptance criteria:**
  - CLI installable via `pip install -e .`.
  - `genkei --help` lists all subcommands.
  - Reads Postgres connection from env/config.

### B-039 — Implement `genkei prices` subcommand
- **Status:** open
- **Priority:** medium
- **Context:** Crypto + (later) equity prices. The most-used query.
- **Acceptance criteria:**
  - `genkei prices --ticker BTC --since 2020-01-01` returns OHLCV.
  - `--json` mode emits machine-readable rows.
  - Handles missing data ranges explicitly.

### B-040 — Implement `genkei filings` subcommand
- **Status:** open
- **Priority:** medium
- **Context:** SEC EDGAR queries: by ticker, form type, date range; includes filing URL.
- **Acceptance criteria:**
  - `genkei filings --ticker AAPL --form 8-K --since 2024-01-01`.
  - Output includes filing URL + extracted fact summary.

### B-041 — Implement `genkei tvl` subcommand
- **Status:** open
- **Priority:** medium
- **Context:** DeFiLlama queries: by chain, by protocol, with rolling change windows.
- **Acceptance criteria:**
  - `genkei tvl --protocol Aave --window 7d`.
  - `genkei tvl --chain Ethereum --since 2023-01-01`.
  - Includes momentum/zombie risk classification per existing logic.

### B-042 — Implement `genkei macro` subcommand
- **Status:** open
- **Priority:** medium
- **Context:** FRED + BEA + Treasury series queries.
- **Acceptance criteria:**
  - `genkei macro --series DGS10 --since 2020-01-01`.
  - Cross-series comparison (`--series DGS10,DGS2`).

### B-043 — Implement `genkei news` subcommand
- **Status:** open
- **Priority:** medium
- **Context:** GDELT topic and date filters.
- **Acceptance criteria:**
  - `genkei news --topic "AI capex" --since 2024-01-01`.
  - Cluster output with representative URLs.

### B-044 — Implement `genkei watchlist` subcommand
- **Status:** open
- **Priority:** medium
- **Context:** Operations on the watchlist — list assets, last-update-per-asset, gaps.
- **Acceptance criteria:**
  - `genkei watchlist list`, `watchlist gaps`, `watchlist health`.
  - Surfaces stale or missing data per asset.

### B-045 — Implement `genkei query` escape hatch
- **Status:** open
- **Priority:** low
- **Context:** Ad-hoc SQL with safety guards for queries the typed subcommands don't cover.
- **Acceptance criteria:**
  - Read-only Postgres role enforced.
  - Query timeout enforced.
  - Result-row cap with explicit override.

### B-046 — CLI session caching
- **Status:** open
- **Priority:** low
- **Context:** Agent often issues the same query multiple times in a session. Caching speeds it up.
- **Acceptance criteria:**
  - In-process cache with sensible TTL.
  - `--no-cache` flag.
  - Cache key includes all query parameters.

### B-047 — CLI documentation in `docs/cli/`
- **Status:** open
- **Priority:** medium
- **Context:** Every subcommand gets `--help` text + a worked example. The agent reads these when it doesn't know how to query.
- **Acceptance criteria:**
  - One markdown file per subcommand under `docs/cli/`.
  - Examples include both human and `--json` output.

## Phase 4 — Agent layer

Wires the data lake to the on-demand AI researcher.

### B-048 — Evaluate agent harness options
- **Status:** open
- **Priority:** high
- **Context:** OpenClaw is no longer the harness. Candidates: Claude Code via GH Actions + `claude-code-action`, `/schedule` Routines, manual local Claude Code, hybrid (continuous ingest in CI, on-demand reasoning local).
- **Acceptance criteria:**
  - Tradeoffs written up in `docs/agent-harness.md`.
  - Decision recorded with rationale.
  - First reference implementation chosen.

### B-049 — Define agent prompt and capabilities
- **Status:** open
- **Priority:** medium
- **Context:** Prompt template guides the agent to use the CLI, surface contradictions, suggest experiments.
- **Acceptance criteria:**
  - Prompt template lives in `prompts/` (or chosen location).
  - Defines: data-access pattern (CLI), output shape, escalation rules.
  - Versioned alongside CLI changes.

### B-050 — Implement chosen agent harness end-to-end
- **Status:** open
- **Priority:** medium
- **Context:** Wire data lake → CLI → agent → output for the harness chosen in B-048.
- **Acceptance criteria:**
  - Demo run completes a real ad-hoc question end-to-end.
  - Errors propagate visibly (no silent failures).

### B-051 — Decide brief delivery surface
- **Status:** open
- **Priority:** medium
- **Context:** Where do generated outputs land? Repo commit, GitHub Issue/Discussion, external channel?
- **Acceptance criteria:**
  - Decision documented.
  - Delivery wired into the harness.
  - Failure-mode behavior defined (retry? alert? drop?).

### B-052 — "Open research questions" log
- **Status:** open
- **Priority:** low
- **Context:** Agent appends questions worth follow-up to a tracked file the user can review.
- **Acceptance criteria:**
  - Tracked file exists (e.g. `docs/research-questions.md`).
  - Agent appends with date + question + originating context.
  - User can mark items resolved without breaking format.

### B-053 — Periodic ingest-health summary
- **Status:** open
- **Priority:** low
- **Context:** Agent reports staleness per source, schema drift, anomalies — surfaces operational issues without manual checks.
- **Acceptance criteria:**
  - Daily or weekly cadence (decided in B-048 context).
  - Summary covers every active source.
  - Anomalies link back to `meta.ingest_runs`.

## Phase 5 — Experiments framework

First-class — the *point* of having the data lake.

### B-054 — Notebooks directory + reproducibility pattern
- **Status:** open
- **Priority:** medium
- **Context:** Experiments need deterministic seeds, snapshot pinning (which `ingest_runs` IDs were used), per-experiment notes.
- **Acceptance criteria:**
  - `notebooks/experiments/` with template structure.
  - Each experiment captures snapshot IDs, seeds, config.
  - `experiment.md` per experiment summarizes hypothesis, result, next steps.

### B-055 — Notebook-to-Postgres connection pattern
- **Status:** open
- **Priority:** medium
- **Context:** Standard helper for notebooks to query Postgres (via the CLI helpers or direct psycopg).
- **Acceptance criteria:**
  - Helper module with `get_session()` / `read_sql_df(...)`.
  - One example notebook using it cleanly.

### B-056 — Experiment: news sentiment vs next-day returns
- **Status:** open
- **Priority:** medium
- **Context:** Classic study using GDELT + prices.
- **Acceptance criteria:**
  - Notebook produces correlation table + a chart.
  - Reproducible from snapshot IDs.

### B-057 — Experiment: 8-K filing impact study
- **Status:** open
- **Priority:** medium
- **Context:** SEC + prices — does a tag/category of 8-K predict short-run drift?
- **Acceptance criteria:**
  - Event-study notebook covering pre/post windows.
  - Per-watchlist results.

### B-058 — Experiment: TVL drawdown early-warning model
- **Status:** open
- **Priority:** medium
- **Context:** DeFiLlama + prices — does TVL change predict price drawdowns?
- **Acceptance criteria:**
  - Logistic or simple ML baseline + a notebook.
  - Out-of-sample validation.

### B-059 — Experiment: macro regime classifier
- **Status:** open
- **Priority:** medium
- **Context:** FRED + Treasury + market prices — bucket regimes (e.g. risk-on/risk-off).
- **Acceptance criteria:**
  - Notebook produces regime labels per date.
  - Output materialized as a Postgres view for downstream queries.

### B-060 — Experiment: insider-buying monitor
- **Status:** open
- **Priority:** medium
- **Context:** SEC Form 4 + prices — flag clusters of insider buys.
- **Acceptance criteria:**
  - Notebook + a watchlist alert hook.

### B-061 — Experiment: 13F crowding monitor
- **Status:** open
- **Priority:** low
- **Context:** SEC 13F + holdings — track crowding in watchlist names.
- **Acceptance criteria:**
  - Notebook surfaces top crowded names per quarter.

### B-062 — Experiment: crypto protocol revenue vs token price
- **Status:** open
- **Priority:** medium
- **Context:** DeFiLlama + CoinGecko — fundamental link between revenue and price.
- **Acceptance criteria:**
  - Notebook + per-protocol results.

### B-063 — Experiment template + cookiecutter
- **Status:** open
- **Priority:** low
- **Context:** Standardize the shape of new experiments to reduce friction.
- **Acceptance criteria:**
  - Template captures hypothesis, data, method, snapshot IDs, results, next steps.
  - One-command bootstrap.

## Phase 6 — Inefficiency-detection signals

Once cross-source data is in, the system starts producing investable signals.

### B-064 — Cross-source signal correlation engine
- **Status:** open
- **Priority:** medium
- **Context:** TVL change + 8-K event + news cluster on the same ticker/protocol is more meaningful than any single signal.
- **Acceptance criteria:**
  - Engine produces a `meta.signals` table.
  - Configurable correlation rules.
  - CLI + notebook access to query.

### B-065 — Watchlist scoring rubric
- **Status:** open
- **Priority:** medium
- **Context:** Each asset gets a daily score from a defined formula. Scores persisted so the score itself can be backtested.
- **Acceptance criteria:**
  - Rubric documented in `docs/scoring.md`.
  - Daily scores written to Postgres.
  - CLI access (`genkei watchlist score`).

### B-066 — Macro regime classifier integrated into queries
- **Status:** open
- **Priority:** low
- **Context:** Output from B-059 surfaced as a Postgres view callable from any query.
- **Acceptance criteria:**
  - View `meta.regime_per_date` exists.
  - CLI exposes regime context (`genkei macro --regime`).

### B-067 — Multi-day trend aggregations as Postgres views
- **Status:** open
- **Priority:** low
- **Context:** 3-day, 7-day, 30-day momentum tables shouldn't be recomputed in every CLI call.
- **Acceptance criteria:**
  - Materialized views per source for common windows.
  - Refresh cadence documented.

### B-068 — Threshold-based alert engine
- **Status:** open
- **Priority:** low
- **Context:** Configurable thresholds emit events to `meta.alerts`. Agent surfaces these on demand.
- **Acceptance criteria:**
  - YAML-defined thresholds.
  - `meta.alerts` table.
  - Optional notification hook (Discord webhook, email, GH issue).

### B-069 — Anomaly detection on per-series outliers
- **Status:** open
- **Priority:** low
- **Context:** Z-score or MAD-based outlier detection per series — flags worth investigating.
- **Acceptance criteria:**
  - Per-series rolling z-score or MAD computed.
  - Flags written to `meta.anomalies`.
  - Surfaced via CLI.

## Phase 7 — Operations & hardening

Reliability work that grows in importance as more sources go live.

### B-070 — Confirm and document Postgres backup posture
- **Status:** open
- **Priority:** medium
- **Context:** Homelab Beelink may already have backups; confirm cadence, test restore, document.
- **Acceptance criteria:**
  - Backup cadence verified.
  - Restore drill performed and documented.
  - Off-site copy strategy decided.

### B-071 — Workflow-failure + ingest-staleness alerting
- **Status:** open
- **Priority:** medium
- **Context:** Silent failures defeat the "constantly gathering" goal.
- **Acceptance criteria:**
  - GH Actions failures notify the user (issue auto-open or external channel).
  - Ingest staleness >threshold per source triggers an alert.

### B-072 — Schema-drift detection
- **Status:** open
- **Priority:** medium
- **Context:** Source APIs add/remove fields; pipeline should surface this rather than silently degrading.
- **Acceptance criteria:**
  - Per-source canary check on field shape.
  - Drift logged + surfaced in ingest-health summary.

### B-073 — Secrets policy and rotation
- **Status:** open
- **Priority:** low
- **Context:** Multiple API keys; need a clear story on where they live and how often they rotate.
- **Acceptance criteria:**
  - Policy documented.
  - Rotation cadence per provider.
  - Access list captured (currently single-user, but documented).

### B-074 — Architecture diagram + expanded README
- **Status:** open
- **Priority:** medium
- **Context:** Onboarding the user-as-future-self after weeks away — and onboarding the agent itself.
- **Acceptance criteria:**
  - High-level diagram in `docs/architecture.md`.
  - README explains data lake + CLI + agent end-to-end.
  - Per-component pointers to deeper docs.

### B-075 — License and redistribution audit
- **Status:** open
- **Priority:** low
- **Context:** Even free APIs have TOS that constrain what can be committed. One-time audit + a check on each new source.
- **Acceptance criteria:**
  - Per-source compliance recorded in `docs/sources/<name>.md`.
  - Repo-level audit before adding any paid source.

### B-076 — Cost / quota tracking per source
- **Status:** open
- **Priority:** low
- **Context:** Free tiers have ceilings; want visibility before hitting them in production.
- **Acceptance criteria:**
  - Per-source quota tracked in `meta.api_usage`.
  - CLI + dashboard query.

### B-077 — Self-hosted GH Actions runner if needed
- **Status:** open
- **Priority:** medium
- **Context:** Confirmed in B-006 that the Beelink is on a private LAN behind double NAT — GitHub-hosted runners cannot reach it. A self-hosted runner is the recommended path; `docs/infrastructure.md` notes Cloudflare TCP tunnel as an alternative once we need cloud-runner access.
- **Acceptance criteria:**
  - Self-hosted runner installed on the Beelink, registered to this repo, with Docker network access to `mission_control_net` so it can reach `genkeicapital-postgres`.
  - GH Actions workflows targeting Postgres pinned to the self-hosted runner via `runs-on:` label.
  - Recovery steps documented (how to restart, what to check if jobs queue forever).
