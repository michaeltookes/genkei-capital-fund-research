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

## Phase 3 — Custom CLI

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

### B-138 — Install the nightly backup on the Beelink + confirm first heartbeat
- **Status:** open (manual homelab step; the repo half shipped 2026-07-22, see `docs/resolved.md`)
- **Priority:** **high** — until this lands the lake has no *confirmed* automated backup.
- **Remaining:** the one step the repo half can't do from CI (governance: no autonomous homelab changes). On the Beelink, per `docs/backups.md` → "Install on the Beelink": install the 04:00-UTC cron, add `rclone.conf` for the R2 remote, set `OFFSITE_REMOTE` + `DISCORD_WEBHOOK_URL` on the cron line, run one dump by hand, and confirm both a `meta.backup_runs` row and the off-site copy landed. `backup-staleness-check.yml` pages daily until then — by design, that alert *is* the acceptance gate. Then log the first quarterly restore-drill date (next due ~2026-08-22).

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

## Epic E-002 — Research cockpit (local web app)

A scoping session on **2026-07-20** decided to build a second interface *beyond the existing TUI*: a **local web app on the homelab Beelink** that renders the lake and embeds a **Claude Agent SDK** chat panel — the "Cursor / Codex desktop" analog mapped onto the fund. Locked scope decisions from that session:

1. **Scope = research cockpit only** — renders the lake + converses with the agent. **No brokerage wiring, read-only, free-sources-only.** (Portfolio/execution surfaces explicitly deferred.)
2. **Agent = embedded panel backed by the Claude Agent SDK** — the *same engine* as Claude Code with a GUI front-end, **not** a hand-rolled LLM loop. Preserves the locked "Claude Code is the agent harness" decision.
3. **Form factor = local web app served from the Beelink**, reachable from Mac + phone on the local network (not a native desktop app for v1).
4. **Webull = evaluate-only, parked** — see B-134.

**Keystone:** reuse the existing `genkei` CLI as the single tool surface via an MCP wrapper (B-130, **shipped 2026-07-24** — see `docs/resolved.md` + `docs/mcp.md`) feeding *both* Claude Code and the cockpit — no duplicated data logic. **Phasing:** B-130 (MCP wrapper, ✅ done) → B-131 + B-132 (read API + static cockpit) → B-133 (embedded agent panel + artifact contract, whose tool surface *is* the B-130 server). Each phase delivers standalone value before the next. **Remaining Phase 8 items are user stories capturing the agreed design; nothing else here is scheduled to build yet.**

_B-130 (the `genkei`-as-MCP server keystone) shipped 2026-07-24 — subprocess-with-`--json` adapter, `genkei.mcp` subpackage, `genkei-mcp` entry point, 13 tools; see `docs/mcp.md` and `docs/resolved.md`._

_B-131 (FastAPI read layer over the lake) + B-137 (cockpit deployment & exposure spec) shipped 2026-07-25 — `genkei.api` subpackage, `genkei-api` entry point, read-only endpoints (prices / watchlist / signals / weekly digest / research decisions / lake health), the shared `db.run_readonly` guard, `docs/api-deployment.md` (LAN-only, no cloudflared route, pool ceiling), and the `/health` + B-119 alert workflow. B-132 consumes this API next; see `docs/resolved.md`._

### B-132 — Static cockpit frontend (workspace pane)
- **Status:** open
- **Priority:** low
- **User story:** As Michael, I want a two-pane web cockpit that renders the lake's artifacts — price / TVL / signal charts, the watchlist, the weekly digest, and the decision log — so that I can review the fund visually instead of only through the TUI/CLI.
- **Context:** This is the workspace half of the Cursor analog and delivers value with **zero agent work**. Charts render far better than a terminal; phone-reachability makes the weekly digest glanceable.
- **Acceptance criteria:**
  - Frontend served from the Beelink, reachable from Mac + phone on the local network.
  - Renders: per-asset price series, TVL/signal history, the current weekly signal digest, and the decision log.
  - Reads **exclusively** from the B-131 API; no agent dependency.
  - Frontend-stack decision recorded (recommendation: React + a financial charting lib such as TradingView `lightweight-charts`; alternative: a Python-native UI) with rationale.
  - Ships and is useful before B-133 exists.

### B-133 — Embedded Claude Agent SDK panel + artifact contract
- **Status:** open
- **Priority:** low
- **User story:** As Michael, I want an embedded chat panel that can query the lake and render results *into the workspace* so that asking "how's ZEC's shielded-pool adoption trending?" opens a chart pane — not a wall of text.
- **Context:** The "Cursor moment" is the agent acting on the workspace, not just replying. That requires a small **typed artifact vocabulary** the frontend knows how to render. Must be backed by the Agent SDK (same engine as Claude Code) to stay inside the locked no-hand-rolled-framework decision.
- **Acceptance criteria:**
  - Chat panel backed by the **Claude Agent SDK** (Python backend, streamed to the UI) — not a hand-rolled LLM loop.
  - Agent's tool surface **is the B-130 MCP server** — identical tools to Claude Code.
  - Typed artifact contract: agent emits `chart` / `table` / `report-link` / `decision-draft` artifacts the frontend renders into the workspace pane.
  - **Write posture:** read-only by default; any repo write (draft → `docs/research/decisions/`, commit a report) is gated behind explicit user approval. Record whether writes ship in v1 or defer.
  - Streaming responses; conversation persists per session.

### B-134 — Webull OpenAPI source evaluation (parked)
- **Status:** deferred 2026-07-20 — evaluate-only; reopen on a concrete live-data gap (see trigger).
- **Priority:** deferred
- **User story:** As the fund, I want a recorded evaluation of Webull's OpenAPI so that when a live-data gap appears we can decide quickly whether it's worth opening the "private-data story."
- **Context:** Surfaced during the E-002 scoping session (Michael uses Webull for individual stocks + select crypto prices). Webull OpenAPI offers **Trading**, **Market Data** (quotes / snapshots / OHLCV bars / screeners / fundamentals across US stocks, ETFs, futures, crypto; 300 req/min), **Broker**, and **Connect** (OAuth) APIs, plus an **official MCP server** (`webull-openapi-mcp`) that plugs into Claude Code, and a Python SDK. Requires a Webull **developer account + App Key/Secret + mobile 2FA**, and possibly a **paid market-data subscription**. **Key finding:** it's a **real-time + real-positions** source, **not a history source** (no documented deep backfill) — it *complements* the free ingesters, doesn't replace them. Because it's account-tied/semi-private, adopting it formally opens the "private-data story" deferred per CLAUDE.md (locked decision D: free/open sources only).
- **Reopen / revisit trigger:** once the cockpit read layer + agent panel (B-131 / B-133) are live **and** a specific live-data gap is identifiable (real-time quotes, or actual positions / P&L, that free sources don't cover).
- **Acceptance criteria (for reopen):**
  - Confirm current pricing / tier for the *specific* data needed, and whether a paid market-data subscription is required.
  - Decide the entry path and record the call: **official MCP server** (fastest, no new ingester — preferred) vs a **`genkei` lake ingester** (data lands in Postgres as system-of-record).
  - If adopted, pair with **B-073** (secrets policy) and **B-075** (license / redistribution audit) — it would be the first private/account-tied source.
