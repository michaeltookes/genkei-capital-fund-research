# SUI token unlock / vesting schedule (B-089; DeFiLlama since B-145)

**Status:** v2 shipped 2026-09-18 (B-145). Source: **DeFiLlama's open datasets
bucket** — `https://defillama-datasets.llama.fi/emissions/sui`, no auth, no
key; it is the same URL the defillama.com/unlocks frontend fetches. Full
coverage: **all 8 allocation series**, TGE (2023-05-03) through 2030, past +
future batches. This also **closes the B-115 gap** — Series A / Series B /
Early Contributors are covered.

## v2 source contract (current)

- **URL** — `https://defillama-datasets.llama.fi/emissions/sui` (open
  datasets bucket). Note the distinction: `api.llama.fi/emission/*` is
  paid-tier (HTTP 402, unchanged from the Phase 1 survey below); the
  datasets bucket is the free path DeFiLlama's own frontend uses. If the
  bucket ever gates, this collector dies with a loud fetch error — treat
  that as a health-contract change, not a bug.
- **Shape** — `documentedData.data`: one series per allocation
  (`label`, `data: [{timestamp, unlocked}]`) — *cumulative* unlocked
  sampled daily, unix-seconds timestamps, floats present. Series finals
  sum to exactly the 10B max supply; percent-of-supply is derived from
  that sum, not hardcoded.
- **Parse** — day-over-day deltas become batch rows keyed
  `(allocation_name, unlock_date)`; a positive delta is dated at the
  *earlier* sample (validated: TGE cliffs land exactly on 2023-05-03).
  Mid-schedule monthly batches can land ±1 day vs the canonical vesting
  date (daily sampling grid) — immaterial at monthly-batch granularity.
  `vesting_type` is NULL for DeFiLlama rows (cumulative curves don't
  distinguish cliff vs linear on the same date).
- **Taxonomy switchover (2026-09-18)** — DeFiLlama's "Community Reserve"
  (4.972B / 49.72%) is the full Sui reserve bucket, NOT CryptoRank's
  "Community Reserves" sub-bucket (1.065B / 10.6%). The 85 legacy
  CryptoRank rows were deleted at switchover (one-time
  `DELETE FROM onchain.sui_unlocks WHERE source_endpoint =
  'https://cryptorank.io/price/sui/vesting'`) — keeping both taxonomies
  would double-count reserve supply in any SUM over the table.

### Why v1 died

CryptoRank put `/price/sui/vesting` behind a Cloudflare JS challenge
(observed 2026-09: HTTP 403 + "Just a moment..." interstitial; the
scheduled runner 403'd and a local fetch got the challenge HTML with no
`__NEXT_DATA__`). Same wall class as Grayscale/ARKB (B-129). We don't
scrape through bot walls; the DeFiLlama dataset replaced it with strictly
better coverage.

### Acceptance gates (v2)

1. **Freshness** — latest `(sui_unlocks, collect)` run within 36h.
2. **Allocation count** — exactly 8 distinct `allocation_name`s from the
   llama source; more/fewer means upstream re-bucketed and totals need
   re-validation.
3. **Supply closure** — `SUM(DISTINCT allocation_total_tokens)` ≈ 10B
   (the series must still sum to max supply).
4. **No legacy rows** — zero rows with the CryptoRank source_endpoint.

---

# Phase 1 investigation (2026-06-07, historical)

Everything below is the original B-089 survey, kept as the record of why
v1 shipped CryptoRank-partial. **Path 1's conclusion is superseded**: the
*API* is paid, but the datasets *bucket* is open — that nuance was found
during B-145.

**Status (then):** Phase 1 investigation complete (2026-06-07). Outcome: **partial ingester** scoped to the one allocation category with full free coverage (Community Reserves). Six of the eight allocation categories — including the load-bearing VC unlock categories — are effectively paywalled across the surveyed free sources.

**Context:** The 2026-05-20 SUI research session named "no SUI token unlock schedule" as a load-bearing data gap. The bear thesis on SUI is that aggressive VC vesting (Series A + Series B + Early Contributors) compresses the token through scheduled unlocks; absent visibility into the schedule, "is the next dilution event known to the market" is unanswerable. B-089 surveys the available free data sources and ships whatever partial coverage exists.

## Findings

### Path 1 — DefiLlama `/emissions` and `/emission/{slug}`: **PAID** (HTTP 402)

```bash
$ curl https://api.llama.fi/emissions
HTTP 402 — Upgrade to the paid API plan at https://defillama.com/subscription
$ curl https://api.llama.fi/emission/sui
HTTP 402 — Upgrade to the paid API plan at https://defillama.com/subscription
```

DefiLlama gates all `/emissions*` endpoints behind their Pro subscription. Per CLAUDE.md's "free / open sources only" stance, this is a dead end until the paid-data question opens.

### Path 2 — Sui Foundation's official blog: **CLOUDFLARE-WALLED**

`https://blog.sui.io/token-release-schedule/` — HTTP 200 returns a 27 KB Cloudflare challenge page (4 Cloudflare markers in the HTML, zero unique date strings in the rendered content). Even if the challenge were bypassed, the content is prose-format and has no machine-parseable tables. Useless as a primary data source.

### Path 3 — CryptoRank `/sui/vesting`: **PARTIAL COVERAGE** (1 of 8 allocations)

