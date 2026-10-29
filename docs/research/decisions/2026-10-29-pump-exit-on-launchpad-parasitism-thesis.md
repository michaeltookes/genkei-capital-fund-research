---
date: 2026-10-29
asset: PUMP
sleeve: crypto-tactical
horizon: months
action: hold
confidence: low
status: pending
supersedes: 2026-10-01-pump-vs-pyth-comparative-buy
reflection_benchmark:
  type: destination_basket
  label: SOL destination (100%) — where the dollars go if Michael sells, and the benchmark every prior crypto-tactical exit file used
  assets:
    - ticker: SOL
      weight: 1.0
reflection_start:
  date: 2026-10-29
  asset_price_usd: 0.00448
  asset_price_source: CoinGecko `pump-fun` simple price at 15:38 UTC (+6.9% on the day; lake CoinGecko print at 06:48 CT was $0.00436). Position already held — Michael disclosed on 2026-10-29 that he owns PUMP at a loss; the 2026-10-01 record never received his fill. Fill date, price, and size not yet recorded — ask.
  benchmark_prices:
    - ticker: SOL
      price_usd: 102.41
      source: CoinGecko `solana` simple price at the same pull
      provisional: true
      note: Same-minute pull as the PUMP print; replace both with the lake Coinbase daily close for 2026-10-29 when it lands. If Michael supplies his fill date, re-base both quotes to that date instead.
trigger_reassessment: "HOLD the existing PUMP position; NO ADDS — the 2026-10-01 'add on the October print >= $50M' gate is cancelled regardless of what the November 1 print says. EXIT in full into SOL on any of: (1) DeFiLlama parent `Pump` revenue for the full November UTC month < $40M (read in the first week of December; October was $57.3M through 10/28) OR launchpad-only `pump.fun` November revenue < $25M; (2) weekly close < $0.0035 (the Sept 7–14 base, $0.00348–0.00351); (3) any rival launchpad > 60% of Solana launchpad 30d fees on DeFiLlama (pump.fun is 72% today); (4) PUMP trails SOL by > 20 pp over any trailing 30 days measured from the 2026-10-29 baseline ($0.00448 / $102.41); (5) an adverse Aguilar v. Baton step per the 2026-10-01 veto list (class certification with damages scoped to fee revenue, an injunction touching the fee/burn pipeline, a receiver, or a settlement redirecting revenue away from the burn contract); (6) any US federal action that names launchpads or pump.fun specifically (SEC or CFTC enforcement, or a market-structure bill clause); (7) HARD CALENDAR: exit before 2027-04-29 unless an on-chain renewal or successor to the 50%-of-revenue burn contract is published by 2027-03-31 — and exit the same day Baton says it will not renew. TRIM HALF (do not wait for the full-exit lines) on either: trailing 7-day parent `Pump` revenue < $7M (it was $11.7M for 10/22–10/28; that is a -40% fade), or a weekly close < $0.0040 (below the 10/18 and 10/27 lows at $0.00416). DISCIPLINE OPTION for Michael's values conflict: if he does not want to be the casino's partner at full size, trim 50% now (~$0.0045) and let the lines above finish the job; if the position is larger than one tactical-secondary starter, cut it to a starter now regardless. A full sale today is his conviction call, not the desk's data call — if executed, flip this record's action to `sell` with the fill as the baseline so the reflection cycle grades it against SOL."
related:
  - decision: 2026-10-01-pyth-reentry-trigger-fired-reserve-v2
  - decision: 2026-10-08-crypto-chain-consolidation-thesis
  - decision: 2026-09-17-pyth-checkpoint-swap-to-sol
  - decision: 2026-09-06-pump-fun-buyback-assessment
  - data: coingecko.market_data
  - data: defillama.chain_tvl
  - data: defillama.stablecoins
  - data: gdelt.gkg
  - data: fred.observations
---

# PUMP — sell everything on the launchpad-parasitism thesis, or hold the 2026-10-01 position on its lines?

## Frame

