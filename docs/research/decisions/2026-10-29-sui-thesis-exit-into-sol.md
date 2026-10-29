---
date: 2026-10-29
asset: SUI
sleeve: crypto-tactical
horizon: months
action: sell
confidence: medium
status: pending
supersedes: 2026-10-08-sui-hold-vs-swap-into-sol-zec
reflection_benchmark:
  type: destination_basket
  label: SOL destination (100%) — where the proceeds go; the benchmark every SUI exit file has used
  assets:
    - ticker: SOL
      weight: 1.0
reflection_start:
  date: 2026-10-29
  asset_price_usd: 0.944
  asset_price_source: CoinGecko `sui` simple price at 16:37 UTC (+2.3% on the day; lake CoinGecko print at 06:46 CT was $0.922). Position already held; Michael's blended cost basis is still not in the record (sub-$1.50 clips plus the ~$0.82 Suilend-loop entry) — ask, and re-base the realised P&L to it when supplied.
  benchmark_prices:
    - ticker: SOL
      price_usd: 101.85
      source: CoinGecko `solana` simple price at the same pull
      provisional: true
      note: Same-minute pull as the SUI print; replace both with the lake Coinbase daily close for 2026-10-29 when it lands. If Michael executes the swap on a different day, his fill becomes the baseline.
trigger_reassessment: "SWAP the SUI position into SOL as a thesis exit, not a stop. Sequence — (1) unwind the Suilend loop first if it is still open (repay the ~$200 suiUSDT, withdraw the sSUI; record the realised inputs the 2026-07-21 file is missing); (2) sell spot SUI into SOL; the desk's call is 100%, staged as 50% at market and 50% within the week on any bounce toward $1.00 but no later than 2026-11-05 — do not wait for the November 1 unlock to print first. NO re-entry on price. RE-ENTRY gate (same as the 2026-10-08 add gate): Sui stablecoin supply > $1.5B with chain TVL > $800M on the lake, OR watchlist Sui-protocol fees > $3M in a calendar month — until then SUI is `avoid`. Grade the swap against SOL over months from the 2026-10-29 baseline ($0.944 / $101.85), re-based to Michael's fill if different. If Michael chooses the discipline variant (50% now, 50% held), the retained half exits on the 10/08 lines tightened to what the data already shows: weekly close < $0.88 (the 10/27 low $0.911 with the unlock ahead), Sui stablecoins < $0.43B on the lake (10/28 print $0.443B; the 2026-09-01 trough was $0.413B), chain TVL < $450M (10/28 $473M), or watchlist Sui-protocol fees < $1.2M for the November calendar month. HARD CALENDAR: any retained SUI is reassessed 2027-01-08 with the consolidation file's quarterly metric pull; if the consolidation metrics still hold, sell the remainder regardless of price."
related:
  - decision: 2026-10-08-crypto-chain-consolidation-thesis
  - decision: 2026-10-29-pump-exit-on-launchpad-parasitism-thesis
  - decision: 2026-10-06-near-ai-chain-l1-assessment
  - decision: 2026-10-06-sui-ap2-gate-reassessment
  - decision: 2026-09-17-zec-position-sizing-reassessment
  - decision: 2026-08-05-sui-ecosystem-thesis-exit
  - decision: 2026-07-21-sui-suilend-leveraged-loop-contrarian
  - decision: 2026-06-02-sui-rotation-into-eth-sol
  - data: defillama.chain_tvl
  - data: defillama.stablecoins
  - data: defillama.protocol_fees
  - data: onchain.sui_unlocks
  - data: coingecko.market_data
---

# SUI — the holder has lost conviction: swap into SOL now, or keep the 2026-10-08 lines?

## Frame