`https://cryptorank.io/price/sui/vesting` — HTTP 200, no Cloudflare, 315 KB Next.js SSR page with `__NEXT_DATA__` JSON embedded. The data layer exposes:

- **Public / un-gated** (`vestingInfo.allocations`):
  - **`Community Reserves`** (10.648% of supply, 1.065B SUI total) — **complete batch schedule**: 85 monthly unlock rows from 2023-05-03 (TGE) through 2030-05-01, linear vesting, monthly cadence. This is the single allocation category we can fully ingest for free.
- **Public / un-gated** (`coin.icoData.allocationChart`): list of 8 allocation names + percent-of-supply, NO per-batch schedule:
  - Allocated After 2030 — 52.172%
  - Community Reserves — 10.648% (this one has batches above)
  - Stake Subsidies — 9.494%
  - Series A — 7.142%
  - Series B — 6.956%
  - Early Contributors — 6.134%
  - Community Access Program — 5.820%
  - Mysten Labs Treasury — 1.635%
- **Paywalled** (`vesting.call_to_sign_up.*` / `vesting.call_to_upgrade.upgrade_overlay.*` intl strings present): the batch schedules for the other 7 allocations — including the load-bearing **Series A** (7.142%), **Series B** (6.956%), and **Early Contributors** (6.134%) categories whose VC-driven unlocks drive the bear thesis.

**Result**: 10.648% of total supply has a usable schedule for free. The remaining 89.352% (including ~20% of "VC-tier" allocations) is gated.

### Path 4 — Dropstab `/coins/sui/vesting`: **NEXT-EVENT ONLY**

`https://dropstab.com/coins/sui/vesting` — HTTP 200, no Cloudflare, 409 KB Next.js SSR. The `__NEXT_DATA__` `unlocks` array carries exactly ONE row (the next upcoming unlock event), not a schedule. All 6 major allocation names appear in the rendered HTML (Community Reserves, Early Contributors, Mysten Labs, Series A, Series B, Stake Subsidies) but the structured schedule data behind them likely loads via XHR after page hydration. Not usable as a free data source for full-schedule ingest.

### Path 5 — Tokenomist.ai (formerly TokenUnlocks): **FRAGILE RSC STREAMS**

`https://token.unlocks.app/sui` 301-redirects to `https://tokenomist.ai/sui` — HTTP 200, 599 KB Next.js App Router page using React Server Component streaming (`self.__next_f.push` chunks, 90 total). 8 chunks contain unlock-related strings (`Series A`, `Series B`, `Mysten Labs`, `vesting`, `batches`). The data IS in the HTML but it's split across RSC streaming fragments with React component refs — parseable in principle but the parser would be tightly coupled to their UI bundle and break on every redeploy. High maintenance burden for partial coverage that may itself be gated post-extraction.

### Path 6 — CoinGecko `/coins/sui`: **NO VESTING FIELDS**

CoinGecko's free coin endpoint returns 30 KB of market / metadata for SUI but exposes zero unlock or vesting fields. They publish `circulating_supply`, `ath`, and similar — not the release schedule.

### Path 7 — MystenLabs GitHub: **NO STRUCTURED TOKENOMICS**

`https://api.github.com/search/repositories?q=org:MystenLabs+token+OR+tokenomics` returns 3 repos (`asset-tokenization`, `pas`, `sui-staker-ui`), none of which publish the SUI release schedule as structured data. Sui Foundation does not maintain a canonical `sui-tokenomics` machine-readable repo.

### Path 8 — On-chain Sui RPC vesting object queries: **HIGH-EFFORT, NOT PURSUED**

In principle, the Mysten Labs Treasury, Series A wallets, and other vesting categories should resolve to specific on-chain object IDs whose stake-locked balances could be queried via the public Sui RPC (`suix_getOwnedObjects`, `sui_getObject`). The discovery problem — *finding* the canonical addresses for each category — is non-trivial: Sui Foundation has not publicly documented per-category addresses, and the candidate addresses identified via community analysis (e.g. Lookonchain) are unverified. Deferred as a v2.1 follow-up if pursued.

## v1 scope — what B-089 ships

**Ingester scope: Community Reserves only** (10.648% of supply). Pulled from CryptoRank's `__NEXT_DATA__` JSON via a Next.js SSR scrape. This is intentionally narrow — caller-side analytics must NOT treat the resulting `onchain.sui_unlocks` table as a complete SUI unlock picture. The largest unlock categories by VC pressure (Series A, Series B, Early Contributors — totaling ~20% of supply) are absent and remain a paid-data gap.

Schema choice: new `onchain.sui_unlocks` table keyed on `(allocation_name, unlock_date)`. Co-located with `onchain.sui_validators` (B-088) under the same `onchain` schema so cross-source queries (e.g. "is the next unlock landing during a net-outflow staking epoch?") don't need cross-schema joins.

## What would unblock full coverage

In priority order:

1. **Sui Foundation publishes a structured release schedule** (JSON / CSV in a repo or stable URL). Lowest cost; highest signal.
2. **Paid-data budget opens** per CLAUDE.md's "Paid APIs deferred until a private-data story exists." DefiLlama Pro at the current per-API tier would close this immediately.
3. **Tokenomist's free tier expands** to cover full SUI schedules without RSC-fragility parsing.
4. **On-chain vesting-object discovery** — community publishes verified canonical addresses for each allocation category. Then a Sui RPC-based collector lands the same shape as the CryptoRank ingester but with full coverage.