Michael holds PUMP (disclosed today; the 2026-10-01 file was still `inactive` because no fill was ever reported, so the desk's record is behind his book). He is down on it and is considering selling all of it, "even at a loss," on a thesis he laid out in three parts: launchpads removed the friction that used to make token launches scarce and made promoters hold, so the market is saturated with one-second launches; the only durable winners are bundling insiders and pump.fun itself (creation fees plus a take on every trade, partly recycled to holders through buybacks); and the industry is about to turn on launchpads and memecoins as a category before the next bull cycle, so the asset should be sold before that turn. Sleeve: crypto-tactical (the 10/01 file already settled that PUMP can never be core: "a memecoin launchpad is the casino"). Horizon: months, bounded by the burn contract's 2027-04-29 expiry. The question splits cleanly: is the thesis right about the product (that is a values question the desk does not grade), and is it already in the numbers that pay PUMP holders (that is what the desk can measure). **Written before the evidence, what would make SELL the data answer:** pump.fun's revenue already falling with memecoin volume share, a 10/01 exit line fired, or the lake showing the sentiment turn in coverage tone. **What would make HOLD the data answer:** revenue holding or rising while memecoin share has already collapsed (the turn already happened without touching the cash flow), no exit line fired, and price sitting at the low end of a drawdown.

## Macro context

`genkei macro-regime` (2026-10-26): **mixed**. DGS10 4.99% (-18 bp/30d), HY OAS 3.30% (+37 bp/30d, widening for a second month), VIX 18.4, USD index 121.3 (+1.0/30d). `genkei momentum --asset-class crypto --window 30` (10/28 closes): BTC -4.3%, ETH -13.3%, SOL -15.9%, LINK -20.8%, SUI -20.3%, PYTH -11.9%, RENDER -13.5%; only HYPE is up (+1.8%/30d, +13.2%/7d). `genkei stablecoin-flow --chain Solana`: $15.6B, **-$0.83B over 30 days** — dry powder is leaving the chain PUMP lives on. `genkei tvl --chain Solana`: $5.62B on 10/28, down 14% from the 10/06 peak of $6.57B. The regime is a credit-led risk-off drift, and everything high-beta in the sleeve is bleeding; PUMP is the highest-beta asset Michael owns, and its -24% since 10/01 is roughly 1.5x SOL's -16%. The bear-market feeling is in the tape, not only on Twitter. Macro argues for staging and for hard lines, which is what this file does; it does not by itself distinguish PUMP from the rest of the sleeve.

## Fundamentals

**Revenue is at a 13-month high, not rolling over.** DeFiLlama parent `Pump` (API pull this session, `api.llama.fi/summary/fees/pump`): monthly revenue 2026 — Jan $48M, Feb $38M, Mar $36M, Apr $31M, May $34M, Jun $27M, Jul $33M, Aug $55.5M, Sep $49.4M, **Oct $57.3M through 10/28** (~$63M full-month pace), the best month since Sep 2025 ($56M). Fees $161M through 10/28, a 2026 high. Launchpad-only `pump.fun`: Oct $42.1M revenue / $58.0M fees vs Sep $33.1M / $46.4M and Aug $36.7M / $48.0M. The thesis's third leg ("the industry is about to turn") is not in October's cash flow.

**But the pie it draws from is shrinking, and the record is a share story.** Blockworks puts memecoins' share of Solana DEX volume at ~10%, down from 67%; Galaxy had 15–20% in Q2 2026 vs >50% at end-2024; Solana DEX volume fell 24% week-over-week to $11.9B in late October, attributed to fewer small new-token trades. Pump.fun's revenue rose anyway because it took the category: `api.llama.fi/overview/fees/solana`, 30-day launchpad fees — pump.fun $62.2M of an $86.7M category (**72%**), StonkFun $10.5M (down from $30.1M the prior 30 days), LaunchLab $4.5M (from $9.7M), BONK.fun $3.7M (from $7.7M); Pons, the September attacker, is at $16.4M October fees / $2.4M revenue after $137M / $23.4M in September, -88% from peak. PumpSwap ($107M fees/30d) is now the largest Solana DEX by 24h volume ($259M on 10/23), and the mobile app adds $10.5M/30d. Share cannot exceed 100%: the next leg down in Solana speculative volume hits revenue with nothing left to offset it. **The intra-month trajectory already shows it:** parent daily revenue $2.0–2.2M on 10/19–21 fell to $1.35–1.83M on 10/24–28 (-25%); launchpad-only from $1.6–1.7M to $1.0–1.4M.

**The burn, measured.** Issuer dashboard (`pump.fun/pump-token`, daily log through 10/27): **$496.51M cumulative, 174.26B PUMP burned**, up from $470.35M on 10/01 — **$26.2M in 27 days, ~$0.97M/day, ~$29M/month**; the late-October daily spend is $590–925K, below early October's ~$1M+. At 50% of eligible revenue that implies ~$52M of burn-eligible revenue for Oct 1–27, which is the first independent cross-check the desk has had on the 10/01 file's "eligible revenue" gate: it clears $50M for October on this reading, though the issuer dashboard still carries its own accuracy warning. Six months remain on the contract; at the current pace that is ~$150–175M more buying, **8–10% of today's capitalization** ($1.79B on the issuer's 399.46B circulating figure; CoinGecko shows $2.06B on ~462B — the supply-definition gap from the 10/07 review is still unresolved). After 2027-04-29 the dashboard's own language is that purchases "carry no obligation."

