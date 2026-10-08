---
date: 2026-10-08
asset: SUI
sleeve: crypto-tactical
horizon: months
action: hold
confidence: medium
status: pending
supersedes: 2026-08-05-sui-ecosystem-thesis-exit
reflection_benchmark:
  type: destination_basket
  label: SOL destination (100%) — the swap Michael asked about and the destination every prior SUI/RENDER/PYTH exit file used
  assets:
    - ticker: SOL
      weight: 1.0
reflection_start:
  date: 2026-10-08
  asset_price_usd: 1.04
  asset_price_source: CoinGecko `sui` simple price at time of writing (−7.7% on the day); position already held, no new fill. Michael's blended cost basis not yet recorded — ask.
  benchmark_prices:
    - ticker: SOL
      price_usd: 108.68
      source: CoinGecko `solana` simple price at the same pull
      provisional: true
      note: Same-minute pull as the SUI print; lake Coinbase daily close for 2026-10-08 should replace both when it lands.
trigger_reassessment: "HOLD the spot SUI position unlevered, STOP the sub-$1.50 DCA clips, UNWIND the Suilend loop. SWAP into SOL (full position) on any of: weekly close < $0.82 (the Sept base and Michael's reopened-loop entry); Sui stablecoin supply back below $0.41B (the 2026-09-01 trough) on the lake's `stablecoin-flow --chain Sui`; watchlist Sui-protocol fees (cetus-clmm + navi-lending + suilend + bluefin-spot + deepbook-v3 + scallop-lend + bluefin-pro) below $1.0M for two consecutive full months in `defillama.protocol_fees`; Sui chain TVL below $415M (the 2026-08-05 exit level). TRIM half at $3.85–3.95 (prior-cycle ATH market cap ≈ $15.8–16.1B at today's ~4.1B circulating), not at the $5 Michael named, which would require ~28% above peak-cycle valuation on a larger float. ADD only if Sui stablecoin supply > $1.5B (the 2026-09-22 gate) with TVL > $800M — never on price. ZEC is NOT a swap destination at market: the 2026-09-17 ZEC file permits adds only on a washout into $700–900 with shielded supply >= 4.5M ZEC; if Michael wants ZEC exposure from SUI proceeds, stage a resting order in that zone. NEAR: hold per the 2026-10-06 file; do not swap a two-day-old staged position on a market-wide down day. Grade SUI vs SOL over months. HARD CALENDAR reassess 2027-01-08 with the consolidation file's quarterly metric pull."
related:
  - decision: 2026-10-08-crypto-chain-consolidation-thesis
  - decision: 2026-10-06-near-ai-chain-l1-assessment
  - decision: 2026-10-06-sui-ap2-gate-reassessment
  - decision: 2026-09-22-sui-agentic-payments-pal-thesis
  - decision: 2026-09-17-zec-position-sizing-reassessment
  - decision: 2026-06-02-sui-rotation-into-eth-sol
  - data: defillama.chain_tvl
  - data: defillama.protocol_fees
  - data: defillama.stablecoins
  - data: onchain.sui_unlocks
  - data: coinbase.candles
---

# SUI and NEAR — keep the thesis, or take the loss and swap into SOL / ZEC?

## Frame

Michael holds SUI (never sold through the 2026-06-02 rotation call or the 2026-08-05 exit call; has been adding sub-$100 clips under $1.50; reopened a Suilend loop at ~$0.82 with $200 of stable borrow) and NEAR (quarter-position starter filled at $5.10 on 2026-10-06). Prompted by the chain-consolidation evidence in the companion file, he asks whether to hold conviction on both or "take my losses and swap these two into Solana and/or Zcash." Horizon months, crypto-tactical, graded against SOL. Two things change the answer relative to August: the desk's own SUI exit calls have not beaten SOL, and the lake now shows the August file's fee baseline was an ingest artifact.

## Macro context

Risk-off day inside a nominally risk-on regime: SOL −7%, SUI −8%, NEAR −8%, ZEC −13%, BTC −1 to −3% at the time of writing; 10-year at 5.28% and rising, DXY 121.8. The companion consolidation file documents the regime that matters here: capital concentrating on Ethereum, Base, Solana, Tron, BSC and a few purpose-built chains, with mid-tier general-purpose L1s emptying (Sui TVL −79% y/y). A swap decision made on a −8% day should be judged on the fundamentals, not the candle.

## Fundamentals

**SUI today (CoinGecko, nearblocks-equivalent lake pulls, 2026-10-08).** Price $1.04; market cap $4.28B; FDV $10.4B with 4.12B of 10.0B tokens circulating (41%), so 5.9B tokens remain to unlock (Series B alone 25M/month in `onchain.sui_unlocks`). ATH $5.35 (2025-01-04), −81%. Returns: 7d −9.6%, 30d +25.6%, 60d +48.2%, 1y −70%.

