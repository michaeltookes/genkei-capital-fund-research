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
- **Note:** Mission Control was tied to the OpenClaw harness. The agent harness is now locked to Claude Code (D-017/R-030), so delivery may shift to GitHub Discussions, an external channel, or a different surface entirely.

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

### B-020 — Move Bitcoin CEX/custody exclusion keywords to config
- **Status:** open
- **Priority:** medium
- **Context:** Currently hardcoded in `scripts/normalize_defillama.py` (~19 name keywords, 4 category keywords). DeFiLlama relabels currently require code changes.
- **Acceptance criteria:**
  - `config/defillama.sources.json` gains a `bitcoin_excluded_keywords` section (name + category lists).
  - Normalizer reads keywords from config.
  - Existing tests still pass.

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

## Phase 2 — Free-data ingesters with backfill

One backlog item per source. Each follows the DeFiLlama-refactored pattern: collect → land in Postgres → normalize → tests → backfill mode.

### B-104 — CME BTC + ETH futures daily OI + volume ingester
- **Status:** blocked
- **Priority:** deferred (was high)
- **Context:** Daily institutional-positioning context for BTC + ETH futures markets, complementary to B-031's weekly CFTC view. CME Group publishes daily settlement data (volume, open interest, settle price) per contract month. **Surfaced 2026-06-02** during the ETH / SOL research sessions: the "is institutional money rotating into / out of crypto?" question that drove the user's "OG sellers" framing on ETH is best answered (on the institutional side) by daily futures OI trajectory. COT (B-031) gives the weekly *who's-long-short* breakdown; CME OI gives the daily *total-institutional-exposure* trajectory. Both matter; CME OI is the higher-frequency / lower-detail companion.
- **2026-06-03 BLOCKER:** Kickoff attempt on branch `cme-daily-oi` confirmed the public CmeWS endpoint (`https://www.cmegroup.com/CmeWS/mvc/Settlements/Futures/Settlements/{productId}/FUT`) now returns a hard TOS block to any non-browser request: *"This IP address is blocked due to suspected web scraping activity... Use of scripts, software, spiders, robots, avatars, agents, tools or other scraping mechanisms is strictly prohibited by CME Group's website Data Terms of Use."* The original spec assumed this endpoint was a "free, no auth, deterministic" feed; that's no longer true. Even with a browser User-Agent the request is rejected. This is a TOS-level block, not a rate-limit — pursuing it would be both technically blocked and ToS-violating. Path forward requires a different data source.
- **Candidate alternatives (none verified yet):**
  - **Yahoo Finance futures** (`BTC=F`, `ETH=F`) via the existing `genkei.ingest.yahoo` infrastructure. Pro: zero new TOS risk, reuses live infrastructure. Con: Yahoo publishes OHLCV only — no open interest. Closes the volume question but NOT the OI question, which is the load-bearing institutional-positioning signal. Would reframe this from "OI tracker" to "yet another price source."
  - **CME static daily files** (`https://www.cmegroup.com/market-data/files/`) — different URL pattern from CmeWS. Unverified whether subject to the same block; worth a 15-min spike if reopened.
  - **Paid market-data provider** (Polygon.io, Coin Metrics, Kaiko) — out of scope per CLAUDE.md's "free/open sources only" stance until a private-data story exists.
  - **CFTC COT (B-031, already shipped)** partially substitutes — gives weekly position breakdowns by Asset Manager / Leveraged Funds. Lower frequency, but it answers the "*who* is positioned and how" question, which is arguably more decision-useful than daily OI alone.
- **Recommended path:** stay deferred. The institutional-flow trio (B-031 / B-105 / B-106) closes most of the gap. Reopen B-104 only if a free CME data path surfaces (e.g. CFTC publishes CME OI in a sibling Socrata dataset, or a free derived feed appears).
- **Acceptance criteria (preserved for if/when reopened):**
  - New ingester `genkei.ingest.cme` reading a free CME daily settlement source. Original `CmeWS` URL above is TOS-blocked; alternative must be identified before reopening.
  - Lands raw blobs in `meta.raw_blobs` (one blob per (product, settlement-date) pair).
  - Normalizes to `cme.futures_settlements` with `(product, contract_month, settlement_date, volume, open_interest, settle_price)` keyed on `(product, contract_month, settlement_date)`.
  - Products covered v1: `BTC` (BTC futures, $50k face), `MBT` (Micro BTC, $5 face), `ETH` (ETH futures, 50-ETH face), `MET` (Micro ETH, 0.1-ETH face).
  - Daily cron in a new `cme-daily.yml` workflow at ~22:00 UTC.
  - Multi-year backfill mode (`--backfill`).
  - `genkei watchlist health` surfaces the new source.
  - `genkei futures --product BTC --since 2024-01-01` answers "what's BTC futures OI over time" out of the box.
  - Unit tests pin the parser (per-product field quirks).


