---
date: 2026-06-03
asset: "crypto-core: BTC vs ETH vs SOL"
sleeve: crypto-core
horizon: years
confidence: medium
status: pending
trigger_reassessment: "ETH chain TVL breaks below $35B (deeper bear) OR ETH chain TVL recovers above $50B (bull confirmation) OR SOL chain stablecoin supply drops below $14B (SOL flow reversal) OR SOL chain stablecoin supply holds above $17B (SOL bull confirmation) OR BTC breaks below $62k (cycle capitulation) OR engine fires any crypto_tvl_stress_combo or leader_crossing on any of the three"
related:
  - decision: 2026-06-02-ethereum-position-assessment
  - decision: 2026-06-02-solana-position-assessment
  - decision: 2026-06-02-sui-rotation-into-eth-sol
  - data: coingecko.market_data
  - data: coinbase.candles
  - data: defillama.chain_tvl
  - data: defillama.stablecoins
  - data: analytics.crypto_relative_strength
  - data: meta.signal_events
---

# Crypto-core comparison — BTC vs ETH vs SOL through the 2026-06-03 dip

## Frame

The user's question: given yesterday's separate ETH and SOL assessments (both landed at "DCA 25-50% of intended target, hold rest for confirmation") and today's further crypto-wide selloff, **which of BTC, ETH, or SOL is the single best buy at current prices?** Sleeve: crypto-core (all three are watchlist core assets per `CLAUDE.md`). Horizon: years. What changes the answer: the data lake should produce a *comparative* ranking, not three parallel DCA recommendations — the user is implicitly asking "if I had one tranche to deploy today, where should it go?" Yesterday's sessions answered ETH and SOL independently; today is a forced comparison with BTC added (BTC was not analyzed yesterday because the user's framing then was "is this generational" on ETH, not "where's the best risk/reward across core").

Crucial data gaps to flag before the analysis: **CFTC COT data (B-031) is MISSING** — the daily cron hasn't fired yet; `cftc.cot_reports` is empty, so the institutional-positioning context that yesterday's sessions repeatedly named as the load-bearing missing input is *still* not in the lake for this session. **Spot ETF activity (B-105)** isn't merged yet; same gap. The institutional-flow cohort I spent the past two days building is functionally absent. This session relies entirely on prices, TVL, stablecoin supply, and rel-strength — the same data set yesterday's sessions had.

## Macro context

Macro regime call identical to yesterday's three sessions — FRED data is the same (`fred.observations` last refreshed 2026-05-13, ~21 days stale; collector is intermittent, fred normalize STALE 441h per `genkei watchlist health`). DGS10 4.47%; DTWEXBGS 118.04 (still softening); VIXCLS 17.26; HY OAS 2.76%; T10Y2Y +0.50%. **Constructive risk-on for crypto, no macro driver for the latest dip.** This is the most important macro framing today: the 2026-06-01 → 2026-06-03 crypto selloff is *crypto-internal* (deleveraging, ETF flow rotation, narrative collapse — pick your story). It is NOT a "the macro broke" event. The implication: drawdowns into benign macro tend to mean-revert faster than drawdowns into deteriorating macro. The base-rate argument applies equally to all three.

## Fundamentals

### Price action through the 3-day dip (`coingecko.market_data`)

| asset | 2026-05-31 | 2026-06-01 | 2026-06-02 | 2026-06-03 (today) | 3-day Δ | from yesterday |
|---|---|---|---|---|---|---|
| BTC | $73,809 | $71,449 | $67,193 | **$66,030** | **−10.5%** | **−1.7%** |
| ETH | $2,019 | $1,977 | $1,906 | **$1,835** | **−9.1%** | **−3.7%** |
| SOL | $82.71 | $80.36 | $76.15 | **$73.17** | **−11.5%** | **−3.9%** |

**Today's "dip" is asymmetric.** BTC took the smallest hit (−1.7% day-over-day, −10.5% over 3 days); ETH and SOL each dropped roughly 4% today on top of the prior 2 days. The selloff is *risk-weighted* — the smaller-cap, higher-beta assets fell harder. Classic "crypto-beta selloff" pattern, not a "BTC is leading down" pattern.

### Drawdown depth from 1y peak

| asset | 1y peak | peak date | today | from peak | from 1y trough |
|---|---|---|---|---|---|
| BTC | $124,773 | 2025-10-06 | $66,030 | **−47.1%** | **+5.0%** above trough |
| ETH | $4,829 | 2025-08-22 | $1,835 | **−62.0%** | **+0.8%** above trough |
| SOL | $247.55 | 2025-09-18 | $73.17 | **−70.4%** | **AT trough** (new 1y low today) |

**This is the single most important observation today.** SOL is making a fresh 1y low *today* ($73.17 = 1y trough). ETH is within 1% of its 1y low. BTC is still 5% above its trough — taking damage but not breaking. The 70% peak-to-trough for SOL is "deepest discount" territory; the 47% for BTC is "moderate sale." Buffett-style "buy quality at fire-sale" — the question is whether to prioritize *quality* (BTC has the best pedigree) or *discount depth* (SOL has the deepest sale).

### Chain TVL through the dip (`defillama.chain_tvl`)

| chain | 2026-05-25 | 2026-06-02 (yesterday) | 8-day Δ |
|---|---|---|---|
| Ethereum | $43.02B | $39.75B | **−7.6%** |
| Solana | $5.45B | $5.11B | **−6.2%** |

**ETH TVL is bleeding faster than SOL TVL through the dip.** Same direction as yesterday's ETH session highlighted (TVL dropping faster than price, bearish divergence). SOL's TVL is also falling but at a *slower* pace, despite SOL price falling harder than ETH price. **For SOL, this means price is overshooting fundamentals DOWN** (TVL holds up better than price drops). **For ETH, this confirms yesterday's bear case** (fundamentals catching DOWN to price, not the reverse).

### Stablecoin supply — the standout signal today (`defillama.stablecoins`)

| chain | 2026-05-25 | 2026-05-31 | 2026-06-03 (today) | 8-day Δ | through-dip direction |
|---|---|---|---|---|---|
| Ethereum | $163.34B | $161.86B | **$160.09B** | **−1.9% ($3.2B left)** | **OUT** |
| Solana | $15.14B | $15.15B | **$15.63B** | **+3.3% ($0.5B added)** | **IN** |

**This is the new data point today that wasn't visible in yesterday's sessions** (yesterday only had single snapshots). The 8-day trajectory shows stablecoin capital *rotating out of Ethereum and into Solana* through the very dip we're analyzing. This is the strongest single directional flow signal in the lake right now:

- ETH ecosystem is losing capital — not just on-chain TVL contraction, but stables (the "dry powder" yesterday's ETH session leaned on as the bull anchor) are actually *leaving*. The 4:1 stablecoin-to-TVL ratio from yesterday is mechanically weakening: stables down 1.9%, TVL down 7.6% on a base of $40B — both worsening at the same time.
- SOL ecosystem is *gaining* capital. SOL price down 11.5% but stables up 3.3% — pure dry-powder accumulation during the price drop. The exact pattern that supports an aggressive bottom-fishing thesis: holders converting to stables on-chain rather than off-ramping is the institutional/serious-allocator pattern.
- The two directions are *opposite* despite both chains seeing similar headline price drops. This is genuinely new information — the cross-chain capital rotation visible only in the latest 8 days.

### Relative strength vs BTC (`analytics.crypto_relative_strength` — live as of 2026-06-03)

| window | ETH/BTC | SOL/BTC |
|---|---|---|
| 7d | **+1.2pp** | -0.5pp |
| 30d | -5.1pp | **+3.2pp** |
| 90d | -4.5pp | -10.5pp |
| 180d | -13.1pp | -19.0pp |
| 365d | **+8.0pp** | -15.7pp |

- **Short-term (7d / 30d): SOL turning, ETH stalling.** SOL +3.2pp vs BTC over 30d, ETH only -5.1pp. The most recent 30-day window favors SOL.
- **Medium-term (90d / 180d): both negative, SOL worse.** SOL's 90d / 180d underperformance is the worst (-10.5pp / -19.0pp) — the SUI session and the 2026-06-02 SOL session both noted this: SOL went through a much worse 6-month than ETH.
- **Long-term (365d): ETH ahead, SOL behind.** ETH +8.0pp YoY vs BTC (validating the franchise as a relative-strength leader on the longest window), SOL -15.7pp (worst of the three).

Neither ETH nor SOL has crossed the engine's ±15pp threshold on the 30-day window. Engine fires zero events on any of the three over the last 20+ days (verified: `genkei query "SELECT ... FROM meta.signal_events WHERE asset IN ('ETH','BTC','SOL','ethereum','solana','bitcoin') AND ts >= '2026-05-15'"` → 0 rows). **Engine remains silent** — same situation as yesterday.

## Flow & positioning

The institutional-positioning lens is structurally weaker today than the spec wanted because the institutional-flow ingesters I spent the past two days building (B-031 CFTC COT and B-105 spot ETF) aren't yet flowing. Honest accounting:

- **CFTC COT (B-031)**: collector + schema + watchlist + CLI all shipped 2026-06-02, but the GitHub Actions cron hasn't fired the first run (would have triggered yesterday 22:00 UTC; status MISSING in watchlist health). The leveraged-funds-positioning lens — the load-bearing missing input named yesterday — is therefore still missing today.
- **Spot ETF activity (B-105)**: collector + CLI + watchlist on a feature branch (`spot-etf-flow`) not yet merged into main. Yahoo would serve the data once running, but no rows exist yet in `yahoo.candles` for IBIT / FBTC / ETHA / etc.
- **On-chain whale flow**: still not implemented (no Etherscan whale-flow ingester). The "OG sellers" question yesterday's ETH session raised is structurally invisible to the lake.

What the lake DOES see today:

- **Stablecoin supply flow direction** (above) is the clearest institutional-flow proxy available. Stablecoin minting/burning is largely institutional / market-maker driven. The ETH-stables-leaving / SOL-stables-arriving signal IS visible and IS load-bearing for today's comparative read.
- **Trading volume:** BTC daily volume jumped from ~$17B (5/30) to ~$50B (today); ETH from ~$7B to ~$24B; SOL from ~$1.4B to ~$3.9B. **All three saw 3-4x volume surges during the dip — classic capitulation-day volume signature.** Heavy volume on a price drop is the textbook "real selling" pattern. Doesn't tell us *who* is selling, just that it's not a thin-market event.
- **Engine silence** is still itself a signal. Yesterday's ETH session noted "capitulation often presents as nothing flashing rather than the engine screaming bottom." Today the engine is silent across all three — same framing applies, slightly intensified.

## Phase A — case for and case against

### BTC

**Bull case:**
1. **Quality at moderate discount.** Best pedigree, deepest liquidity, no DeFi-business contraction risk (BTC isn't a DeFi asset). −47% from peak is the kind of drawdown depth that historically marks late-bear / early-recovery zones for BTC specifically.
2. **5% above 1y trough = lowest knife-catching risk** of the three. ETH/SOL are at or within 1% of their lows; BTC has more "buffer" before claiming bottom.
3. **In every prior crypto-cycle bear, BTC bottoms first and re-establishes leadership before alts.** If you believe we're near the cycle low, BTC accumulation is the lowest-risk way to participate in the recovery.
4. **No on-chain DeFi business to lose.** ETH's bear case (TVL bleeding, DeFi shrinking) doesn't apply to BTC at all. BTC's value proposition isn't "DeFi-on-Bitcoin works" — it's "digital gold." That thesis is intact regardless of the past 3 days.
5. **Today's selloff hit BTC LEAST.** A −1.7% day in a crypto-wide selloff is BTC behaving as the relative-strength anchor — what you want to see if BTC is going to lead a recovery.

**Bear case:**
1. **Smallest discount.** −47% peak vs −62% / −70% for ETH / SOL. If you're hunting asymmetric returns from depth, BTC is the *least* compelling on that lens.
2. **365d return is the WORST of the three on raw price** (−37.6% trailing year). BTC hasn't outperformed ETH or SOL on a year-over-year basis — the 365d rel-strength shows ETH +8pp ahead of BTC, SOL −15.7pp behind BTC. BTC was the laggard for the full year.
3. **BTC capitulates LAST historically.** In every prior cycle, BTC's biggest single-month drawdown comes after the alts have already bottomed. If we're not at the cycle low yet, BTC has the most downside remaining (could go to $50k or $46k in a market-wide capitulation).
4. **Marginal buyer thesis is weaker.** "I'm buying because it's cheap" is harder to make at −47% than at −70%. The price-discovery floor is more ambiguous.

### ETH

**Bull case (mostly carried forward from yesterday's session):**
1. **Macro constructive + 365d rel-strength leader vs BTC.** ETH beat BTC by +8pp YoY — the franchise is intact on the longest window. The drawdown is sector-wide, not asset-specific.
2. **Drawdown depth (−62% from peak)** is enough that the marginal panic-seller is largely flushed. Buffett-style "buy quality at fire-sale" applies; ETH IS quality at this point.
3. **Within 1% of 1y trough — base may be forming.** If $1,820 is the floor, $1,835 is an acceptable entry. The 7d rel-strength (+1.2pp vs BTC) is consistent with a base attempting to form.
4. **Engine silent, not bearish.** No bearish stack fires; the engine isn't confirming a "more pain coming" thesis.

**Bear case (STRENGTHENED from yesterday):**
1. **Stablecoin supply is LEAVING.** This is new vs yesterday. ETH stables down $3.2B in 8 days. The "dry powder waiting to deploy" thesis that yesterday's session named as the strongest bull anchor is *mechanically weakening in real time*. If stables continue to leave for another week, the dry-powder argument fully collapses.
2. **TVL continues to bleed faster than price.** Yesterday's load-bearing bear signal is still operative and arguably accelerating (−7.6% TVL vs −9.1% price over 8 days vs 3 days respectively — TVL contraction is matching pace).
3. **Engine has emitted ZERO leader_crossing events on ETH for 10+ months.** No confirmation that ETH-vs-BTC momentum has flipped. The 7d +1.2pp is too short to count.
4. **The "another dip" was DEEPER on ETH than on BTC.** ETH fell harder over the 3-day period (-9.1% vs BTC -10.5% is actually close; but day-over-day today, ETH -3.7% vs BTC -1.7% — ETH led the latest leg DOWN, not up).
5. **SOL is gaining what ETH is losing.** The stablecoin flow direction is a *zero-sum capital rotation* signal: $0.5B that landed on SOL didn't materialize out of thin air. Some of it may have rotated from ETH.

### SOL

**Bull case (STRENGTHENED from yesterday):**
1. **Deepest discount (−70% from peak)** of the three. "Generational buy" framing has more data backing it for SOL than for ETH — the depth genuinely matches the kind of drawdown that has historically marked bottoms.
2. **AT 1y trough today.** Best literal "I'm buying at the bottom" entry point of the three. Even if not the absolute floor, buying within 1% of the floor across a wide entry-window is a defensible Buffett-style move.
3. **Stablecoin supply RISING during the dip.** This is the cleanest single positive signal in the whole comparison. $0.5B of NEW stables landed on Solana while SOL price fell 11.5% over 8 days. Capital is *accumulating* on SOL during weakness — the precise pattern of conviction buying that signals real bottom-formation.
4. **TVL holding up relatively well.** ETH TVL fell faster (-7.6%) than SOL TVL (-6.2%) despite SOL price falling harder. **SOL fundamentals are NOT catching down to price** — opposite of ETH. Yesterday's SOL session called this "price overshooting TVL DOWN"; today's data confirms and intensifies that pattern.
5. **30d rel-strength vs BTC is POSITIVE (+3.2pp).** SOL is *outperforming* BTC over the past 30 days. The shortest-window engine input is favoring SOL.
6. **Solana 2y stablecoin growth (+403% per yesterday's session) is the strongest ecosystem-flow signal across all three assets**, and that trend is continuing through the dip rather than reversing.

**Bear case:**
1. **Worst long-window relative strength.** SOL −15.7pp vs BTC over 365d, −19.0pp over 180d. The franchise has *underperformed* both ETH and BTC for the bulk of the past year. The recent 30d turnaround is too short to call a structural shift.
2. **Highest volatility / largest beta.** SOL falls hardest in selloffs and rises hardest in rallies. If we're not at the cycle low yet, SOL has the *most* downside remaining among the three.
3. **At 1y trough means trough has *not yet held*.** Today's print IS the new low; buying at the literal low is psychologically appealing but statistically as likely to be the start of a new leg down as the end of the old one.
4. **The +$0.5B stables on SOL could be a single-whale event.** Cross-chain stablecoin minting is often dominated by large addresses; what looks like "broad-based accumulation" could be one or two institutional movers. The signal direction is real but the magnitude / breadth may be misleading.
5. **No engine confirmation.** Same situation as ETH and BTC — zero events firing on SOL over the past 20 days.
6. **The Solana drawdown narrative is harsher.** SOL went from ~$248 to $73 — anyone holding from $248 is sitting on losses of nearly 3/4. The capitulation buyer at $73 is competing against the persistent selling pressure of bag-holders.

## Phase B — counter-thesis

**The strongest case for the comparison being wrong (single-asset framing trap):** the user's question itself ("which is the better buy") forces a single-asset answer that the data may not support. The most disciplined response to a market where engine is silent + macro is benign + three assets are showing different flow patterns is to **not concentrate**, just DCA-equally across the crypto-core sleeve. Splitting today's tranche 33/33/33 across BTC/ETH/SOL would:
- Capture the BTC quality-anchor benefit
- Capture the SOL deepest-discount benefit
- Not abandon the ETH position that yesterday's session committed to building
- Avoid the trap of being wrong on a single concentrated bet

A smart fund manager would say: "You don't have an edge that tells you which of these three will recover *first*. You have an edge that tells you (a) all three are deeply discounted in benign macro, (b) the engine isn't screaming bottom OR more-pain-coming, (c) flow direction favors SOL today but the signal is 8 days old. The mature move is to DCA into all three proportional to your target crypto-core weights — typical institutional split is 60% BTC / 30% ETH / 10% SOL by market cap, but you've already overweighted SOL via yesterday's SUI rotation. Continue that weighting; don't try to pick a single winner from data that doesn't have a clear leader."

**Specific signals that would confirm a single-best-buy ranking instead of the diversified approach:**

1. **Engine fires a `leader_crossing` event on any of the three.** Currently zero in 20+ days. Would mean one asset has genuinely broken out vs BTC on the 30-day window — confirming structural outperformance, not just noise.
2. **SOL stablecoin supply crosses $17B (continued ramp).** Would confirm the +$0.5B in 8 days is a real broad-based accumulation, not a single-whale event.
3. **ETH stablecoin supply REVERSES back above $162B.** Would mean the "stables leaving ETH" signal was a temporary flush, not a structural capital flight; would re-strengthen yesterday's ETH bull thesis.
4. **BTC breaks $62k (new 1y low).** Would mean cycle isn't bottomed yet; the "buy BTC for safety" thesis weakens because the safety isn't safe.

**Base rate question:** crypto-core comparison decisions made at -47% / -62% / -70% drawdown depths in benign macro. Historical base rates favor: (a) the deepest discount asset outperforms over 12-24 months IF the bottom holds (the "discount" was real), (b) the highest-quality asset outperforms over 24+ months regardless. The 6-12 month horizon (which is closer to the user's reassessment window) is genuinely ambiguous — about 50/50 which framing wins on that horizon. **For "years" horizon (the user's actual stated horizon), the discount-depth argument compounds favorably.**

**The fund manager's strongest argument for the diversified rebalance:** "You should DCA proportionally into all three based on yesterday's already-committed weighting. Today's data slightly favors SOL on flow direction and slightly disfavors ETH, but neither shift is *large enough* to override the multi-decision portfolio approach you've been building. Concentrating into a single asset based on 8 days of stablecoin direction is exactly the kind of overactive trading the years-horizon discipline is meant to avoid."

## Conclusion

**Recommendation: ranked DCA, NOT single-asset concentration.** Allocate today's tranche across all three assets, but with weights that reflect the comparative data the lake produced today:

1. **SOL — 40% of today's tranche.** Best aggregate signal: deepest drawdown (−70% from peak), at 1y trough (best entry point), stablecoin supply *rising* during the dip (the clearest single bullish flow signal across all three), TVL holding up better than ETH despite price falling harder, 30d rel-strength leader. This is the highest-conviction "deploy capital here today" call from the comparative data — but is NOT a single-asset bet.
2. **BTC — 35% of today's tranche.** Quality anchor + lowest knife-catching risk + still 5% above 1y trough. The data doesn't support concentrating into SOL because BTC's role as the relative-strength anchor matters when engine is silent and signals are noisy. If today's SOL bull signal turns out to be a single-whale rotation, BTC absorbs that downside.
3. **ETH — 25% of today's tranche.** Continue yesterday's DCA per the prior decision's commitment, but at a *reduced* weight relative to the equal-split default because today's new data (stables leaving ETH) is *bearish-strengthening*, not just neutral. Yesterday's session committed to deploying 25-50% of intended ETH target with confirmation triggers; the −5% price move today gets closer to the bearish trigger (TVL <$35B = next add-tranche), but stables-leaving is a new negative that wasn't in yesterday's analysis.

**Sleeve & horizon:** Crypto-core, multi-year (years). The comparative ranking applies *within the existing crypto-core sleeve allocation* — this isn't an argument for changing total crypto exposure, it's a question of which of the three to weight more heavily on this tranche.

**Confidence: medium.** Same calibration as yesterday's sessions, and for the same reasons: the data is genuinely split on each asset individually but the comparative ranking is cleaner than expected (stablecoin flow direction is a strong differentiator). The engine's continued silence prevents "high" confidence on any directional call. The 40/35/25 weighting is *opinionated* but the deviation from a default 33/33/33 split is *only modest* — that's appropriate for medium confidence.

**Position-sizing implication:** Today's tranche should be sized at roughly **15-25% of total crypto-core capital available to deploy** (treating "intended target allocation" as the denominator). Splitting that tranche 40/35/25 across SOL/BTC/ETH means:
- If user has $X total dry powder for crypto-core: deploy ~$0.20X today, allocated as ~$0.08X SOL / ~$0.07X BTC / ~$0.05X ETH.
- Hold the remaining ~$0.80X for either continuation-buys at lower prices (bear case develops) or confirmation-buys at higher prices (bull case resolves).
- **DO NOT deploy all available crypto-core dry powder today.** Even with strong drawdown depth signals, the engine silence and the absence of institutional-positioning data (CFTC COT not yet flowing, ETF data not yet flowing) means we lack high-conviction confirmation. Reserve capital for the data we're about to gain.

**Key risks (counter-thesis distilled):**

1. **SOL flow signal could be a single-whale artifact.** The +$0.5B in stables on SOL might be 1-2 large institutional movers, not broad-based demand. If true, the SOL ranking should drop. Monitor: SOL stables crossing $17B within 30 days confirms broad-based; staying flat / reversing below $14.5B disconfirms.
2. **BTC could break to a new 1y low.** $62k is the prior 1y trough; a break below that signals cycle isn't bottomed yet and all three assets have further to fall. The "BTC as stability anchor" thesis weakens immediately if BTC breaks $62k.
3. **ETH stables-leaving accelerates.** If ETH stables continue dropping (below $158B / $156B) the bull case from yesterday fully collapses. Would push ETH weight down to 10-15% of next tranche, increasing SOL or BTC share.
4. **A 4th-leg-down resolves the ambiguity downward.** Today's prices look like a "could be bottoming" zone; another −15% across all three would make it a "second-leg-of-the-bear" zone. The smart move IF that happens is to deploy the next tranche AT THE LOWER PRICES, not now.
5. **Engine fires a `crypto_tvl_stress_combo` on ETH.** Would mean both TVL stress AND laggard crossing are present — the engine's bearish confluence signal. Would push ETH weight on the next tranche to ~0%.

**Trigger conditions for reassessment** (frontmatter): any of (a) ETH chain TVL breaks below $35B [bearish ETH escalation: ETH tranche weight drops to ~10-15%], (b) ETH chain TVL recovers above $50B [bullish ETH: tranche weight rises back to ~35-40%, SOL drops to ~30%], (c) SOL chain stablecoin supply drops below $14B [SOL flow reversal: SOL tranche weight drops to ~25%, BTC/ETH absorb], (d) SOL chain stablecoin supply holds above $17B [SOL bull confirmation: SOL tranche rises to ~50%], (e) BTC breaks below $62k [cycle capitulation: deploy all reserved tranche aggressively, weight by drawdown depth], (f) engine fires any `crypto_tvl_stress_combo` or `leader_crossing` on any of the three [individual asset re-rankings per the engine signal direction].

**Meta-takeaway (for `/reflect-decisions` in ~6 months):** This is the first *comparative* crypto-core decision in the log (prior ones were single-asset). If SOL outperforms BTC + ETH meaningfully (say +30pp combined over 6-12 months), the lesson is that the stablecoin flow direction signal was undervalued and should be promoted to a first-class engine input. If BTC outperforms SOL + ETH (the "quality wins"), the lesson is that drawdown-depth + flow-direction signals were not strong enough to override the long-cycle "BTC bottoms first" base rate. If ETH outperforms (surprise outcome), the lesson is that the engine silence was the dominant signal and the visible bear signals (stables leaving, TVL bleeding) were noise. The decision is structured so each outcome teaches something specific.

**Backlog implications surfaced by this session:**

1. **Run the CFTC COT backfill ASAP.** The most-load-bearing missing input for crypto-core decisions, and the data is now infrastructure-ready (B-031 shipped 2026-06-02). The single biggest leverage move from this session is `gh workflow run cftc-weekly.yml -f backfill=true` or running `python3 -m genkei.ingest.cftc --backfill` from the homelab terminal. Until COT lands, every crypto-core decision is blind to the institutional positioning context yesterday's sessions repeatedly named as critical.
2. **Merge `spot-etf-flow` and run the Yahoo backfill** — the second-most-load-bearing missing input. Daily ETF activity per asset would have produced a stronger "is institutional money in BTC vs ETH" data point for this comparison today. Once merged + backfilled, the next comparative decision can integrate `genkei etf-flows --asset BTC --since 2025-01-01` vs `--asset ETH` to see which ETF basket is seeing more aggregate activity.
3. **Stablecoin flow direction is currently the strongest single cross-asset comparative signal in the lake.** Worth surfacing as a typed CLI subcommand instead of requiring a `genkei query` SQL escape hatch. A `genkei stablecoin-flow --chain Ethereum --window 8d` or similar would let future comparative sessions skip the query-mode lookup.
4. **The "OG sellers" / whale-wallet question still applies** — same gap as yesterday. File-or-defer status unchanged (B-106 Etherscan whale-flow tracker is open at medium priority).
5. **Engine threshold tuning candidate:** the rel-strength emitter requires ±15pp on the 30-day window. ETH/BTC −5.1pp today is *neutral* in the engine's view, but combined with TVL bleeding + stables leaving, the *combination* is bearish. A future `crypto:core:capital_flight` rule could pair sub-threshold rel-strength weakness with stablecoin outflow + TVL contraction. Would surface the "ETH is weakening on multiple slow dimensions" pattern the current engine misses. Worth filing.

---

## Outcome (filled in by /reflect-decisions)

(reserved — pending; will resolve at 2026-12-03 or earlier on trigger)
