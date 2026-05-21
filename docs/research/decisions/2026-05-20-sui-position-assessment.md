---
date: 2026-05-20
asset: SUI
sleeve: crypto-tactical
horizon: months
confidence: medium
status: pending
trigger_reassessment: "SUI lags SOL by another 15-25pp over 3 months OR Sui chain TVL breaks below $500M OR Sui chain TVL recovers above $800M OR SUIG insider open-market buy cluster ≥2 reporters at sub-$2 within 6 months"
related:
  - decision: 2026-05-17-link-position-assessment
  - data: coingecko.market_data
  - data: defillama.chain_tvl
  - data: defillama.stablecoins
  - data: sec.form4_transactions
---

# SUI — crypto-tactical position assessment

## Frame

SUI is the primary-tier crypto in the **tactical** sleeve (per `config/watchlists.yml`; the only primary-tier asset *not* in crypto-core). Question: does SUI warrant continued exposure at $1.05 after a -73% YoY drawdown, and if so, is this a capitulation-bottom add opportunity, a hold, or a trim within the tactical sleeve? Horizon: **months** (tactical sleeve is turnover-eligible by definition). What would change my mind: signs that SUI is mean-reverting against SOL (its closest peer in the alt-L1 cohort), OR insiders at SUIG (the public-market Sui treasury vehicle in the equity-core watchlist) stepping in on the capitulation lows. The lake doesn't surface Sui-native protocol TVL (none of the Sui ecosystem protocols are in `defillama.protocol_tvl` — they're outside the 8-slug watchlist B-081 backfilled) or SUI token unlock schedules, which limits per-protocol fundamental drill-down. Flagging as a gap, not a blocker.

## Macro context

Macro pulled today (`genkei macro --series ...`); FRED collector failed in the last 24h (per `genkei watchlist health`) so values are 3-13 days stale, usable for directional regime call.

- `genkei macro --series DGS10` → 4.47% (2026-05-13). Mid-range, no rate shock pricing in.
- `genkei macro --series DTWEXBGS` → 118.04 (2026-05-07), trending down from 118.83 a week prior. **USD softening — crypto tailwind.**
- `genkei macro --series BAMLH0A0HYM2` → 2.76% (2026-05-13). Historically tight, credit risk-on.
- `genkei macro --series VIXCLS` → 17.26 (2026-05-13). Benign vol regime.

