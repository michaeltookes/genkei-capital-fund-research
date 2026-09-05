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
- **Status:** open (breadth; blocked on access walls)
- **Priority:** low
- **Context:** BITB (B-113) and **ETHW** (shipped 2026-07-07, see `docs/resolved.md`) added Bitwise as the second net-flow issuer on both the BTC *and* ETH surfaces, meeting the original "at least one additional issuer" bar. The three still-uncovered majors stay behind access walls (per `docs/sources/spot-etf-net-flow.md`): **ARKB** Cloudflare-walled, **GBTC** rate-limits scripted access, **FBTC** URL not yet identified. Each needs its own scrape path or a paid feed; IBIT+BITB+ETHW already capture the dominant share of spot-ETF AUM, so this is pure breadth, not a load-bearing gap. Reopen a concrete build only if one of the three surfaces a clean free path.
- **Acceptance criteria:**
  - A pinned scrape path (or paid feed) for at least one of FBTC / GBTC / ARKB landing daily NAV + shares-outstanding into the existing `etf.fund_snapshots` table (no schema changes).
  - Watchlist health monitors the new issuer source; a unit test pins the per-issuer extractor.
- **Out of scope:**
  - 10-Q quarter-end shares-outstanding triangulation (B-114 owns that path).

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

### B-141 — Market-sentiment layer: surface what we have, ingest what we lack
- **Status:** open
- **Priority:** medium — requested 2026-08-04 (Michael, after the Suilend/Coldcard security-fear week: "do we have any resources that tell us market sentiment?").
- **Context:** The lake already carries three sentiment-adjacent surfaces that have no first-class presentation: (1) **GDELT news tone** — `gdelt.gkg` stores `tone`, `positive_score`, `negative_score`, `polarity` per article with `matched_assets` tagging (live demo 2026-08-04: BTC 7-day avg tone −1.56 across 2,383 articles during the Coldcard hack news cycle — it works); (2) **CFTC COT positioning** (`cftc.cot_reports`) — institutional long/short sentiment, weekly; (3) **flows as revealed sentiment** — stablecoin supply deltas + spot-ETF net flows. What's missing is *crowd* sentiment: no Fear & Greed index, no funding rates, no social volume.
- **Acceptance criteria:**
  - **Typed CLI surface first (highest leverage, zero new ingest):** `genkei sentiment [--asset BTC] [--since …]` rendering per-asset GDELT tone trajectories (7d/30d averages, article counts, tone-vs-price divergence hook) with `--json`; one-line ToolSpec append so the MCP server and read API pick it up (cockpit card candidate).
  - **Fear & Greed ingester:** alternative.me's crypto Fear & Greed index (free API, daily, history to 2018) → new small table + backfill, standard collect/normalize shape. The classic contrarian crowd gauge.
  - **Funding-rates survey (stretch/optional):** identify a free, TOS-clean source for BTC/ETH/SOL perp funding rates (positioning sentiment at higher frequency than COT); build only if a clean source exists — otherwise document the gap alongside B-104's OI blocker.
  - Signal-emitter hook: extreme readings (tone z-score, F&G <20 / >80) emit into `meta.signal_events` with horizon tags so the weekly digest and threshold alerts see them.

The interface the agent (and human user) uses to query the lake.

_B-046 (CLI query caching) shipped 2026-07-17 — see `docs/resolved.md`._

### B-136 — CLI query surface for on-chain staking / SUI validators / SUI unlocks
- **Status:** open
- **Priority:** medium — B-130 (the MCP server) shipped 2026-07-24 and its tool registry derives from CLI subcommands, so these three domains are now the gap: they're the tools the registry will gain in a one-line append each once their subcommands land.
- **Context:** From the 2026-07-22 audit's CLI inventory: three ingested domains have no dedicated query subcommand — `onchain.staking_events`, `onchain.sui_validators`, and `onchain.sui_unlocks` (the last already flagged in B-126's notes: "no `genkei unlocks` query command exists today"). All are reachable via raw `genkei query` SQL, but the cockpit plan (E-002) turns CLI subcommands into the MCP tool surface, so domains without a subcommand become second-class for both the agent and the FastAPI read layer. Every other lake domain has full coverage (31 subcommands, all `--json`).
- **Acceptance criteria:**
  - `genkei unlocks --asset SUI` (schedule + realized batches) and a staking surface (e.g. `genkei staking [--validators]`) with `--json`, following the shared `_helpers` conventions.
  - Human + JSON formatters in the same module per the standard subcommand shape.
  - Unit tests per subcommand; add each as a one-line `ToolSpec` to `genkei.mcp.registry` so the shipped B-130 tool surface picks them up.

