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


### B-106 — Etherscan whale-flow tracker (top-N ETH wallet net flow)
- **Status:** open
- **Priority:** medium
- **Context:** The user's "OG sellers" framing on ETH directly maps to "are large long-term ETH wallets net-selling?" — a question the lake explicitly couldn't answer in the 2026-06-02 ETH session. Etherscan v2 API (free tier 100k calls/day, already used by B-082 for LINK staking) exposes per-address balance history + transaction history, which is enough to track per-day net flow for a maintained list of known whale addresses. The list of addresses is the hard part: it has to be curated (exchanges, custodians, foundation wallets, known whales identified by community analyses). Once curated, the ingest is mechanical. Lower priority than B-031 / B-104 / B-105 because (a) the address-list curation is ongoing rather than one-shot, (b) the data is per-wallet rather than aggregate so the "institutional vs retail" cut is harder to make, and (c) the COT / CME / ETF flow data closes most of the institutional-flow gap at a higher signal-to-noise ratio.
- **Acceptance criteria:**
  - Watchlist gains an `eth_whale_addresses:` section listing addresses with `address`, `label` (e.g. "Binance cold wallet 1", "Ethereum Foundation"), `category` (exchange / custodian / foundation / whale), `notes`. v1 seed list: ~25-50 known addresses from publicly-documented sources (Etherscan's own labeled-addresses dataset, known foundation wallets, Lookonchain-style published whale identifications). Document curation methodology in `docs/sources/eth-whale-addresses.md`.
  - New ingester `genkei.ingest.etherscan_whale_flow` reading Etherscan v2 API. Uses the same `ETHERSCAN_API_KEY` env var as B-082; gracefully skips with a loud warning when no key is set (per D-020).
  - Daily per-address snapshot: `(address, ts, balance_eth, balance_usd_at_snapshot, net_flow_eth_24h, net_flow_usd_24h, tx_count_24h)` keyed on `(address, ts)`.
  - Lands raw blobs (one blob per address per day) for audit trail.
  - Normalizes to `onchain.eth_whale_flows`. View `onchain.eth_whale_flows_aggregate` sums per category (exchange / custodian / foundation / whale) per day for the "are whales net-selling?" headline query.
  - Daily cron in a new `etherscan-whales-daily.yml` workflow, or extended into `sec-daily.yml`'s self-hosted runner block to share the Etherscan rate-limit budget with B-082.
  - Backfill mode walks Etherscan's transaction history for each address back to a configurable `--since` date (default: 1 year).
  - `genkei whales --category whale --since 2025-01-01` answers "are tracked whales net-selling ETH over time" without custom code.
  - Unit tests pin the Etherscan v2 response parser + the 24h-net-flow computation (sum of incoming txns minus sum of outgoing txns, with ETH-only filter; ERC-20 transfers ignored for v1).
  - **Known limitations**: (a) the address-list is necessarily incomplete — true whales obfuscate via fresh wallets; (b) exchange cold wallets aggregate many users so "exchange net inflow" doesn't equal "users buying" — exchange flows often reverse the intuitive sign; (c) Etherscan rate limits will cap address-list size at ~500 addresses per daily run. Document these limits in the writeup so users don't over-read the data.

### B-107 — Spot ETF true net flow via SEC EDGAR (v2 of B-105)
- **Status:** open
- **Priority:** medium
- **Context:** B-105 v1 shipped 2026-06-03 as "daily-dollar-volume per asset" via Yahoo OHLCV — a magnitude proxy for institutional ETF activity, NOT signed net flow. The pivot from the original Farside spec was forced by Cloudflare-walled scrape sources (Farside + SoSoValue) and Yahoo's auth-gated `quoteSummary` (which has shares-outstanding). The institutional-flow gap that drove the 2026-06-02 ETH/SOL/SUI research sessions is partially closed by B-105 v1 (we now see *if* ETF trading volume is heavy) but NOT fully closed (we don't see *if* creations exceed redemptions on a given day — the canonical "is money flowing in or out?" question). This item pursues the missing piece via the primary-source path: SEC EDGAR daily filings.
- **Investigation required before kickoff:**
  - Which SEC form(s) carry daily creation/redemption baskets or daily NAV+shares-outstanding for spot ETFs? Candidates: N-CEN (annual), N-PORT (quarterly, too slow), Form 8937 (corporate actions), Form NT-NCEN, Form NPX (annual). May require reading actual IBIT / FBTC filings to map.
  - Each ETF issuer also publishes daily NAV + shares-outstanding on their own product page (e.g. ishares.com/us/products/333011/IBIT). Whether those pages are Cloudflare-walled like Farside or directly scrape-able needs probing — IBIT alone may be ~250 trading days × 1.5y = ~400 calls for a backfill, manageable if it works.
  - Does Coinbase's institutional product feed have a free tier for ETF creation/redemption data? (Likely no, but worth a quick check.)