### B-113 — Multi-issuer expansion of spot ETF net flow (B-107 v2.1)
- **Status:** open
- **Priority:** low
- **Context:** B-107 v1 shipped 2026-06-07 covering BlackRock-issued ETFs only (IBIT / ETHA / ETHB) via the iShares product-screener JSON. That's the dominant share of spot crypto ETF AUM (IBIT alone is ~$46B vs ~$60B total spot BTC ETF AUM as of 2026-06-05), but a complete net-flow picture needs the other major issuers. Phase 1 investigation (see `docs/sources/spot-etf-net-flow.md`) showed mixed accessibility: ARKB is Cloudflare-walled, GBTC rate-limits scripted access, FBTC URL is not yet identified, BITB plausible but unprobed. Each issuer needs its own ingester or scrape path.
- **Acceptance criteria:**
  - Per-issuer ingester(s) landing rows into the existing `etf.fund_snapshots` table (no schema changes — the table was designed to extend cleanly).
  - At least one additional issuer's full daily NAV + shares-outstanding (or shares-outstanding alone, computed against `yahoo.candles` close).
  - Watchlist health monitors each new issuer source.
  - Unit tests for the per-issuer extractor.
- **Out of scope:**
  - 10-Q quarter-end shares-outstanding triangulation — file separately if pursued (it's a different code path than the daily issuer-page scrapes).

### B-114 — SEC 10-Q quarter-end ETF shares-outstanding backfill (B-107 v2.1)
- **Status:** open
- **Priority:** low
- **Context:** B-107 v1's iShares product-screener ingester only fetches the current daily snapshot — there's no backfill path from that endpoint. Historical daily NAV / shares-outstanding lives elsewhere (likely behind iShares's JS-rendered chart widgets or a paid feed). However, the SEC 10-Q quarterly filings DO contain point-in-time shares-outstanding at quarter end — 4 checkpoints per year per ETF, going back to inception (IBIT: 2024-01-11 → 10 quarters as of mid-2026). Extracting those would let us reconstruct historical AUM trajectory (not daily flows, but quarterly drift) and triangulate against the daily snapshots once they accumulate.
- **Acceptance criteria:**
  - 10-Q parser extracting shares-outstanding as of report-period-end for IBIT / ETHA / ETHB (and any future watchlist BlackRock ETFs).
  - Lands rows in `etf.fund_snapshots` keyed on the report-period-end date (which is always quarter-end) with an explicit `source_endpoint` marker distinguishing 10-Q-derived rows from daily-feed rows.
  - Unit tests pin the 10-Q XBRL extraction.
- **Out of scope:**
  - Backfilling daily NAV / TNA between quarter-end points — that's the gap a paid feed would fill; not pursuing today per the "free/open sources only" stance.

### B-115 — SUI unlock schedule for the 7 paywalled allocations (B-089 v2)
- **Status:** open
- **Priority:** low
- **Context:** B-089 v1 (2026-06-07) shipped a CryptoRank-scraped ingester covering ONE of SUI's 8 allocation categories (Community Reserves, 10.648% of supply). The remaining 7 categories — including the load-bearing **Series A** (7.142%), **Series B** (6.956%), and **Early Contributors** (6.134%) VC tranches that actually drive the unlock-pressure bear thesis — are paywalled across all eight surveyed free sources (see `docs/sources/sui-unlocks.md`). The collector module's `KNOWN_FREE_ALLOCATIONS` tuple is the extension point: adding a name there is the entire code change once data becomes available.
- **Unblock paths (in priority order per the survey doc):**
  - Sui Foundation publishes a structured release schedule (JSON / CSV in a repo or stable URL). Lowest cost; highest signal.
  - Paid-data budget opens per CLAUDE.md's "Paid APIs deferred until a private-data story exists." DefiLlama Pro at the current per-API tier would close this immediately.
  - Tokenomist's free tier expands to cover full SUI schedules without RSC-fragility parsing.
  - On-chain vesting-object discovery — community publishes verified canonical addresses for each allocation category. A Sui RPC-based collector would then land the same shape as the CryptoRank ingester with full coverage.
- **Acceptance criteria:**
  - Extend `KNOWN_FREE_ALLOCATIONS` in `src/genkei/ingest/sui_unlocks.py` (or add a parallel collector if the new data path is structurally different — on-chain vs scraped vs paid API).
  - Per-category batch rows land in the existing `onchain.sui_unlocks` table — no schema migration needed since v1 was designed to extend cleanly.
  - Unit tests cover the new allocation parser.
  - Update the SUI 2026-05-20 research decision file's Backlog implications note to mark the gap fully closed.

### B-084 — Oracle market-share data source (likely paid)
- **Status:** open
- **Priority:** low
- **Context:** The third LINK /research data gap. Knowing whether Chainlink is *gaining or losing* oracle market share against Pyth / RedStone / native protocol oracles is the structural-thesis question for any LINK position. No obvious free source today — Pyth publishes some metrics, RedStone publishes some, but a unified comparable view is paid-API territory (Token Terminal, Messari, similar). Tracked here so the next time the project re-opens the "paid data" question this is in the queue.
- **Acceptance criteria:**
  - Survey of available sources (free + paid) with rough pricing + coverage assessment — that's the first deliverable.
  - If a free source emerges or a paid budget opens: schema + collector for cross-oracle TVS share over time, by protocol category (price feeds, randomness, CCIP-style cross-chain).
  - Pair with B-081 once both exist — would let `genkei query` join LINK's TVS share against competitors' over the same time series.

### B-116 — Enable Chainlink v0.1 legacy Staking contract (B-086 follow-up)
- **Status:** open
- **Priority:** low
- **Context:** B-086 (2026-06-07) mapped the full DefiLlama chainlink-staking surface as 3 contracts and shipped ingestion for the 2 v0.2 pools (Community + Operator). The third — the v0.1 legacy `Staking` contract at `0x3feB1e09b4bb0E7f0387CeE092a52e85797ab889` — is intentionally NOT in `DEFAULT_POOLS` because it emits different event signatures from v0.2 (the v0.2 Staked topic returns 0 results on the v0.1 address; verified live 2026-06-07). The contract still holds 0.46M LINK during unwind (~$3.5M, <1% of total chainlink-staking TVL), so its absence doesn't materially hurt the reconciliation — but the unwind dynamics themselves are interesting and would round out the surface.
- **What's required to ship:**
  - Pull the v0.1 contract's ABI from Etherscan; compute keccak256 of each Staked/Unstaked event signature.
  - Extend `PoolConfig` with per-pool event-topic overrides (or a per-pool topic-to-event-type mapping), since v0.1 and v0.2 emit different shapes.
  - Extend the parse path to accept v0.1's likely-different data field layout.
  - Add `CHAINLINK_V01_POOL = PoolConfig(protocol_slug="chainlink-v01", ...)` to `DEFAULT_POOLS`.
  - Run backfill from the v0.1 deployment block (16083969, Nov 2022).
  - Update the docstring + tests.
- **Out of scope:** generalizing to other protocols (Lido / RocketPool / EigenLayer) — that's a separate B-082 follow-up if pursued.

## Phase 3 — Custom CLI

The interface the agent (and human user) uses to query the lake.

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
  - Daily or weekly cadence aligned with the Claude Code harness decision (D-017/R-030).
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

### B-063 — Experiment template + cookiecutter
- **Status:** open
- **Priority:** low
- **Context:** Standardize the shape of new experiments to reduce friction.
- **Acceptance criteria:**
  - Template captures hypothesis, data, method, snapshot IDs, results, next steps.
  - One-command bootstrap.

## Phase 6 — Inefficiency-detection signals

Once cross-source data is in, the system starts producing investable signals.

### B-064 follow-ups — wire the remaining signal emitters

The cross-source signal correlation engine (B-064, resolved 2026-05-28) shipped the store, the correlator, the starter rule pack, and one reference emitter (`insider_clusters`). The engine cannot fire a real stack until a **second** source is wired, because the correlator enforces `min_distinct_sources ≥ 2`. The four starter rules in `src/genkei/data/signal_rules.yml` are partial-fire until their component emitters land. Each emitter is ~150–200 lines following `src/genkei/experiments/emitters/insider_clusters_emitter.py`: adapt an existing Phase 5 experiment's output into `meta.signal_events`, resolve `asset` via the watchlist, wrap in `meta.ingest_runs` (`source='signal_emitter'`) so `genkei watchlist health` surfaces staleness, register the source in `cli/watchlist.py`, and chain the run into the relevant daily workflow. See the deferred-follow-ups paragraph in the B-064 entry of `docs/resolved.md` and `docs/experiments/cross-source-signals.md`.

### B-096 — Wire the macro-regime emitter into signal_events
- **Status:** open
- **Priority:** low
- **Context:** Source experiment is `src/genkei/experiments/macro_regime.py` (B-059). The engine wants regime **transitions** (risk_on→risk_off, etc.), not continuous daily state, so the emitter de-dupes within a regime run and only fires on the boundary.
- **Acceptance criteria:**
  - New emitter `src/genkei/experiments/emitters/macro_regime_emitter.py` emits one event per regime transition with direction inferred from the transition (e.g. →risk_off = bearish overlay).
  - `asset` is a market-wide sentinel (decide convention — e.g. `MACRO`/`SPY`) documented in the emitter and `cross-source-signals.md`.
  - Standard idempotency, `meta.ingest_runs` wrapping, watchlist registration.
  - Unit tests pin transition detection (no event on same-regime days) and direction mapping.

### B-097 — Wire the watchlist-scoring emitter into signal_events
- **Status:** open
- **Priority:** low
- **Context:** Source is the composite `meta.signals` scores (B-065). Emits when an asset's composite score crosses a threshold band (e.g. into the top/bottom band), not on every score change.
- **Acceptance criteria:**
  - New emitter `src/genkei/experiments/emitters/watchlist_scoring_emitter.py` emits a band-crossing event with direction from the band.
  - Hysteresis / band definition documented so a score oscillating on a boundary doesn't emit repeatedly.
  - Standard idempotency, `meta.ingest_runs` wrapping, watchlist registration.
  - Unit tests pin band-crossing logic and no-emit-within-band behavior.

### ~~B-111 — Equity relative-strength emitter (generalize B-098 to equities vs SPY/QQQ)~~
- **Status:** resolved 2026-06-06 (see `docs/resolved.md`)
- **Priority:** medium
- **Context:** Add an equity-side relative-strength emitter so cross-source rules can pair price leadership/laggard onsets with other equity signals.
- **Acceptance criteria:**
  - Emitter reads `yahoo.candles`, compares each watchlist equity against SPY over a trailing 30-day window, and emits one signal per laggard/leader crossing onset.
  - Signal rules consume the new `equity_relative_strength` source for bullish and bearish confluence stacks.
  - Daily Yahoo workflow runs the emitter after normalize, and `genkei watchlist health` monitors it as a recurring signal emitter.
  - Tests pin thresholds, crossing behavior, event shape, watchlist routing, and UTC timestamp conversion.


### B-099 — Correlator: decay weighting by event age
- **Status:** open
- **Priority:** low
- **Context:** Today `detect_stacks` sums `weight × strength` equally for every event inside the window regardless of recency. A 7-day-old insider cluster and a same-day one contribute identically. Add an optional age-decay factor so fresher corroboration counts for more.
- **Acceptance criteria:**
  - Optional decay function (config-driven half-life) applied in `_score_window`; default off preserves current behavior.
  - Unit tests pin decayed vs undecayed scores and the default-off contract.

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

### B-073 — Secrets policy and rotation
- **Status:** open
- **Priority:** low
- **Context:** Multiple API keys; need a clear story on where they live and how often they rotate.
- **Acceptance criteria:**
  - Policy documented.
  - Rotation cadence per provider.
  - Access list captured (currently single-user, but documented).

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

## Epic E-001 — 2026-06-12 codebase-review findings

A full-codebase review (source, tests/CI, agent layer) on 2026-06-12 found the engineering layers in good shape but the research loop operationally unproven and its instructions drifted behind the shipped code. Six items, ordered by leverage. B-117 (resolved 2026-06-12, see `docs/resolved.md`) and B-118 protect the integrity of the decision/reflection loop and should land before the first real reflection cycle; the rest harden ops and code quality.

### B-118 — Dry-run the reflection cycle + trigger-fire convention
- **Status:** open
- **Priority:** high
- **Context:** Nine decisions logged, zero machine reflections — the first decision isn't horizon-eligible until ~2026-12. The loop is the calibration engine and has never executed; the one `resolved` decision (2025-12-05 CRM) was closed by a hand-written supersession note in a format the skill doesn't expect. The skill also checks `trigger_fired_at`, but no decision file populates it — the CRM→SaaS-sector supersession is exactly the event that field was designed for. B-117 landed 2026-06-12 (prompts now match the shipped CLI), so this is unblocked — the dry run will exercise the corrected equity path.
- **Acceptance criteria:**
  - One `/reflect-decisions` dry run executed on a throwaway branch with temporarily lowered horizon thresholds; bugs/gaps found are filed or fixed.
  - The first real outcome block (even from the dry run) added to `prompts/reflect-on-decisions.md` as a worked example, including what a deferred outcome looks like.
  - Trigger-fire convention documented in `docs/research/README.md`: `trigger_fired_at: YYYY-MM-DD` in frontmatter when a trigger condition fires, plus a `related:` link from the superseding decision to the superseded one.
  - CRM decision file's frontmatter retro-fitted to record the 2026-06-05 supersession under that convention.

### B-119 — Close the silent-staleness windows in ingest ops
- **Status:** open
- **Priority:** medium
- **Context:** B-071's staleness check is good defense-in-depth, but three gaps remain: (a) alerts land only as GitHub issues — visible only when someone looks; (b) nothing alerts if scheduled workflows simply don't run (Beelink runner down for two days → lake quietly stale, downstream research poisoned) — the staleness check itself runs on the same runner; (c) failed daily ingests get no retry, so a single transient API flake costs a full day of data until the next cron. Overlaps B-023 (CLI freshness warning) and B-068 (alert engine) but is narrower and earlier: push notification + runner-independence + retry.
- **Acceptance criteria:**
  - Staleness-check and workflow-failure alerts also push to a real-time channel (Discord/ntfy/email — pick one, document in `docs/infrastructure.md`).
  - A "no `ingest_run` rows for any source in 48h" check runs on **GitHub-hosted** compute so it survives homelab downtime.
  - Daily ingest workflows gain a retry-on-failure step (one templated pattern applied across the ~14 ingest workflows).
  - Long-timeout jobs (gdelt 360m, etherscan-whales 360m) get per-step timeouts or heartbeat logging so a hang is distinguishable from slow progress.

### B-120 — Promote backlog items into the mission queue
- **Status:** open
- **Priority:** medium
- **Context:** The mission queue (B-078) is fully built, tested, and documented — and has processed exactly one mission ever, while 40+ items sit in this backlog. Overnight-autonomous mode is idle capacity. This item is the process kick: pick the highest-leverage open items, write them as mission files, and run the queue.
- **Acceptance criteria:**
  - 3–5 open backlog items promoted to `missions/pending/` using `missions/_template.md` (candidates: B-117, B-053, B-047, B-064 emitter follow-ups).
  - One full `/run-missions` pass executed; completed missions land in `missions/done/` with checklists marked.
  - Friction or spec gaps found in the mission format fed back into `docs/missions.md`.

### B-121 — Code-quality pass: hoist duplicated patterns, surface silent failures
- **Status:** open
- **Priority:** medium
- **Context:** Four findings from the source/test review, none urgent but all compounding. (1) The watchlist-loader pattern (`load_coins` / `load_equities` / `load_series` / `load_products` / `load_filers` / EIA / BEA) is on its ~sixth near-copy — CLAUDE.md says hoist at the third. (2) `normalize/defillama.py` is the largest tangle: ~8 endpoint types dispatched by string-prefix matching in one module. (3) `except Exception: pass` blocks in coercion helpers (`ingest/ishares.py:110,118,129`, `ingest/sui_staking.py:164`, `ingest/sui_unlocks.py:138`) swallow unexpected failures with no log line — in unattended daily ingest that's the difference between noticing bad data and not. (4) The 11 Postgres integration test classes duplicate identical pool setup, and the `--backfill` paths — the recovery mechanism when ingest breaks — have no automated coverage.
- **Acceptance criteria:**
  - `common/watchlist.py` gains a generic `load_watchlist_entries(...)` (entries getter + dedup key); the per-ingester loaders delegate to it.
  - `normalize/defillama.py` split into a `normalize/defillama/` package with a dispatch table and per-endpoint modules.
  - Silent `except Exception` coercion blocks log a `WARNING` on non-ValueError failures.
  - A shared `PostgresTestCase` base class replaces the duplicated integration-test setup; at least one ingester's `--backfill` path gains an automated test.
  - Date-range chunking (`coinbase._chunk_windows` + the coingecko inline copy) hoisted to `common/`.

### B-122 — Resolve the output-channel decision before more emitters land
- **Status:** open
- **Priority:** medium
- **Context:** `reports/` is empty and B-001/B-051 (brief delivery) are still open. Consistent with "the lake is the asset," but it means the decision log is the *only* durable research output — if the reflection loop is miscalibrated (see B-117/B-118), no second artifact catches it. Phase 6 emitters are starting to produce signals with nowhere defined to land, and CLAUDE.md requires every signal output to carry a horizon tag. This item escalates B-051 from "decide someday" to "decide before the next emitter ships" — even a minimal decision ("reports/ in-repo, weekly cadence") suffices.
- **Acceptance criteria:**
  - B-051's decision recorded (surface + cadence + failure-mode behavior), and B-001/B-002/B-025 closed or rescoped accordingly.
  - Signal/brief outputs carry the horizon tag convention from CLAUDE.md.
  - First artifact actually lands in the chosen surface as proof of the path.