## Phase 4 — Agent layer

Wires the data lake to the on-demand AI researcher.


## Phase 5 — Experiments framework

First-class — the *point* of having the data lake.

_All Phase 5 items shipped 2026-07-01 (B-054 / B-055 / B-063) — see `docs/resolved.md`._

## Phase 6 — Inefficiency-detection signals

Once cross-source data is in, the system starts producing investable signals.

_B-064 follow-ups (wire the remaining signal emitters) shipped — all emitters landed and are chained into the daily workflows; removed 2026-07-01, see `docs/resolved.md`. B-111 (equity relative-strength emitter) also resolved 2026-06-06 and now lives only in `docs/resolved.md`. B-068 (threshold alert engine) shipped 2026-07-16 — see `docs/resolved.md`._

### B-139 — Chainlink staking-capacity monitor (`staking.chain.link` opening alert)
- **Status:** open — **blocked on B-143 (ntfy channel), no longer on the cockpit** (2026-08-14 pivot). The no-Discord constraint stands; delivery is now an ntfy push notification. Poll/threshold design below is unchanged; buildable immediately after B-143 lands (or in the same branch).
- **Priority:** medium
- **Context:** Michael holds LINK (crypto core) and wants into the v0.2 community staking pool (~4.32% effective). The pool (40,875,000 LINK community allotment) has been full since Early Access (Dec 2023); under General Access anyone can stake **whenever an existing staker withdraws and space frees up** — no allowlist, just speed. Openings are therefore an on-chain observable: the v0.2 community pool contract exposes `getMaxPoolSize()` / `getTotalPrincipal()`, and available capacity = max − principal. Requested 2026-07-25 (session that logged the token-necessity research question — the staking what-if is the reason LINK stays a conviction hold).
- **Acceptance criteria:**
  - GH Actions cron polls available capacity via `eth_call` against a public Ethereum RPC (no API key; builder verifies the current community-pool contract address + selectors from docs.chain.link — v0.2 community pool is believed to be `0xBc10f2E862ED4502144c7d632a3459F49DFCDB5e`).
  - Alert via the ntfy channel (B-143) when available capacity ≥ a configurable threshold (default ≥ 100 LINK — dust-sized churn isn't actionable), including the amount free and a `staking.chain.link` link. Re-alert only on threshold re-cross, not every poll.
  - Poll cadence as tight as GH Actions scheduling honestly allows (~15 min; document that cron drift makes minutes-level sniping unreliable — the realistic catch is larger withdrawals and, above all, a **v0.3 / pool-expansion event**, which this monitor catches at announcement-scale capacity).
  - Secondary tripwire: add `chainlink staking v0.3` / pool-expansion terms to LINK's GDELT `gdelt_terms` watch so an expansion announcement surfaces in news flow even before capacity moves on-chain; keep `Chainlink` in that list too, because explicit `gdelt_terms` replace the implicit name match.

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

### B-140 — `meta.raw_blobs` growth management (32 GB and compounding)
- **Status:** open
- **Priority:** medium-high — discovered 2026-08-03 during the B-138 install: the database is **34 GB, of which 32 GB is `meta.raw_blobs`** (up from 1.5 GB total at the 2026-05-22 drill). Every daily ingest appends raw vendor JSON forever; GDELT/SEC/BEA payloads dominate. At this growth rate the Beelink disk (28 GB free) becomes the binding constraint on both the lake and its backups within months.
- **Context:** raw blobs are the *re-fetchable* tier (per `docs/backups.md`), kept for provenance + re-normalization. Nobody queries them hot. Their size now (a) inflates every nightly dump, (b) forced the backup preflight override (`DISK_FACTOR_PCT`, this branch), and (c) will eventually fill the disk. Candidate fixes, not mutually exclusive:
  - **At-rest compression:** convert `meta.raw_blobs` to a hypertable partitioned on `fetched_at` + enable TimescaleDB native compression on chunks older than ~7 days (JSONB → columnar compressed typically 5-10×). Least invasive; keeps SQL access.
  - **Blob tiering:** archive blobs older than N months to R2 (same bucket family as backups) and delete locally, keeping a manifest table for provenance. Changes the "restore = one dump" story — restore would need dump + archive.
  - **Per-source retention policy:** some sources (GDELT gkg raw XML/JSON) are re-fetchable from the vendor's own archive indefinitely — their local raw copies could have a hard TTL.
- **Acceptance criteria:**
  - Decision recorded (compression vs tiering vs TTL, or combination) with measured before/after sizes.
  - `meta.raw_blobs` on-disk size reduced to a level where 7-day dump retention fits the Beelink disk comfortably, OR the backup posture doc updated to a deliberately different retention shape.
  - Provenance guarantees in CLAUDE.md still hold (audit trio intact) — whatever moves off-box must remain reachable.

## Epic E-001 — 2026-06-12 codebase-review findings

A full-codebase review (source, tests/CI, agent layer) on 2026-06-12 found the engineering layers in good shape but the research loop operationally unproven and its instructions drifted behind the shipped code. Six items, ordered by leverage. B-117 and B-118 (both resolved 2026-06-12, see `docs/resolved.md`) protected the integrity of the decision/reflection loop before the first real reflection cycle; the rest harden ops and code quality. B-119 (resolved 2026-06-13) closed the observability half of silent-staleness. Spin-offs filed along the way: B-123 (VEEV ingest) from the B-118 dry run, and B-125 (ingest retry) from B-119 — all below (B-124 resolved, see `docs/resolved.md`).

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

## Epic E-002 — Interface layer (pivoted 2026-08-14: bring-your-own-interface via MCP)

**Pivot (2026-08-14, Michael's call):** instead of building and maintaining a custom web cockpit, package Genkei so the user **picks their own interface** — Claude Code, Claude Desktop, Cursor, Codex, or any MCP-speaking client — and Genkei plugs in. This is the purer expression of the founding CLAUDE.md stance ("the data lake is the asset; briefs and reports are emergent UIs") and of D-017 ("Claude Code is the agent harness"): the mature agent interfaces already exist; Genkei's job is to be excellently *installable* into them, not to compete with them. The original 2026-07-20 cockpit scoping (local web app + embedded Agent SDK panel) is preserved in this section's history and in `docs/resolved.md`; nothing built for it is wasted — the B-130 MCP server is now the *centerpiece* rather than a stepping stone, and the B-131 read API remains the generic HTTP surface any future client (including a revived dashboard or a chat bot) can consume.

Post-pivot interface path: **B-143** (ntfy push-alert channel, carrying the alerting intent forward as native notifications and unblocking B-139) is the remaining open piece — **B-142** (installable MCP packaging + per-client setup) shipped 2026-08-24 (see `docs/resolved.md`). **B-132** (static frontend) is parked, not deleted — the API keeps it a cheap option if glanceable dashboards are ever missed. **B-133** (embedded agent panel) is obsoleted — Claude Desktop/Code *is* the agent panel (see `docs/resolved.md`). Chat-platform bridges (Discord/Slack bots) are deliberately **not** scheduled: they require hosted bot services + per-message API costs, and there's no second user who lives there yet; revisit only on concrete demand.

_B-130 (the `genkei`-as-MCP server keystone) shipped 2026-07-24 — subprocess-with-`--json` adapter, `genkei.mcp` subpackage, `genkei-mcp` entry point, 13 tools; see `docs/mcp.md` and `docs/resolved.md`. Packaged, documented per-client, and registered end-to-end by **B-142** (shipped 2026-08-24) — the previously-pending Python ≥3.10 registration is closed._

_B-131 (FastAPI read layer over the lake) + B-137 (cockpit deployment & exposure spec) shipped 2026-07-25 — `genkei.api` subpackage, `genkei-api` entry point, read-only endpoints (prices / watchlist / signals / weekly digest / research decisions / lake health), the shared `db.run_readonly` guard, `docs/api-deployment.md` (LAN-only, no cloudflared route, pool ceiling), and the `/health` + B-119 alert workflow. After the E-002 pivot, this API remains the generic LAN-only HTTP surface for a possible revived dashboard or other non-MCP client; B-132 is parked, not next. See `docs/resolved.md`._

### B-132 — Static cockpit frontend (workspace pane)
- **Status:** **parked** (2026-08-14 pivot — bring-your-own-interface via MCP; B-133 is obsolete; see epic header). Not deleted: the B-131 API keeps a thin dashboard a cheap, always-available option. Revisit only as a standalone visual surface if glanceable/ambient visuals are genuinely missed after living with the MCP + ntfy setup for a while.
- **Priority:** parked
- **User story:** As Michael, I want a two-pane web cockpit that renders the lake's artifacts — price / TVL / signal charts, the watchlist, the weekly digest, and the decision log — so that I can review the fund visually instead of only through the TUI/CLI.
- **Context:** This is the workspace half of the Cursor analog and delivers value with **zero agent work**. Charts render far better than a terminal; phone-reachability makes the weekly digest glanceable.
- **Acceptance criteria:**
  - Frontend served from the Beelink, reachable from Mac + phone on the local network.
  - Renders: per-asset price series, TVL/signal history, the current weekly signal digest, and the decision log.
  - Reads **exclusively** from the B-131 API; no agent dependency.
  - Frontend-stack decision recorded (recommendation: React + a financial charting lib such as TradingView `lightweight-charts`; alternative: a Python-native UI) with rationale.
  - If revived, ships only if it is useful as a standalone visual review surface without an agent panel.

### B-143 — ntfy push-alert channel (the alerts panel, reborn as notifications)
- **Status:** open
- **Priority:** medium-high — unblocks B-139 and gives every logged trigger tripwire (PYTH checkpoint, HYPE fees/share, RENDER/SUSHI/SUI reopens) a delivery path that isn't Discord and doesn't require a custom UI.
- **Context:** Michael rejected Discord as an alert surface (2026-08-04); the cockpit's alerts panel was the planned alternative and is now parked. [ntfy](https://ntfy.sh) fits the homelab ethos: a topic is a URL, publishing is one `curl` from any GH Action or Beelink cron, and delivery is a native push notification in the app/browser of Michael's choice. Self-hosted ntfy on the Beelink is acceptable for Beelink-originated alerts, but it does **not** cover runner-down / homelab-down alerts: `ingest-heartbeat.yml` and `workflow-failure-alert.yml` run on GitHub-hosted runners, cannot reach the homelab, and must survive the Beelink being offline. Those paths require an access-controlled hosted ntfy.sh topic or an authenticated public relay/exposure design before ntfy can be treated as full B-119 coverage.
- **Field evidence (2026-09-05, the Aug-Sept ingest outage):** the B-119 heartbeat *fired correctly the whole time* — issue #206 opened Jul 30 and accumulated 35 daily "still stale" comments through a 4-week total outage, and the Discord step ran green into the rejected webhook — yet nothing reached Michael. Two delivery defects to fix here, not in B-119: (1) no channel Michael actually watches (the ntfy work above); (2) **no escalation semantics** — #206 was opened for marginal noise (3 workflows ~49h late), so the real 22-workflow blackout a week later arrived as buried comments on an already-ignored thread instead of a new, louder event.
- **Acceptance criteria:**
  - Escalation semantics in `ingest-heartbeat.yml`: a severity jump (e.g. stale count crossing ~25% of watched workflows, or any staleness doubling past 96h) must produce a NEW alert event (fresh issue title-suffixed with severity, or a distinct ntfy priority) rather than another comment on the standing marginal-noise issue.
  - Decision recorded by publisher class: Beelink-local alerts may use self-hosted or hosted ntfy; GitHub-hosted runner-down alerts (`ingest-heartbeat.yml`, `workflow-failure-alert.yml`) require an access-controlled hosted ntfy.sh topic or an authenticated public relay/exposure design. Hosted ntfy credentials live in GitHub secrets, and validation must prove unauthenticated publish/subscribe attempts are rejected. Beelink self-hosting alone must not be recorded as covering the full B-119 path.
  - A shared notify helper (mirroring `.github/actions/discord-notify`'s shape) so workflows can `curl` the appropriate topic; wire it as an *additional* channel into the B-119 alert path where the publisher can reach the topic (GitHub issues remain the durable record).
  - At least one real Beelink-local alert flowing end-to-end: the backup staleness check or the threshold-alert engine (B-068) publishing to ntfy, received on Michael's phone. Before replacing Discord for runner-down coverage, also prove one GitHub-hosted heartbeat/failure alert reaches the access-controlled hosted/relay topic.
  - `docs/alerting.md` (or the existing alerting docs) updated with the channel map: what pages where, which publishers can reach it, and which outages it survives.

### B-144 — Port genkei-mcp to the MCP SDK 2.x handler API
- **Status:** open
- **Priority:** low — the `mcp>=1.0,<2` pin keeps the 1.x line working today; this is debt paydown so the pin doesn't rot.
- **Context:** During B-142 validation (2026-08-24), the unpinned `[mcp]` extra resolved to MCP SDK 2.1.0, whose low-level `mcp.server.Server` dropped the `@server.list_tools()` / `@server.call_tool()` decorators the B-130 server is built on — the server crashed at startup (`AttributeError: 'Server' object has no attribute 'list_tools'`). B-142 capped the extra at `mcp>=1.0,<2` (resolves 1.29.1) as the correct v1 fix.
- **Acceptance criteria:**
  - Server ported to the 2.x handler/registration API (or `FastMCP` equivalent), all 13 tools listed and callable.
  - The `<2` pin lifted; `[mcp]` extra resolves a current 2.x SDK and the blessed `uvx` install path from `docs/mcp.md` still starts the server clean.
  - Existing MCP tests (and the end-to-end stdio client check from B-142) pass against the ported server.

### B-134 — Webull OpenAPI source evaluation (parked)
- **Status:** deferred 2026-07-20 — evaluate-only; reopen on a concrete live-data gap (see trigger).
- **Priority:** deferred
- **User story:** As the fund, I want a recorded evaluation of Webull's OpenAPI so that when a live-data gap appears we can decide quickly whether it's worth opening the "private-data story."
- **Context:** Surfaced during the E-002 scoping session (Michael uses Webull for individual stocks + select crypto prices). Webull OpenAPI offers **Trading**, **Market Data** (quotes / snapshots / OHLCV bars / screeners / fundamentals across US stocks, ETFs, futures, crypto; 300 req/min), **Broker**, and **Connect** (OAuth) APIs, plus an **official MCP server** (`webull-openapi-mcp`) that plugs into Claude Code, and a Python SDK. Requires a Webull **developer account + App Key/Secret + mobile 2FA**, and possibly a **paid market-data subscription**. **Key finding:** it's a **real-time + real-positions** source, **not a history source** (no documented deep backfill) — it *complements* the free ingesters, doesn't replace them. Because it's account-tied/semi-private, adopting it formally opens the "private-data story" deferred per CLAUDE.md (locked decision D: free/open sources only).
- **Reopen / revisit trigger:** once the MCP interface layer (B-142) is in daily use **and** a specific live-data gap is identifiable (real-time quotes, or actual positions / P&L, that free sources don't cover).
- **Acceptance criteria (for reopen):**
  - Confirm current pricing / tier for the *specific* data needed, and whether a paid market-data subscription is required.
  - Decide the entry path and record the call: **official MCP server** (fastest, no new ingester — preferred) vs a **`genkei` lake ingester** (data lands in Postgres as system-of-record).
  - If adopted, pair with **B-073** (secrets policy) and **B-075** (license / redistribution audit) — it would be the first private/account-tied source.