- **Acceptance criteria (v2, draft — refine after investigation):**
  - Per-ETF per-day shares-outstanding from a primary or near-primary source (SEC or issuer page).
  - Derived net flow = (shares_outstanding_today − shares_outstanding_yesterday) × close.
  - Lands in a new schema, e.g. `etf.net_flows`, keyed on `(ticker, flow_date)`. The B-105 v1 `dollar_volume_usd_mm` column remains the activity-magnitude proxy; this new column is the signed direction.
  - Daily cron alongside `yahoo-daily.yml`; backfill per ticker from the ETF launch date forward.
  - `genkei etf-flows --asset BTC --net-flow` shows the signed column; default behavior stays on `dollar_volume_usd_mm` so v1 callers don't break.
  - Unit tests pin the shares-outstanding extractor for whichever source is chosen.
- **References:**
  - Original B-105 spec (resolved 2026-06-03) for the Farside + SoSoValue Cloudflare findings.
  - Yahoo `quoteSummary` is auth-gated; B-092's existing chart-only path is what unblocked v1.

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

### B-036 — Per-source ingest documentation
- **Status:** open
- **Priority:** medium
- **Context:** Each source gets its own doc explaining endpoints, schema mapping, freshness expectations, known quirks.
- **Acceptance criteria:**
  - `docs/sources/<name>.md` for every ingester (DeFiLlama first as the template).
  - Acceptance gates included (mirroring `docs/defillama-daily-review.md` pattern).

### B-084 — Oracle market-share data source (likely paid)
- **Status:** open
- **Priority:** low
- **Context:** The third LINK /research data gap. Knowing whether Chainlink is *gaining or losing* oracle market share against Pyth / RedStone / native protocol oracles is the structural-thesis question for any LINK position. No obvious free source today — Pyth publishes some metrics, RedStone publishes some, but a unified comparable view is paid-API territory (Token Terminal, Messari, similar). Tracked here so the next time the project re-opens the "paid data" question this is in the queue.
- **Acceptance criteria:**
  - Survey of available sources (free + paid) with rough pricing + coverage assessment — that's the first deliverable.
  - If a free source emerges or a paid budget opens: schema + collector for cross-oracle TVS share over time, by protocol category (price feeds, randomness, CCIP-style cross-chain).
  - Pair with B-081 once both exist — would let `genkei query` join LINK's TVS share against competitors' over the same time series.