**What the August exit file said would reopen SUI, and where each stands.**

| Reopen condition (2026-08-05) | Then | Now | Status |
|---|---|---|---|
| Chain TVL reclaims $800M with 2+ months growth | $415M | $546M (Sept $453M → Oct $554M) | Growing two months; below threshold |
| Watchlist Sui-protocol fees ≥ $1M/mo for 2+ months "vs $314K July" | $314K (as measured then) | Jul $1.14M, Aug $1.53M, Sep $1.82M | **Met as written, but the baseline was wrong** |
| Stablecoin supply grows 2+ consecutive months | shrinking | Aug 15 $0.47B → Sep 1 $0.41B → Oct 7 $0.50B | One month of growth after a dip; not two |
| Spot SUI ETF approved with a month of positive flows | filed only | TSUI/GSUI/SUIS live; Feb +$21.3M (21Shares) | Met (external, not lake-tracked) |

The fee row needs an honest note. The lake's `defillama.protocol_fees` shows Sui watchlist-protocol fees of $1.67M (Apr), $2.04M (May), $1.69M (Jun), $1.14M (Jul), $1.53M (Aug), $1.82M (Sep). The August file recorded July at $314K and a −91% collapse from $3.43M; that July figure was taken on Aug 5 during the ingest outage logged in research-questions (sources stale from ~Aug 6–7, `defillama normalize` failing on a hypertable FK violation), so it was a partial month. Fees never collapsed 91%; they fell ~45% from spring and have recovered to roughly flat. The trigger fires mechanically (two months above $1M), which is why this file supersedes the August one, but it does not describe an ecosystem reversal. It describes a measurement error being corrected.

**Relative usage, which is the thing that actually matters for a consolidation regime.**

| Metric (30d unless noted) | Sui | Solana | Sui as % of Solana |
|---|---|---|---|
| Chain TVL | $546M | $6.3–6.6B | ~8% |
| Stablecoin supply | $0.50B | $16.6B | 3% |
| Chain gas fees | $0.20M | $27.7M | <1% |
| DEX volume | $1.52B (−40% m/m) | $76.4B (−21% m/m) | 2% |
| Watchlist protocol fees | $1.82M (Sep) | n/a | — |
| RWA ranking (rwa.xyz) | not in top 10 | #3 ($4.4B) | — |

Sui is roughly 2–8% of Solana on every usage measure and its DEX volume fell twice as fast last month. It is also on the wrong side of the consolidation tables: TVL −79% in a year against Solana's −49% (both dollar figures; Sui's token fell further, but stablecoins and fees confirm the direction).

**What is genuinely new for Sui.** Basecamp (Oct 7–8) produced a $500M+ committed Bitcoin-finance programme (Hashi), Alibaba Cloud and Google Cloud agent-budget and verification agreements, and a 40M TPS live test. The 2026-10-06 reassessment already weighed the AP2 and Beep evidence and retained avoid on the agentic-payments thesis. None of this week's announcements adds attributable usage yet; they are the kind of partnership evidence the 2026-08-05 file called "real, cycle-timed, no on-chain follow-through" and the gate for changing that view (stablecoins > $1.5B) is unchanged.

**NEAR today.** $4.71, −8% on the day, −7.6% from the $5.10 fill two days ago. Nothing in the 2026-10-06 file has changed: Intents 30d volume $4.5B, NEAR capture of gross fees $1.65M (Sept), NRR ETF taking inflows, Nov 1 capture print pending, invalidation at a $2.60 daily close. NEAR is the opposite profile from SUI: a revenue line independent of chain TVL, rising capture, an ETF bid, and a token that already outperformed ETH and SOL by 200+ points YTD. Its risks are leverage and issuance, not relative decay.

## Flow & positioning

**The desk's SUI record, graded (lake Coinbase candles).**

| Call | SUI then → now | SOL then → now | ZEC then → now | Verdict |
|---|---|---|---|---|
| 2026-06-02 rotate SUI → ETH/SOL | $0.808 → $1.04 (+29%) | $74.12 → $108.68 (+47%) | $610 → $1,144 (+88%) | SOL call right; ZEC better |
| 2026-08-05 exit SUI → SOL | $0.69 → $1.04 (+50%) | $73.97 → $108.68 (+47%) | $512 → $1,144 (+124%) | A wash vs SOL; ZEC far better |

Michael's hold beat the August exit by three points and lost to it by 18 points from June. Both desk calls would have been right about ZEC and were not made about ZEC. The honest base rate: the desk's SUI fundamental read (relative usage decay) has been right for four months, and its price call has been a coin flip against SOL. Swapping SUI into SOL has not been the free alpha the files implied.