Michael holds SUI (never sold through the 2026-06-02 rotation or the 2026-08-05 exit call; sub-$1.50 DCA clips; a Suilend loop reopened at ~$0.82 with $200 of stable borrow that the 10/08 file told him to unwind; blended cost still unrecorded). Three weeks after the 10/08 hold-with-lines file he says he has "lost almost all conviction." His new thesis, in his words: Solana's cycle performance came from being the escape valve from Ethereum gas and the home of memecoin trading, and now rests on hosting pump.fun; crypto is maturing toward traditional finance rather than the DeFi future; sentiment has moved to perps and privacy, away from the L1-race and mass-adoption narratives; the top 15 is essentially chosen; Ethereum and Solana can carry global tokenization between them the way Visa and Mastercard carry payments, and if there is an American Express slot it is more likely XRP or Binance than Sui, which is "throwing ideas at the wall." The question is whether a change of conviction, with no 10/08 line fired, is itself a reason to swap into SOL. Sleeve crypto-tactical, horizon months, benchmark SOL. **What would make HOLD the data answer:** the 10/08 metrics stable or improving, a measurable catalyst (ETF flows, stablecoin growth, fee growth) that the holder is discounting, and lines comfortably far away. **What would make SWAP the data answer:** every 10/08 metric drifting toward its line, a structural supply headwind SOL does not share, and evidence that the "throwing ideas at the wall" read is literal.

## Macro context

Unchanged from this morning's PUMP file: `genkei macro-regime` **mixed** (DGS10 4.99%, HY OAS 3.30% and widening, VIX 18.4, USD 121.3); 30-day crypto momentum negative across the sleeve (BTC −4.3%, ETH −13.3%, SOL −15.9%, SUI −20.3%); Solana stablecoins −$0.83B/30d; Ethereum TVL $53.2B → $45.8B in October. The one bright spot is the narrative Michael names: HYPE is +20% today to a $23.0B capitalization and ZEC is +6% to $22.9B, ranks 10 and 11 on CoinGecko, while every general-purpose L1 outside the top two is red. The regime does not differentiate SUI from SOL; the sector rotation inside crypto does, and it is running Michael's way.

## Fundamentals

**Every 10/08 metric moved toward its line in three weeks; none crossed.** From the lake (`defillama.chain_tvl`, `defillama.stablecoins`, `defillama.protocol_fees`, `coingecko.market_data`):

| Metric | 10/08 file | 10/28 | 10/08 exit line | Distance |
|---|---|---|---|---|
| Sui chain TVL | $510M | $473M (−7%) | < $415M | 14% |
| Sui stablecoin supply | $503M | $443M (−12%) | < $0.41B | 7% |
| Watchlist Sui-protocol fees, calendar month | Sep $1.82M | Oct $1.53M through 10/28 (~$1.70M pace) | < $1.0M two months | ~40% |
| SUI price | $1.04 | $0.944 (16:37 UTC) | weekly close < $0.82 | 13% |

**The stablecoin print is the one that matters, and it broke on 10/21.** Sui stablecoins were $547M on 10/20, $463M on 10/21, $433M on 10/22, and have sat at $442–444M since. The move is one asset: USDC on Sui went from $375M to $275M in eight days (−$100M, −27%), while USDsui ($77M), FDUSD, BUCK and suiUSDe were flat. That is a single large holder or market-maker leaving the chain, not retail drift, and it leaves supply 7% above the 2026-09-01 trough that the 10/08 file set as the exit line. The 9/22 add gate was $1.5B; the chain is at 30% of it and falling.

**Supply is the structural headwind SOL does not share.** `onchain.sui_unlocks` (DeFiLlama emissions feed, refreshed 10/29): ~138M SUI unlocking in October and again in November (Community Reserve ~59M, Mysten treasury ~24M, plus investor/contributor tranches), 82M in December, 165M in January. At $0.94 that is ~$130M a month of new float against a $3.9B capitalization, ~3.4% of circulating supply per month, into a chain whose stablecoin base is $443M. Third-party trackers quote a smaller "November 1" figure (13–23M) because they count one tranche on one date; the lake's monthly total is the number a holder should use.

**"Throwing ideas at the wall" is a fair description of October.** Mysten Labs named Kevin Boon CEO on 10/23 and moved co-founder Evan Cheng to run a new AI unit; the month's announcements were a Google Cloud "Verifiable Agent Arbiter" for AI agents (10/06), Hashi BTCfi with "$500M committed" (10/08), a $30M Sui Foundation commitment to migrate the DATA Network (ex-Story) to Sui (10/12, which also closes the 10/08 open question — the migration is now announced), and a 40M-TPS off-chain stress-test headline. None of these shows up in TVL, stablecoins or fees yet, and the chain's institutional product, the Grayscale Sui Staking ETF (GSUI, NYSE Arca since 2026-02-18), holds roughly $23M. These are options, and the market is pricing them at zero in a risk-off tape.