### B-088 — Sui on-chain validator + staking-flow ingester
- **Status:** open
- **Priority:** medium
- **Context:** Surfaced by the SUI /research session (2026-05-20). Equivalent to the LINK B-082 ingester on Ethereum, but for the Sui consensus stake. The session noted "no Sui-chain validator / staking flow" as the second-biggest data gap on crypto-tactical assets — without it, "are stakers committing more capital or unbonding" is unanswerable, which is exactly the signal that would distinguish a Sui-chain bottom from a death-rattle. Blockvision (`https://docs.blockvision.org/reference/rpc-node-for-sui`) exposes a managed Sui JSON-RPC endpoint that supports the standard `suix_getLatestSuiSystemState`, `suix_getValidatorsApy`, `suix_getCommitteeInfo`, and `suix_getStakes` methods — the natural source for validator + staking data. Mirrors the precedent set by B-082's Etherscan-V2-keyed collector for LINK.
- **Acceptance criteria:**
  - Verify Blockvision's free-tier availability + rate limits for the Sui RPC endpoint. Document the key registration flow (likely BLOCKVISION_API_KEY env var pattern, mirroring ETHERSCAN_API_KEY in B-082). If a key is required, follow the D-020 graceful-skip-when-no-key pattern: collector records a successful run with 0 rows + loud warning rather than failing the daily cron.
  - New schema (`onchain.sui_validators` or extend `onchain.staking_events` with a `chain` discriminator column — pick the cleaner of the two given B-086's pending generalization work). Capture per-epoch validator state (`validator_address`, `voting_power`, `stake_amount`, `commission_rate`, `apy`) and per-epoch staking flow (delta vs prior epoch).
  - New collector module `src/genkei/ingest/sui_staking.py` following the B-082 shape: `PoolConfig`-style parameterization for future multi-chain reuse, soft per-epoch failure, incremental + `--backfill` modes.
  - `genkei watchlist health` surfaces the new source with the same loud OK / STALE / FAIL / MISSING / EMPTY semantics as the other sources.
  - `genkei query` against the new table answers "is total Sui staked SUI growing or shrinking over the last 30 days" without needing custom code.
  - Update the SUI /research decision file's "Backlog implications" note to point at the resolved item.

### B-089 — SUI token unlock / vesting schedule data source
- **Status:** open
- **Priority:** medium
- **Context:** Surfaced by the SUI /research session (2026-05-20). The session noted "no SUI token unlock schedule — Sui had aggressive vesting at launch (3y+ cliff), continued unlocks are a known headwind not visible in the lake." For tactical-sleeve crypto positions on a months horizon, knowing whether a major unlock is imminent is the single most actionable supply-side data point — base-case bear thesis for any post-2023 alt-L1 is "VC unlocks compress the token." Lake-gap-free analysis isn't possible until this lands. Investigation tier first because the Blockvision indexing API documented endpoints (`docs.blockvision.org/reference/sui-indexing-api`) do *not* list token-vesting / unlock-schedule endpoints — coverage stops at account holdings + coin market data. Likely sources: (a) on-chain analysis of known vesting contracts via the Sui RPC + Blockvision's Account Activity endpoint, (b) CryptoRank / TokenUnlocks-style external APIs (may be paid), (c) Sui Foundation's published tokenomics schedule (static, but parseable into a per-month unlock schedule).
- **Acceptance criteria:**
  - Survey of available sources (free + paid + on-chain) for SUI vesting / unlock data. First deliverable: a one-page comparison in `docs/sources/sui-unlocks.md` covering coverage (cliff dates, monthly amounts, recipient categorization), staleness, and cost.
  - If a free / cheap source emerges: new schema (`onchain.sui_unlocks` or similar — `unlock_date`, `unlock_amount_sui`, `category` (team / investor / community / etc.), `cumulative_pct_supply`) + collector + normalizer following the standard `meta.ingest_runs` + `meta.raw_blobs` pattern.
  - If only paid sources exist: B-089 stays open at low priority; add the survey doc to the repo so the next time the paid-data question opens, this is in the queue (mirrors the precedent set by B-084 for oracle market share).
  - `genkei query` against the new table answers "how much SUI unlocks in the next 30 / 60 / 90 days, and what % of circulating supply does that represent." That number directly informs position sizing in the crypto-tactical sleeve.
  - Update the SUI /research decision file's "Backlog implications" note to point at the resolved item.

### B-086 — Map the full Chainlink staking surface (cross-source reconciliation)
- **Status:** open
- **Priority:** medium
- **Context:** Surfaced by the first live B-082 backfill (2026-05-17). DefiLlama reports `chainlink-staking` TVL at ~$414.8M, but the v0.2 Community Staking Pool we ingest (`0xBc10f2E862ED4502144c7d632a3459F49DFCDB5e`) only holds ~6.5M LINK ≈ $64M at current price — a **6.5× discrepancy** between sources. Most likely DefiLlama is summing across multiple contracts: the v0.1 community pool (legacy, still holds LINK during unwind), a separate node-operator-only pool, and possibly other staking-related contracts. Until we map and ingest the full set, queries against `onchain.staking_events` give a misleadingly small picture of Chainlink staking demand and the DefiLlama TVL number can't be tied back to on-chain reality.
- **Acceptance criteria:**
  - Identify every contract DefiLlama includes under the `chainlink-staking` slug. Likely starting points: DefiLlama's protocol page source, the Chainlink docs (`docs.chain.link/architecture-overview/staking`), Etherscan "Related" addresses for `0xBc10f2E862ED4502...DFCDB5e`.
  - Add each contract as a new `PoolConfig` entry in `genkei.ingest.onchain_staking.DEFAULT_POOLS` (the schema and collector are already generic across protocols).
  - Run the historical backfill against the new pools — the schema's `(tx_hash, log_index, block_timestamp)` PK keeps re-runs idempotent so the v0.2 pool's existing 18,827 events don't duplicate.
  - Verify: compute net staked LINK per pool (`staked` minus `unstaked`), sum across all `chainlink-*` pools, multiply by the latest LINK price, and compare that value to DefiLlama's `chainlink-staking` TVL. The result should land within ~10%; if the gap stays large after pool-mapping, document why (DefiLlama including delegated-but-not-pool LINK, accounting differences, etc.).
  - Update the cap-and-intent interpretation note in `onchain_staking.py`'s module docstring to reflect the full-surface picture (current text only covers the v0.2 community pool).

## Phase 3 — Custom CLI

The interface the agent (and human user) uses to query the lake.

### B-043 — Implement `genkei news` subcommand
- **Status:** open
- **Priority:** medium
- **Context:** GDELT topic and date filters.
- **Acceptance criteria:**
  - `genkei news --topic "AI capex" --since 2024-01-01`.
  - Cluster output with representative URLs.

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

### B-056 — Experiment: news sentiment vs next-day returns
- **Status:** open
- **Priority:** medium
- **Context:** Classic study using GDELT + prices.
- **Acceptance criteria:**
  - Notebook produces correlation table + a chart.
  - Reproducible from snapshot IDs.

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