**Positioning.** The Suilend loop is leverage on the asset with the weakest relative usage in the book, into a tape where 10-year yields and the dollar are rising. The sub-$1.50 DCA clips are adding to a mid-tier L1 during the regime the companion file documents. Lake `watchlist score` gives SUI +3 (relative-strength +2, TVL trend +1), the same as JUP, which is a reminder that the scorer sees the 30-day price bounce, not the two-year share loss. NEAR is not yet in the scorer (added to the watchlist 2026-10-06, ingest pending).

## Phase A — case for and case against

**Case for holding SUI.**
1. Two desk exit calls, neither beat SOL in price; the third would be sold on a −8% day after a +26% month.
2. Stablecoins, TVL and fees are all up since the August exit level (stablecoins +22% since Sept 1), so the asset is not collapsing, it is lagging.
3. Basecamp delivered the largest institutional commitments Sui has had (Hashi $500M, Alibaba, Google); SUI ETFs exist and SUIG is a listed treasury vehicle.
4. The 41% float cuts both ways: thin circulating supply means a liquidity upcycle moves the token hard.

**Case for swapping SUI.**
1. Every consolidation metric puts Sui among the losers; 3% of Solana's stablecoins and 2% of its DEX volume is not a top-chain profile.
2. 5.9B tokens still to unlock against a $4.3B market cap; the $5 target implies a $20.5B market cap, 28% above the prior cycle's peak valuation, on a float that keeps growing.
3. The fee "recovery" is a measurement correction, not an inflection; fees are flat-to-down from spring.
4. SOL has the same beta with ten times the usage and is already a core holding; ZEC has been the actual winner of every SUI comparison.

**On NEAR.** The case for swapping is that it is down 7.6% in two days and macro is turning. The case against is that nothing thesis-relevant moved, the file has an explicit invalidation ($2.60) and a dated second-tranche trigger (Nov 1), and reversing a two-day-old decision on a market-wide down day is the exact churn the decision log exists to prevent.

## Phase B — counter-thesis

The strongest case against holding SUI: the consolidation regime is the signal, the Basecamp announcements are the same cycle-timed partnership pattern the August file identified, and the honest reading of "SUI matched SOL since August" is that SUI has had its beta bounce and now carries 5.9B tokens of overhang into a liquidity-tightening tape; the desk is rationalising a hold because its last two sells were early. If that is right, the lines in the frontmatter ($0.82 weekly close, stablecoins < $0.41B, fees < $1M for two months) are where it shows up, and the position goes to SOL there rather than here. The strongest case against holding NEAR: it rallied 2x on leverage into a macro turn and gives it all back; the $2.60 line is the answer, and it was set before the drop.

## Conclusion

**SUI: hold the spot position, but change how it is held. Do not swap at market today.** Stop the DCA clips, unwind the Suilend loop, and let the hard lines in the frontmatter make the exit decision instead of a −8% candle. Confidence medium: the fundamental read says Sui is a consolidation loser, the price record says the desk has not made money selling it to buy SOL, and the resolution is a held position with mechanical exits rather than a third discretionary sell.

- **Discipline size:** trim 50% into SOL now, hold 50% with the frontmatter lines, no adds. This books the consolidation view without betting the whole position against the desk's own two-for-two record of early SUI exits.
- **Conviction size (Michael's side, per the standing sizing feedback):** hold 100% spot, no adds, close the loop, full swap into SOL only on a line break. Reset the target from $5 to $3.85–3.95 and trim half there.
- **ZEC as destination: not at $1,144.** The ZEC file's rule is add only on a washout into $700–900; ZEC is −13% today and −17% on the week, so the zone is approaching. If Michael wants ZEC exposure from SUI proceeds, the discipline trim funds a resting order in that zone, not a market buy.

**NEAR: hold, unchanged.** The 2026-10-06 file stands: invalidation at a $2.60 daily close, second tranche on the Nov 1 capture print, no chase above $5.40. A two-day drawdown that matches the market is not evidence.

Top risks: (1) the hold is early-exit regret dressed as patience, bounded by the four lines; (2) a liquidity upcycle lifts SUI 3x on its thin float and the discipline trim looks wrong, bounded by holding at least half; (3) the lines trigger together in a crash and the swap executes into SOL weakness, acceptable because SOL is the core benchmark either way.

Position-sizing: SUI is a tactical position; it should not exceed the sleeve's speculative ceiling after today, and nothing in this file adds to it. Reflection baseline SUI $1.04 / SOL $108.68 on 2026-10-08, provisional until the Coinbase daily close lands. Michael's blended SUI cost basis is not in the record and should be, so the eventual outcome can be graded in realised rather than paper terms.

---

## Outcome (filled in by /reflect-decisions)

(reserved — pending)