**Unlocks offset the burn.** The 10/12 insider tranche (6.875B, ~$31M at today's price) landed; lake CoinGecko shows PUMP $0.00555 → $0.00497 that day (-10%). Monthly tranches continue through July 2029 at roughly the same size as the monthly burn, so net circulating supply is approximately flat: the burn is an offset to insider distribution, not net shrink. That qualifier was already in the 10/01 file and it still holds.

**Price and the 10/01 lines.** `coingecko.market_data` (lake): PUMP $0.00575 on 10/01 → peak $0.00652 on 10/04 → $0.00436 on 10/29 (lake 06:48 CT; $0.00448 CoinGecko at 15:38 UTC). **-24% since the 10/01 file, -33% from the October peak.** Volume faded from $300–600M/day in early October to $80–180M/day in the last ten days. It never closed above the $0.0070 chase block; the discipline pullback band ($0.0045–0.0050) was reached on 10/15 and price has since traded below it (lows $0.00416 on 10/18 and 10/27). SOL over the same window: $119.5 → $100.2 (-16%), so PUMP trails SOL by ~8 pp since 10/01, inside the 20 pp exit line. **None of the 10/01 exit conditions has fired**: eligible revenue is above $35M (and above the $50M add gate on the burn cross-check), no rival is above 60% share, SOL relative strength is inside the line, and the Aguilar docket shows no new escalation (the 8/31 order dismissing the securities counts while letting RICO proceed predates the file's 9/11 docket check; no trial date; nothing found for October).

**Base rate for the "turn."** Parent revenue has already done one full memecoin bust inside the lake's window: Jan 2025 $150M → Jul 2025 $20M (**-87%**), then a grind back to $55M by Aug 2026. The procyclical-burn risk the 9/06 and 10/01 files named ("shrinks exactly when needed") is real and has happened once; it is also why this file's exit lines are revenue-keyed rather than narrative-keyed.

## Flow & positioning

No insider surface exists for a token; the monthly unlock schedule is the insider flow, and it is a known seller. Solana stablecoin supply -$0.83B/30d and US spot SOL ETFs -$99.6M in October are the positioning read for the chain. PUMP daily volume has fallen ~70% from early October, which is speculative interest draining, not accumulation.

**The sentiment turn cannot be measured from the lake.** `gdelt.gkg` carries 1–2 pump.fun-tagged articles per week over the last 12 weeks because pump.fun is not a watchlist term (the 10/01 file's activation housekeeping — move PUMP to `crypto.secondary` with `gdelt_terms`, add the `pump` protocol slug — was never done because the fill was never reported). Memecoin-tagged coverage: 165 articles with tone -2.1 in the week of 9/07 (the Pons attack), then 2–16 articles/week with tone between -1.1 and +0.4 since. That is too thin to confirm or deny Michael's read of Twitter; the desk records it as **unmeasured**, which is different from false.

## Phase A — case for and case against

**Case for selling now (Michael's thesis, with the evidence that supports it):**
1. The product description is accurate and was already the desk's own: friction-free launches, insider bundling, a take on creation and every trade. Secondary sources put graduation at ~0.7–0.8% of launches and rug-like behaviour at ~98.6% (methodology unverified), and the FCA forced a UK exit in 2024. This is the casino.
2. The category has already lost the chain: memecoins fell from 67% to ~10% of Solana DEX volume, the Solana Foundation is steering the narrative to stablecoins and RWA, and HYPE's +13%/7d against a red sleeve says the next cycle's speculative capital is going to perps and exchanges, not launchpads.
3. Revenue is a share-of-a-shrinking-pie story at 72% share with a -25% fade inside October; the asset's cash flow has done -87% once before in this very window.
4. Net supply is flat (unlocks ≈ burn), the burn contract expires in six months, the RICO case proceeds, and the position was taken (per Michael's own book) above the current price.
5. A position the holder is ashamed of gets sold at the worst moment anyway; selling on a plan beats selling on a bad day.

**Case for holding on the lines:**
1. October is the best revenue month in 13 months; the thesis's "turn" is visible in volume share but not in the cash flow that funds the buyback. Selling on a narrative the numbers have not yet confirmed is a sentiment trade.
2. Not one of the 10/01 exit lines has fired, and the burn cross-check clears the $50M October gate.
3. The remaining six months of contractual buying is 8–10% of capitalization, a floor the sleeve's other names do not have.
4. Price is -33% from the October peak at the lows of the range with volume exhausted — the desk's own calibration (below) says this is exactly when it has been wrong about these names.
5. The desk's token-accrual lens (Michael's own rule from 10/19: every token needs a one-sentence holder claim) is satisfied until 2027-04-29: "half of pump.fun's net revenue buys and burns PUMP." Few tactical names pass it.

## Phase B — counter-thesis

**Against selling.** The "industry turns on memecoins" thesis is largely a description of something that already happened (volume share 67% → 10%) without denting pump.fun's revenue, so what Michael is really forecasting is a further collapse in *total* Solana speculative volume. That is also a SOL revenue call — Solana's own fee and MEV take depends on the same churn — and he is not selling SOL. The consistent version of his view is "sell the casino, keep the chain" only if the chain's non-speculative revenue is growing fast enough to replace it, which the stablecoin and TVL data this month do not show. Second, the desk's track record on these high-beta names says selling at moments like this has been wrong: the 9/17 PYTH swap call would have cost 23 pp in two weeks, and the 9/06 PUMP avoid preceded a +40% rally (`docs/research/decisions/2026-09-17-pyth-checkpoint-swap-to-sol.md`, outcome). Both were bearish calls made when the name was already down and the narrative was ugly. Third, "even at a loss" is a framing, not a reason: the loss is sunk either way; the only question is where the dollars earn more from here, and the reflection benchmark (SOL, -16%/30d itself) makes that explicit.

**Against holding.** The revenue decoupling has a hard ceiling (share is already 72%) and the intra-October fade is the first print of the pie shrinking faster than share can rise. If November parent revenue comes in near $40M, the burn drops toward $20M/month into monthly unlocks of ~$31M and net supply starts growing into a falling price — the procyclical trap, seen once at -87%. And the asset has an expiry: on 2027-04-29 the holder claim ends unless renewed, and nothing in the data today says whether Baton renews. Holding a wasting claim through a bear market while waiting for a revenue line to confirm the bear is how a -33% drawdown becomes -60%.

**What the desk is most likely overweighting:** the October revenue print, because it is the one hard number that contradicts Michael's read. The late-month fade is small in sample but it is the direction his thesis predicts. That is why the trim-half tripwire sits at a 7-day revenue level, not a monthly one.

## Conclusion

**Hold the position on explicit, thesis-keyed lines; do not add; do not sell the whole thing today on sentiment.** The data answer is hold because October revenue is a 13-month high, the burn cross-check clears the add gate, none of the 10/01 exit conditions has fired, and the price is at the low end of a -33% drawdown with volume exhausted — the configuration in which the desk's bearish calls on PYTH and PUMP were both wrong within weeks. Michael's thesis is right about the product and already visible in volume share; it is not yet visible in the cash flow that pays PUMP holders, and the lake cannot measure the Twitter turn he is seeing. So the file converts the thesis into lines the data can execute: full exit into SOL if November parent revenue prints below $40M (or launchpad-only below $25M), on a weekly close below $0.0035, on a rival above 60% share, on a 20 pp lag to SOL from today's baseline, on an Aguilar escalation or any federal action naming launchpads, and before 2027-04-29 absent a renewal by 2027-03-31; trim half early on a 7-day revenue fade below $7M or a weekly close below $0.0040. **The 10/01 add gate is cancelled**: adding to a position its owner wants to sell is wrong regardless of the November 1 print.

**Discipline and conviction, per Michael's standing instruction.** Discipline (the desk's call): hold at no more than one tactical-secondary starter, cut to that size now if the position is larger, lines above. Conviction (Michael's, in the sell direction): if he does not want to own the casino on principle, trim 50% now at ~$0.0045 and let the lines handle the rest — a values decision deserves a size, not a narrative, and half out is the size that lets him hold the remainder through the November print without flinching. A full sale today is defensible only as a values decision, not a data decision; if he makes it, this record's action flips to `sell` with his fill as the baseline so the reflection cycle grades it against SOL honestly. One tax note, not advice: a realized loss offsets realized gains, and no US wash-sale rule currently applies to crypto, so a sell-and-wait is possible if he wants the loss booked — confirm with his tax adviser.

Sleeve: crypto-tactical. Horizon: months, hard-bounded by 2027-04-29. Confidence: **low** — the direction over months is close to a coin flip in a credit-led risk-off, the sentiment leg of the thesis is unmeasured, the revenue decoupling has a ceiling, and the asset has an expiry; what the desk is confident in is the structure, not the outcome. **Top risks:** (1) Solana speculative volume keeps falling and November revenue prints below $40M while the price is already lower (the procyclical trap); (2) Baton signals non-renewal early, which ends the holder claim on the spot; (3) a US regulatory or litigation step aimed at launchpads specifically. **Housekeeping:** this file supersedes the 2026-10-01 record (activation happened off-log; fill unrecorded); Michael owes the desk his fill date, price, and size, after which the activation moves from the 10/01 file (PUMP to `crypto.secondary` with `gdelt_terms`, the `pump` protocol slug) get made so the November lines can be read from the lake instead of the API.

---

## Outcome (filled in by /reflect-decisions)

(reserved — pending)
