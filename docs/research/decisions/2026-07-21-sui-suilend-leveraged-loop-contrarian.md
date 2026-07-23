---
date: 2026-07-21
asset: SUI
sleeve: crypto-tactical
horizon: months
action: add
confidence: medium
status: pending
position_type: leveraged_loop
trigger_reassessment: "SUI reclaims $1.00 (thesis realized → unwind loop, repay suiUSDT) OR SUI falls to the loop's liquidation price (thesis broken → forced exit) OR sSUI de-pegs from SUI by >2% (structural risk → unwind)"
related:
  - decision: 2026-05-20-sui-position-assessment
  - decision: 2026-06-02-sui-rotation-into-eth-sol
  - decision: 2026-07-19-crypto-bottom-top50-accumulation-thesis
  - decision: 2026-07-19-suig-sui-treasury-vehicle-assessment
  - data: coingecko.market_data
  - data: defillama.chain_tvl
tags:
  - contrarian
  - leverage
  - user-directed
---

# SUI — Suilend leveraged loop (contrarian, user-directed)

## Frame

This entry documents a **user-directed contrarian bet** that runs *against* the desk's standing research on SUI. The position: deposit **sSUI** (Spring Sui, Suilend's liquid-staking token) on **suilend.fi**, borrow **suiUSDT** against it, and loop the borrowed stable back into more deposited sSUI — a recursive leverage-long on SUI that also earns the sSUI staking yield. Plan: **unwind and repay the suiUSDT loan when SUI reclaims ~$1.00.** Sleeve: crypto-tactical (turnover-eligible). Horizon: **months** — the explicit target is **year-end 2026**. The user's thesis: *SUI has bottomed and will trade sideways-to-slightly-up into year-end*, so a leveraged long captures an amplified mean-reversion while staking yield pays part of the carry. What would change the answer: SUI reclaiming $1.00 (win → unwind), SUI falling to the loop's liquidation price (loss → forced exit), or an sSUI/SUI de-peg.

**Loop parameters (from user, 2026-07-21):**
- Entry SUI price: **$0.76**
- Collateral: **850 sSUI ≈ $684**
- Borrowed: **$200 suiUSDT** → current LTV ≈ **29%** (200 ÷ 684); equity ≈ **$484**; effective leverage ≈ **1.4×** on equity. Modest, not aggressive.
- Liquidation SUI price: **~$0.28 (desk estimate: 29% LTV at a ~80% liquidation threshold → ‑63% from entry). NEEDS CONFIRMATION.** User-reported "$667" does not reconcile as a SUI liquidation price against a $200 borrow (at $0.667, collateral is still ~$600 vs $200 debt = 33% LTV, far from any liquidation threshold). Confirm the actual figure from Suilend's UI — it should be a sub-$0.76 SUI price.
- Net carry: **POSITIVE.** Suilend sSUI **supply APR +2.44%** *plus* sSUI's embedded native SUI staking yield, vs suiUSDT **borrow APR −1.43%** → net spread ≈ **+1.0%** on the looped portion *before* the embedded staking yield. The loop **earns** while SUI trades flat rather than bleeding — which strengthens the sideways thesis.

## Macro context

Couldn't pull live macro this session (local shell lacks `GENKEI_DATABASE_URL`; lake is on the Beelink). Leaning on the last logged regime call (2026-07-19 sessions): **constructive/benign risk-on** — the macro backdrop is *not* the reason to be defensive on SUI. This bet is a bet on SUI's *idiosyncratic* mean-reversion inside that supportive macro, plus leverage. If macro flips risk-off, a leveraged alt-L1 loop is exactly the wrong place to be — re-pull macro at the next check-in.

## Fundamentals

The desk's read as of the most recent SUI-adjacent sessions: SUI chain **USD TVL ≈ −80% and still falling** with no bottoming signal (2026-07-19 SUIG assessment), and SUI sits in the *deteriorating* half of the top-50 dispersion (2026-07-19 crypto-bottom thesis explicitly named "SUI/Aptos-like names whose TVL is still falling" as the tail to avoid). No Sui-native protocol TVL or unlock schedule in the lake yet (gap flagged since 2026-05-20). So on the desk's own fundamentals, this is **buying — and levering — a knife the research says is still falling**. The contrarian premise is that price/TVL has now discounted enough that forward returns are asymmetric to the upside; that is a *price* call, not a fundamentals call, and the entry deliberately overrides the fundamental signal.

## Flow & positioning

Research counter-signal: **aggregate stablecoin supply still net-contracting** — dry powder leaving the alt complex, the classic "bottom not yet confirmed" tell (2026-07-19). SOL was the one alt showing *confirming* inflow; SUI was not. This position is contrarian to positioning/flow as well as fundamentals — the bet is that flow turns before the liquidation price is hit.