**SUI versus SOL since the hold.** SUI $1.04 → $0.944 (−9.2%); SOL $108.68 → $101.85 (−6.3%). The hold is ~3 pp behind the 10/08 discipline trim after three weeks: small, but the direction is the one the consolidation thesis predicts, and the 10/08 file's stated reason for not trimming was that the desk's two prior SUI exits had lost to SOL. That calibration argument is now weaker than it was.

**Michael's top-15 claim, checked.** CoinGecko top 15 by capitalization today: BTC, ETH, USDT, BNB, XRP, USDC, SOL, TRX, FIGR_HELOC, HYPE, ZEC, DOGE, XMR, USDS, WBT. Strip the stablecoins and the wrapped/RWA tokens and the non-stable top ten is BTC, ETH, BNB, XRP, SOL, TRX, HYPE, ZEC, DOGE, XMR: two general-purpose L1s (ETH, SOL), two exchange chains (BNB, TRX), one payments rail (XRP), one perps venue (HYPE), two privacy coins (ZEC, XMR), one meme (DOGE). Perps and privacy are not a forecast; they are already three of the ten. SUI is #29 at $3.9B, behind AVAX, LTC and NEAR (#20). The "American Express" candidates Michael names (XRP #5 at $82B, BNB #4 at $98B) are 20–25x SUI's size. The claim that the list is settled is unfalsifiable over months, but the composition today supports his reading of where the capital went.

## Flow & positioning

The USDC outflow above is the flow read: −$100M in a week from a $375M base. Unlocks are the insider flow and they are a known seller at ~3.4% of float per month through January. `genkei news --asset SUI` is unusable for sentiment: 121 articles since 10/08 and the clusters are Sun Communities (NYSE: SUI) earnings, Pakistani gas-utility (SUI) load-shedding, a Paris fashion show and an actress; the one crypto item is a 24/7 Wall St "which holds up best if crypto falls 20%" piece. The ticker needs `gdelt_terms` the way PYTH got them (B-147); logged below. ETF flow for GSUI is not in the lake and the fund is too small to matter either way.

## Phase A — case for and case against

**Case for swapping into SOL now:**
1. The holder's conviction is gone, and the 10/08 hold was explicitly his conviction variant; the desk's own discipline call that day was trim 50% into SOL.
2. Every tracked metric moved toward its line in three weeks (TVL −7%, stablecoins −12%, price −9%), and the stablecoin drop was a single $100M USDC exit, which is how the 2026-08-05 exit file described the beginning of the end.
3. Supply: ~$130M of unlocks a month against a $3.9B cap and a $443M stablecoin base, through January. SOL has no equivalent schedule.
4. The 10/08 consolidation file already scored Sui "on the wrong side by every capital measure" and set a rule against mid-tier general-purpose L1s without a revenue line independent of TVL. Sui's fee base is $1.5–1.8M a month across seven protocols. The hold was the exception to the desk's own rule.
5. October's leadership reshuffle and announcement cadence are consistent with a team searching for a narrative, not executing one; the market is pricing those options at zero.
6. Swapping into SOL keeps the beta. The regret case if crypto rips is bounded to SUI-versus-SOL relative performance, which has favoured SOL since 10/08 and over the consolidation period.

**Case for holding on the lines:**
1. No line has fired. The 10/08 file was written precisely to stop the desk selling on a bad day, and a −9% three-week move in a −16% SOL tape is not a bad enough day to override it.
2. The desk's record: both prior SUI sells (6/02, 8/05) were followed by SUI beating SOL for a stretch. Selling at $0.94, near the 10/27 low of $0.911 with the position already −9% from the hold date, is the setup where the desk has been wrong twice.
3. The thesis is sentiment-shaped. "Perps and privacy" is a narrative that is 20% up today; the holder is updating on the same Twitter signal he distrusts in the PUMP session this morning.
4. Thin float plus deep drawdown is the configuration that produces 3x bounces in liquidity upcycles; the 10/08 risk list named exactly this.
5. Michael's cost basis is unknown. If the clips were taken sub-$1.50 and the loop at $0.82, the realised loss on a swap is large in percentage terms, and selling the loop collateral at $0.94 locks in the leverage cost too.

