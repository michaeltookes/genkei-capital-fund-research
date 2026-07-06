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

_B-003 / B-004 / B-005 were obsoleted by the DeFiLlama daily-brief retirement (B-025) and removed 2026-07-01 — see `docs/resolved.md`._

## Phase 0 — Foundation: Postgres + project scaffolding

The data lake doesn't exist yet; this phase makes it possible to land a single row.

## Phase 1 — Refactor DeFiLlama onto Postgres

Migrate the existing MVP into the new foundation; it becomes the canonical pattern for every future ingester.

_B-020 (move Bitcoin CEX/custody exclusion keywords to config) was obsoleted 2026-07-01 — its target `scripts/normalize_defillama.py` was deleted in the lake-shaped normalizer rewrite (B-016/B-051) and no such keyword logic exists in the current pipeline. See `docs/resolved.md`._

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


### B-129 — Remaining spot-ETF net-flow issuers (B-113 follow-up)
- **Status:** open
- **Priority:** low
- **Context:** B-113 (resolved 2026-06-30, see `docs/resolved.md`) added Bitwise BITB as the second net-flow issuer alongside BlackRock IBIT/ETHA/ETHB, satisfying the "at least one additional issuer" bar. The remaining major issuers stay behind their access walls (per the `docs/sources/spot-etf-net-flow.md` survey): **ARKB** Cloudflare-walled, **GBTC** rate-limits scripted access, **FBTC** URL not yet identified, plus **Bitwise ETHW** (their ETH ETF — in the watchlist but not yet pinned in `bitwise.PRODUCT_URLS`; needs the same product-page spike BITB got). Each needs its own scrape path or a paid feed; IBIT+BITB already capture the dominant share of spot-BTC-ETF AUM, so this is breadth, not a load-bearing gap.
- **Acceptance criteria:**
  - Per-issuer ingester(s) / extra `PRODUCT_URLS` entries landing rows into the existing `etf.fund_snapshots` table (no schema changes).
  - At least one of FBTC / GBTC / ARKB / ETHW landing daily NAV + shares-outstanding (or shares-outstanding alone, computed against `yahoo.candles` close).
  - Watchlist health monitors each new issuer source; unit tests pin the per-issuer extractor.
- **Out of scope:**
  - 10-Q quarter-end shares-outstanding triangulation (B-114 owns that path).

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

## Phase 4 — Agent layer

Wires the data lake to the on-demand AI researcher.


## Phase 5 — Experiments framework

First-class — the *point* of having the data lake.

_All Phase 5 items shipped 2026-07-01 (B-054 / B-055 / B-063) — see `docs/resolved.md`._

## Phase 6 — Inefficiency-detection signals

Once cross-source data is in, the system starts producing investable signals.

_B-064 follow-ups (wire the remaining signal emitters) shipped — all emitters landed and are chained into the daily workflows; removed 2026-07-01, see `docs/resolved.md`. B-111 (equity relative-strength emitter) also resolved 2026-06-06 and now lives only in `docs/resolved.md`._

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

A full-codebase review (source, tests/CI, agent layer) on 2026-06-12 found the engineering layers in good shape but the research loop operationally unproven and its instructions drifted behind the shipped code. Six items, ordered by leverage. B-117 and B-118 (both resolved 2026-06-12, see `docs/resolved.md`) protected the integrity of the decision/reflection loop before the first real reflection cycle; the rest harden ops and code quality. B-119 (resolved 2026-06-13) closed the observability half of silent-staleness. Spin-offs filed along the way: B-123 (VEEV ingest) and B-124 (yahoo magnitude audit) from the B-118 dry run, and B-125 (ingest retry) from B-119 — all below.

### B-126 — Jupiter (JUP) token-unlock / emissions ingester
- **Status:** deferred 2026-06-22 — stale by events. Reopen only if Jupiter resumes net-new emissions (see reopen criteria).
- **Priority:** deferred (was medium)
- **Context:** JUP landed in the watchlist on 2026-06-17 (crypto-core + a `jupiter` DefiLlama protocol entry), so price, TVL, and fees flow through the existing CoinGecko + DefiLlama ingesters automatically. The gap is **token unlocks**: Jupiter has a material vesting/emissions schedule and unlock events are a known JUP price catalyst, but the repo has no general unlock ingester — only the SUI-specific `ingest/sui_unlocks.py` (CryptoRank scrape, single-allocation coverage). Without unlock data the dilution half of the JUP thesis is blind, and the event-driven edge type (CLAUDE.md lists "token unlocks") can't fire for JUP. This is the second unlock source; per CLAUDE.md clean-code the extract-a-shared-helper trigger is the *third*, but the SUI ingester's silent `except Exception: pass` smell (flagged in B-121) is worth resolving in whatever shape emerges.
- **2026-06-22 DEFERRAL (kickoff on branch `jup-token-unlock` aborted before any build code):** A source-availability + thesis probe invalidated the item's premise on two fronts:
  - **The forward signal is governance-killed.** Jupiter's **"Goes Green" / Net-Zero Emissions** DAO proposal passed (~75%) in late-Feb 2026, cutting projected net-new emissions from ~1.2B JUP to effectively zero: all **Jupuary** allocations cancelled (1B reserve + 200M 2026 + 200M 2027), **Team** + **Mercurial** vesting concluded early (Team's last unlock 2026-01-27; Mercurial took a final accelerated unlock 2026-02-25), and new emissions/airdrops are paused for 2026. Tokenomist now marks JUP's unlock schedule **ended** ("no upcoming unlock events"; ~47% circulating, the remaining ~53% frozen/cancelled rather than scheduled to emit). Corroborated by The Defiant ("Jupiter Cuts Token Emissions to Zero"), CoinDesk, and ainvest. **The recurring forward-unlock catalyst this item was scoped to monitor no longer exists.**
  - **Free forward-schedule data is paywalled** — the same wall that capped B-089/SUI to 1-of-8 allocations. CryptoRank's free Next.js SSR payload for the JUP slug exposes only 1 of ~10 allocations (the historical 10% "Initial Airdrop" TGE batch — none of the cliffs). DefiLlama's emissions API (`api.llama.fi/emissions`, `/emission/jupiter`) now returns **402 Payment Required** (Pro-tier). Implementation note for any reopen: the CryptoRank slug is `jupiter-stattion` (their own typo), not `jupiter`/`jupiter-ag` (those 404).
- **Reopen criteria:** Jupiter resumes net-new emissions (a future Jupuary or a "Goes Green" reversal), OR a paid-data budget opens — DefiLlama Pro's emissions endpoint would close this immediately and would simultaneously unblock B-115 (SUI's 7 paywalled allocations), so the two share a trigger. If reopened, the original spec below applies.
- **Acceptance criteria (preserved for if/when reopened):**
  - A JUP unlock schedule lands in Postgres from a free source (CryptoRank or a Jupiter-published vesting source), with allocation breakdown where available.
  - The collector follows the standard `db.ingest_run(...)` / `db.bulk_upsert(...)` shape with a `--backfill` path; raw payloads go through `db.store_raw_blob(...)`.
  - A CLI surface (e.g. `genkei unlocks --asset JUP`) returns the schedule with `--json`, mirroring the SUI pattern. (Note: no `genkei unlocks` query command exists today — SUI ships only the ingester + watchlist-health wiring — so this would create that surface.)
  - Coverage limits documented in `docs/sources/` (which allocations are visible vs paywalled), matching the SUI-unlocks honesty note.
  - Decide whether to generalize `sui_unlocks.py` into a shared unlock framework now or defer to a third source; record the call in this item.