**Macro regime call: constructive risk-on for crypto, unchanged from the 2026-05-17 LINK session (FRED hasn't refreshed since).** Tactical-sleeve positions are *more* macro-sensitive than core on a months horizon — but with USD softening + HY tight + VIX benign, there's no macro reason to be defensive. Like LINK, any SUI underperformance has to be explained by idiosyncratic / sector factors, not macro.

## Fundamentals

**SUI price + market cap anchors** (`genkei query` against `coingecko.market_data`):

| date | SUI price | SUI mcap | SUI return vs 1y |
|---|---|---|---|
| 2025-05-20 (1y ago) | $3.86 | $12.86B | base |
| 2025-11-21 (6m ago) | $1.39 | $5.12B | -64.0% |
| 2026-02-19 (3m ago) | $0.93 | $3.57B | -76.0% |
| 2026-04-20 (1m ago) | $0.94 | $3.73B | -75.6% |
| 2026-05-13 (1w ago) | $1.21 | $4.84B | -68.7% |
| 2026-05-20 (today) | **$1.05** | **$4.19B** | **-72.8%** |

**Sui chain TVL anchors** (`genkei query` against `defillama.chain_tvl`):

| date | Sui TVL | vs 1y ago |
|---|---|---|
| 2025-05-20 | $2.06B | base |
| 2025-11-21 | $957M | -53.6% |
| 2026-02-19 | $566M | -72.6% |
| 2026-04-20 | $586M | -71.6% |
| 2026-05-19 (yesterday — latest) | **$577M** | **-72.0%** |

**Peer comparison — alt-L1 cohort 1y returns:**

| asset | 1y price return | 1y chain TVL return |
|---|---|---|
| ETH | ~-11% (per LINK session) | -28.7% ($60.3B → $43.0B) |
| SOL | -49.9% ($168 → $84) | -35.2% ($9.16B → $5.94B) |
| **SUI** | **-72.8%** ($3.86 → $1.05) | **-72.0%** ($2.06B → $577M) |

**Key fundamental observation #1: price and TVL are collapsing in lockstep.** Unlike B-062's `chainlink-requests` divergence (price-up while revenue-down), SUI shows **no divergence** between market valuation and protocol activity. The token is correctly pricing a -72% contraction in DeFi activity on the chain. This means SUI is neither obviously overvalued nor obviously undervalued versus its own fundamentals — the price is *tracking* a real decline.

**Key fundamental observation #2: SUI's drawdown is materially worse than its peers.** SOL fell -50% with TVL -35%; SUI fell -73% with TVL -72%. SUI is **22-37 percentage points worse** than the next-closest comparable alt-L1 on both axes. That's not sector beta — that's idiosyncratic weakness on Sui specifically.

**Key fundamental observation #3: 3-month TVL has flatlined around $560-635M** (3m ago: $566M; today: $577M; recent local high $635M on 2026-05-13). The price has done the same — $0.93 to $1.21 to $1.05 range. **Whether this is a base forming or a death rattle is the central question** of this session.

**Stablecoin supply on Sui** (`defillama.stablecoins`, today only — B-085 sparsity remains unresolved): $590M across 8 assets (USDC $388M dominant, followed by USDSUI $75M, FDUSD $43M, BUCK $26M). **The stablecoin-to-TVL ratio on Sui is roughly 1:1** ($590M stables vs $577M TVL). Compare with Ethereum where the ratio is ~4:1 ($165B stables / $43B TVL). On Sui, stablecoins aren't being deployed into DeFi — capital is sitting in wallets, not productive. Either dry powder waiting for yield (bull interpretation) or weak demand for Sui DeFi (bear interpretation).

**Data gaps (lake-improvement backlog):**
1. No Sui-native protocols in `defillama.protocol_tvl` — Cetus, Suilend, Navi, Aftermath, etc. are outside the 8-slug B-081 watchlist. Can't drill down into which Sui protocols are bottoming vs which are still bleeding.
2. No SUI token unlock schedule — Sui had aggressive vesting at launch (3y+ cliff). Continued unlocks are a known headwind not visible in the lake.
3. No Sui-chain validator / staking flow — equivalent to the LINK B-082 gap, but for Sui's consensus stake.
4. `defillama.stablecoins` historical sparsity (B-085) — can't measure whether Sui dry powder is growing or shrinking.

## Flow & positioning

**SUI itself has no SEC-reporting equity insiders.** The closest proxy is **SUIG (Sui Group Holdings)** — listed in the equity-core watchlist as "Sui equivalent of MSTR's bitcoin-treasury playbook." Querying `sec.form4_transactions` for SUIG via `genkei insiders --ticker SUIG` and `genkei insider-clusters --ticker SUIG`:

**SUIG Form 4 activity since 2025-01-01:**
- **2025-03-12..13** — small open-market buy cluster: director Zipkin and CEO Polinsky, ~34K shares for ~$65K at $1.82-$1.96. Modest dollar value, but it's the *only* recent open-market buy cluster in SUIG and it landed when SUI itself was trading near $3 (after a strong run from $0.50 lows in 2024). Director + CEO open-market buying together is the classic small-cap-conviction shape.
- **2025-07-31** — large AA-coded (compensation / automatic award) grants: CEO Polinsky and CFO Geraci, ~620K each at $5.42-$7.05 (the stock's range at the time). Not signal — these are equity grants, not open-market purchases.
- **2026-01-05** — AA-coded grants to director Quintenz: 207K total at $0.00 cost. Pure compensation.
- **Other 2025 activity** — minor P-coded purchases by Zipkin (~$5K), JD codes (gift/family) by CFO.

**Critical observation: NO open-market insider buys in SUIG since 2025-05.** SUI cratered from $3.86 to $0.93 between mid-2025 and Feb 2026, and the public-market insiders closest to a Sui-treasury thesis did **not** step in to add. They've taken grants (free shares from the company) but didn't open the checkbook. **This is bearish flow** — Buffett's "be greedy when others are fearful" works when *insiders* are getting greedy; absence of open-market insider buying at the capitulation lows is significant.

**On-chain SUI positioning** (validator staking, exchange flows): not surfaced by the lake — same crypto-positioning gap noted in the LINK session.

## Phase A — case for and case against

**Bull case:**

1. **Capitulation-bottom pattern.** -73% YoY drawdown is deep enough that the marginal panic seller is largely done. The 3-month flatline ($566M → $577M TVL) suggests a base is forming, not active distribution. Crypto-tactical bottoming patterns historically present as "TVL stops falling first, then price re-rates."
2. **Macro constructive.** USD softening + HY tight + vol benign = no macro headwind. Tactical sleeve discipline argues for maintaining at least some position in a primary-tier asset.
3. **Relative-pain extreme = relative-rebound candidate.** If alt-L1 rotation happens (e.g. ETF talk on SOL spills to other L1s, or a Move-VM narrative shift), SUI has materially more room to recover than SOL or ETH simply because it fell further. The 22-37pp peer underperformance is asymmetric upside in a rotation scenario.
4. **Stablecoins on Sui at $590M with 1:1 TVL ratio = dry powder.** Capital is sitting on the chain in stables, not deployed. A yield catalyst (new DeFi protocol, new airdrop campaign, new Move-native primitive) could activate that capital into TVL → narrative → price.
5. **SUIG insider 2025-03 buy cluster** validates that someone with skin in a Sui-treasury thesis thought sub-$2 SUIG was attractive — even if that was 14 months ago. The thesis existed in someone's head.

**Bear case:**

1. **Idiosyncratic underperformance vs SOL is the real signal.** Falling 22pp more than SOL in 1y, with TVL falling 37pp more, is not sector beta. Something is specific to Sui — VC unlocks, ecosystem-launch failures, competitive Move-VM pressure from Aptos, validator concentration concerns, or some combination. The lake doesn't surface what it is, but the *fact* of idiosyncratic weakness is the signal.
2. **Price and TVL collapsed together — no setup for snap-back.** B-062's `revenue-divergence` framework would call this "aligned" (both down materially). The price isn't overshooting fundamentals; it's *tracking* them. A capitulation snap-back typically requires price to have overshot the underlying business; SUI's hasn't.
3. **NO SUIG insider buying at the lows.** The single best flow signal available on the equity side of a Sui thesis is absent. Insiders took grants but did not write checks. If the people closest to Sui aren't stepping in at $1.05 (down from $3.86), why should we?
4. **Stablecoin 1:1 ratio = weak DeFi demand, not dry powder.** Reframed: $590M of stables sitting idle for 6+ months with chain TVL stable suggests no one wants to deploy capital into Sui DeFi at any yield. That's not waiting-for-catalyst dry powder; that's structurally weak demand.
5. **Volume down ~65-70% — interest waning.** Daily volume ran $1.4B in May 2025 and is now $400-500M (~64-71% decline, roughly a 3x drop). Declining volume on a declining asset is the standard "no one cares anymore" pattern, which is how alt-L1s fade from primary to legacy.
6. **3-month flat TVL is more "death rattle" than "base formation."** Healthy bottoming patterns on alt-L1s typically show TVL stabilizing while *something* else inflects (developer activity ticking up, a flagship protocol launch, mainnet upgrade). SUI 3m: nothing else is inflecting. Flatline-without-catalyst is a way-station, not a bottom.
7. **Tactical sleeve discipline = turnover-eligible.** Per `CLAUDE.md`, "the `tactical` sleeve... signals can argue for trimming/adding." The point of having SUI in tactical (vs core) is precisely so we *can* trim when the signal is this bad.

## Phase B — counter-thesis

**Strongest case for being wrong (the bull thesis I'm most likely underweighting):** capitulation bottoms in crypto frequently look like exactly this — boring 3-month flatlines that resolve UP, not down, and the catalyst that activates them is rarely visible 30 days in advance. SOL in late 2022 / early 2023 had the same shape (deep underperformance vs peers, flat TVL for months, NO insider equivalent at the time, capitulation-volume) and re-rated 10x in 2023. If you screen out SUI right now you may be selling the bottom of a tactical-sleeve asset that is *supposed* to be turnover-eligible — and that includes turning over INTO the asset when it's been crushed, not just out of it.

**Specific signal that would confirm this counter-thesis:**

1. SUI outperforms SOL by 15pp over 3 months — would mean idiosyncratic decline reversed and SUI starts the rebound earlier than the peer benchmark.
2. Sui chain TVL recovers to >$800M — would mean DeFi capital is re-deploying onto Sui at the same time the flat-line breaks UP.
3. SUIG open-market insider buy cluster (≥2 reporters at sub-$2) — would mean insiders are stepping in at the lows; their absence today is the bear-side smoking gun, presence would flip it.

**Base-rate question:** alt-L1 tokens with -70% drawdowns in benign macro that flatten for 3 months — historically maybe 1-in-3 of these recover meaningfully within 6-12 months. The other 2-in-3 either keep grinding lower (FTM/Avalanche-style fade) or stay flat at the lows for years (Cardano-style purgatory). The base rate doesn't favor adding aggressively; it favors small position retention with clear trigger conditions.

**What a smart fund manager would say:** "You have a tactical sleeve for a reason. The asset is down 73% in a year, 23pp worse than its closest peer. The insiders on the equity vehicle aren't stepping in. The stablecoin ratio says no one's deploying. The TVL flatline has no other inflection signal. You're a year into a turnover-eligible position that hasn't worked. Trim to underweight, keep a starter for capitulation-bottom optionality, and reallocate the freed capital to crypto-tactical assets with better relative strength (PYTH and RENDER are both in the tactical secondary tier — neither has worked either, but at least their drawdowns aren't 73%)."

**The smart-fund-manager argument is stronger than the bull case here.** The bull case rests on "bottoms are invisible" — true, but base rates say most -70% drawdowns are not bottoms, they're way-stations. The bear case has multiple specific signals (peer underperformance, insider absence, stablecoin ratio, declining volume) all pointing the same direction.

## Conclusion

**Recommendation: Trim to underweight within crypto-tactical sleeve.** Do NOT exit entirely (capitulation bottoms do happen and SUI is primary-tier in the tactical watchlist, not secondary). Do NOT add at current levels (every available signal points down or sideways). Within crypto-tactical, prefer maintaining PYTH and RENDER allocations over adding to SUI.

**Sleeve & horizon:** Crypto-tactical, months horizon (turnover-eligible by definition; reassess within 3 months).

**Confidence: medium.** Better data than the LINK session: peer-comparison data is clean and unambiguous, insider data on SUIG is decisive, and the cross-source bear signals (peer underperformance + insider absence + 1:1 stable/TVL + volume decline + flat TVL without inflection) point consistently the same direction. The bull case rests primarily on "bottoms are invisible" and macro neutrality, both of which are true but not specific enough to override five concrete bear signals. Per the methodology's confidence-calibration rule (look at past reflections — both prior decisions are still `pending` so no resolved track record yet), keeping at "medium" rather than escalating to "high" since this is only the 3rd decision and there's no calibration data to support "high" yet.

**Position-sizing implication:** Bring SUI from its current crypto-tactical allocation down to an underweight position (~25-50% of the prior allocation, depending on current size). Keep a residual position for capitulation-bottom optionality — primary-tier assets don't get fully exited on flow signals alone, only on fundamental thesis breaks (e.g. Sui mainnet failure, validator concentration crisis, or competitor displacement). Reallocate freed capital within crypto-tactical (to PYTH / RENDER) or temporarily to crypto-core (BTC / ETH / SOL — SOL specifically is the same alt-L1 trade but working better).

**Key risks (counter-thesis distilled):**

1. **SUI lags SOL by another 15-25pp over 3 months** → confirm idiosyncratic decline isn't done; trigger trim to zero.
2. **Sui chain TVL breaks below $500M** → the 3-month flat-line was a way-station, not a base; trigger trim to zero.
3. **Sui chain TVL recovers above $800M** → DeFi capital re-deploying; trigger re-add.
4. **SUIG insider open-market buy cluster ≥2 reporters at sub-$2 within 6 months** → insiders stepping in at the lows; trigger re-add.

**Trigger conditions for reassessment** (see frontmatter): any of (a) SUI lags SOL by another 15-25pp over 3 months [bearish trim to zero], (b) Sui chain TVL breaks below $500M [bearish trim to zero], (c) Sui chain TVL recovers above $800M [bullish re-add], (d) SUIG insider open-market buy cluster ≥2 reporters at sub-$2 within 6 months [bullish re-add].

**Meta-takeaway (for `/reflect-decisions` in ~6 months):** This is the first decision using the B-062 framework (price-vs-fundamentals divergence), even though SUI presented as "aligned" rather than divergent. The discipline of confirming alignment (rather than assuming a snap-back is coming) is what flipped this from "buy the dip" to "trim the lag." If SUI rebounds materially over the next 6 months despite this call, the lesson is: in crypto-tactical, capitulation depth alone is sometimes enough to override flow-signal absence. If SUI continues to lag or breaks $500M TVL, the lesson is: cross-source bear signals (peer + insider + stablecoin + volume + TVL) compound — when 5 of them point the same way, lean into the call rather than hedge it.

**Backlog implications surfaced by this session** (separate from the decision itself):

1. ~~Sui-native protocols (Cetus, Suilend, Navi, Aftermath) not in `defillama.protocol_tvl` watchlist~~ → **resolved by B-087** (2026-05-20): seven slugs added to the watchlist — navi-lending, suilend, cetus-clmm, scallop-lend, bluefin-spot, bluefin-pro, deepbook-v3. They carry six unique `coingecko_id` values, but `revenue-divergence` activation is still gated by B-091 wiring protocol-token IDs into CoinGecko ingestion.
2. SUI token unlock schedule — no source in the lake. **Filed as B-089** (2026-05-20). Token-unlock-aware analysis would materially improve confidence on whether continued dilution is a known headwind or a surprise risk.
3. Validator / on-chain staking flow for Sui (Move-VM equivalent of the LINK B-082 ingester). **Filed as B-088** (2026-05-20). Same gap as crypto-core, applies to crypto-tactical too.
4. ~~`defillama.stablecoins` historical sparsity~~ → **resolved by B-085** (2026-05-21): the daily ``/stablecoins`` endpoint returns current-state only, so we'd accumulated only the days since the collector started running (~11 days at session time). The per-asset ``/stablecoin/{id}`` endpoint via ``--backfill --endpoint stablecoins`` carries the historical timeseries; one-shot backfill landed multi-year per-(asset, chain) supply rows. Future research sessions can now run ``SELECT SUM(supply_usd) FROM defillama.stablecoins WHERE chain='Sui' AND ts::date IN (latest, latest-30, latest-90, ...)`` and get a real time series.
5. ~~SUI/SOL relative-strength as a tracked metric~~ → **resolved by B-090** (2026-05-21): the math is now a Postgres view (`analytics.crypto_relative_strength`) + a `genkei relative-strength` CLI subcommand. The manual SUI-vs-SOL 365d computation from this session (-22.8pp) is now a one-line CLI call (`genkei relative-strength --ticker SUI --peer SOL --window 365`) — reproduces the exact number against live data.

---

## Outcome (filled in by /reflect-decisions)

(reserved — pending; will resolve at 2026-11-20 or earlier on trigger)