## Phase B — counter-thesis

**Against swapping.** The strongest case for being wrong is that this is the third discretionary SUI sell in five months and the first two lost to SOL. What is different now has to be stated precisely: the first two sells were made on the desk's fundamental read against a holder who still believed; this one is made with the holder's read and the desk's read aligned, with the metrics drifting the same way, and with a supply schedule that was not in the earlier files. If that is not enough to distinguish it, then the lines were never going to be honoured anyway, because a holder without conviction sells the next −8% candle. The second counter is the catalyst stack: Hashi, DATA, the AI arbiter, a 40M-TPS headline. If one of them converts into stablecoin supply or fees, the re-entry gate ($1.5B stables and $800M TVL, or $3M monthly fees) is the desk's admission that it was wrong, and it is measurable on the lake, so the exit is not a permanent avoid.

**Against holding.** Holding without conviction, at a line distance of 7% on stablecoins and 13% on price, with ~$130M of unlocks landing in each of the next two months, is a decision to let the next bad week make the decision. The lines will most likely fire within the horizon, after a further 10–15% loss, and the swap will then execute into SOL weakness in a crash, which the 10/08 file already named as its own worst case.

**What the desk may be overweighting:** Michael's sentiment. The data case for swapping (drift, unlocks, USDC exit) was mostly visible on 10/08 and the desk held then. The honest statement is that the desk's discipline answer has not changed since 10/08 (50% into SOL); what changed is that the conviction answer flipped, and the desk is now agreeing with the holder rather than restraining him.

**NEAR is the same question and is not answered here.** By Michael's framing NEAR (#20, $6.0B) is also outside the chosen list; the 2026-10-06 file holds a quarter starter with a second tranche gated on the November 1 Intents capture print. That print is three days away and is the scheduled test; a separate session after it decides NEAR.

## Conclusion

**Swap the SUI position into SOL as a thesis exit.** The desk's call is 100%, staged as half at market and half within a week on any bounce toward $1.00, executed before 2026-11-05 and after the Suilend loop is unwound. Confidence medium: the direction of every tracked metric, the unlock schedule, the chain's rank among the capital tables and the holder's own conviction all point one way; what holds confidence below high is the desk's two-for-two record of early SUI sells and the thin-float bounce risk.

- **Discipline (the desk's 10/08 answer, still valid):** 50% into SOL now, hold 50% on tightened lines: weekly close < $0.88, stablecoins < $0.43B, TVL < $450M, November fees < $1.2M; hard calendar 2027-01-08, sell the remainder then if the consolidation metrics still hold.
- **Conviction (Michael's side, and now the desk's recommendation):** 100% into SOL on the sequence above. No ZEC from these proceeds at market: ZEC is $1,347 today, outside the 9/17 file's $700–900 add zone; SOL is the destination.
- **Re-entry is measurable, not narrative:** Sui stablecoins > $1.5B with TVL > $800M, or watchlist fees > $3M in a month. Until one prints, SUI is `avoid`.

Top risks: (1) a liquidity upcycle lifts SUI 2–3x off a $3.9B base while SOL does 1.5x, and the swap costs 50–100 pp of relative performance, bounded by SOL still participating; (2) the swap executes into a SOL leg down from $102 and the grade looks bad in dollar terms even if right in relative terms; (3) a Hashi or DATA catalyst converts to real capital inside the horizon and the re-entry gate fires above the exit price.

Position-sizing: proceeds go to the SOL core position, not to a new tactical name. The tactical sleeve after this is PYTH, NEAR (pending 11/01), PUMP (held on lines), RENDER. **Housekeeping:** Michael owes the desk his blended SUI cost basis and whether the Suilend loop was unwound after 10/08, so the 2026-07-21 loop file can finally record realised inputs; the 10/08 open question on Story/DATA is answered by the 10/12 migration announcement and should be closed in `docs/research-questions.md`; SUI needs `gdelt_terms` so `genkei news` stops returning Sun Communities and Pakistani gas (B-147 pattern).

---

## Outcome (filled in by /reflect-decisions)

(reserved — pending)