## Phase A — case for and case against

**Bull case (the user's thesis):**
1. **Bottom is in.** −73%+ multi-year drawdown into benign macro is historically where accumulation gets paid; the user judges downside from here as limited relative to upside.
2. **Sideways-to-up into year-end** is enough — the loop doesn't need a V-recovery; even a grind back toward $1.00 realizes the target.
3. **Leverage amplifies** the mean-reversion the user expects.
4. **Carry is net positive** — Suilend's 2.44% sSUI supply APR plus sSUI's embedded native staking yield exceed the 1.43% suiUSDT borrow cost (~+1% spread before staking), so the loop *earns* while waiting rather than costing money to hold.

**Bear case (the desk's standing research):**
1. **No bottoming signal yet** — TVL −80% and still falling; the desk's calls were AVOID/trim/"don't add under any bull scenario short of an SUIG insider buy cluster" (2026-05-20, 2026-06-02, 2026-07-19).
2. **Dry powder is leaving alts** — stablecoin contraction says flow hasn't turned.
3. **Leverage stacks avoidable risk on a knife** — the exact critique the 2026-07-19 SUIG session made about a *leveraged* SUI proxy, here applied with real liquidation mechanics.
4. **Carry is thin, not a cushion** — net positive (~+1% spread plus embedded staking) means the loop earns modestly while flat, but that yield is far too small to offset a meaningful adverse price move. Don't mistake positive carry for downside protection.

## Phase B — counter-thesis (the leverage-specific failure mode)

The dangerous part isn't being wrong on direction — it's being **right on direction and still losing**. A loop can be liquidated on a **downside wick** below the liquidation price even if SUI later recovers to $1.00; leverage removes the ability to "just hold through it" that an unlevered spot bag has. Three concrete ways this bet loses while the *thesis* looks fine:
1. **Liquidation wick** — a brief flush to the liquidation price force-closes the loop at the worst moment; SUI then recovers without you.
2. **Opportunity cost, not bleed** — carry here is net *positive* (~+1% plus embedded staking), so time isn't eroding the position while flat; the real cost of a long sideways grind is capital tied up in a liquidation-exposed loop earning mid-single-digits when it could be deployed elsewhere.
3. **sSUI de-peg / Suilend smart-contract risk** — the collateral is a wrapped LST on a lending protocol; a de-peg or exploit is a total-loss vector unrelated to SUI's price. Hence the frontmatter's >2% de-peg trigger.

The frontmatter `trigger_reassessment` encodes the exits: reclaim $1.00 → unwind (win); liquidation price → forced exit (loss); sSUI de-peg → unwind (structural).

## Conclusion

**Recommendation (user-directed, contrarian): OPEN the leveraged sSUI→suiUSDT→sSUI loop on Suilend, targeting an unwind when SUI reclaims ~$1.00 by year-end 2026.** This is logged **explicitly against** the desk's research, which says AVOID/trim and specifically warns against a *leveraged* SUI bet before any bottoming signal. **Sleeve:** crypto-tactical. **Horizon:** months (year-end target). **Confidence: medium** — this reflects the *user's* stated conviction that SUI has bottomed, not the desk's model, which would put confidence low; the disagreement is the whole point of logging it.

**Top risks:** (1) liquidation wick force-closes a directionally-correct bet; (2) the modest positive carry (~+1% plus staking) is too thin to cushion any real drawdown — it earns while flat but won't save the position if SUI slides toward the liq price; (3) sSUI de-peg / Suilend contract risk — a collateral-side total-loss vector independent of SUI price.

**Position-sizing implication:** keep this *small and speculative* — the appropriate size for a levered, against-the-research bet on a still-falling asset is well below a normal tactical add. Size such that a full liquidation is an acceptable loss.

**Trigger conditions for reassessment (see frontmatter):** SUI reclaims $1.00 → unwind and repay (thesis realized); SUI falls to the loop's liquidation price → forced exit (thesis broken); sSUI de-pegs >2% → unwind (structural).

**Reflection-grading note for `/reflect-decisions`:** this is a **leveraged, carry-bearing** position — spot SUI return vs the BTC benchmark is only a *directional* proxy and will **understate** the real P&L (leverage amplifies the move; borrow cost and staking yield net against it; a liquidation wick can zero it regardless of end-of-horizon price). When grading, note (a) did SUI reclaim $1.00 within horizon, (b) was the position liquidated at any point in the window (check the interim low vs the confirmed liquidation price), and (c) directional call vs BTC — but flag that true P&L needs the leverage + net-carry inputs above, not just spot return.

---

## Outcome (filled in by /reflect-decisions)

(reserved — pending)
