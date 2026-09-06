---
date: 2026-09-05
asset: "macro: crypto stablecoin-flow confirmation"
sleeve: macro-aware
horizon: months
action: add
confidence: medium
status: pending
trigger_reassessment: "Escalate beyond fuller approved-name accumulation only if stablecoin supply remains net-growing for 4+ additional weeks while BTC holds above $72k and breadth improves across non-weak-fundamental watchlist names. Slow or pause if aggregate stablecoin supply rolls back to net-contraction for 2+ weeks, BTC loses $72k, or breadth deteriorates while flows concentrate in one chain."
related:
  - decision: 2026-09-03-crypto-broad-accumulation-trigger-fire
  - decision: 2026-07-19-crypto-bottom-top50-accumulation-thesis
  - data: defillama.stablecoins
  - data: coingecko.market_data
supersedes: 2026-09-03-crypto-broad-accumulation-trigger-fire
---

# Crypto broad-market accumulation — stablecoin flow confirmed

## Frame

This file records the action after the 2026-09-03 staged-broadening decision's reassessment trigger fired. The runner recovery and backfill audit confirmed the missing flow signal: aggregate stablecoin supply bottomed the week of Aug 3, then printed four consecutive weekly gains, with Ethereum flipping to +$1.44B over 30 days. That satisfies the prior file's "stablecoin supply confirms net-growth for 2+ weeks" clause and forces a fresh decision on whether staged broadening should become fuller accumulation.

## Flow & positioning

The repaired `defillama.stablecoins` history changes the posture from price-only confirmation to price plus flow confirmation. BTC had already reclaimed the >$72k trigger line by the 2026-09-02 external read, and the backfilled stablecoin series now says dry powder rebuilt for more than the two-week threshold. The useful signal is not "buy every token"; it is that the liquidity constraint which justified small, staged additions is no longer the active blocker.

## Phase A — case for and case against

**Case for fuller accumulation:** the July framework deliberately made aggregate stablecoin net-growth the decisive broadening signal, and the repaired lake now shows more than the required duration. Ignoring the trigger after restoring the data would turn a recorded forward commitment into a discretionary suggestion. The flow confirmation also reduces the main objection to the September 3 price-trigger file, which was that BTC's breakout might be running without dry-powder support.

**Case for restraint:** the outage audit does not select names, weights, or execution timing by itself. It also does not rehabilitate assets whose own decision files say sell, avoid, or wait for asset-specific reopen triggers. Four weeks of aggregate growth can still mask narrow rotation if breadth fails to improve, so the desk should not use the macro signal to override weaker micro decisions.

## Phase B — counter-thesis

The strongest case this escalation is wrong is that the stablecoin rebound is only a short post-backfill bounce or chain-specific rotation, not durable capital returning to the complex. The guardrail is to escalate sizing only inside already-approved assets and keep watching whether supply remains net-growing while breadth improves. If stablecoin supply rolls back to net-contraction for 2+ weeks, BTC loses $72k, or breadth deteriorates while flows concentrate, slow or pause the fuller-accumulation posture.

## Conclusion

**Recommendation: move from staged active broadening to fuller accumulation, but only inside assets already cleared by their own live decision files.** The macro gate now has both legs: BTC broke the price line and stablecoin supply confirmed net-growth for longer than the two-week threshold. Deploy reserved broadening capital more deliberately into approved quality names, with BTC/SOL and other positive-live-decision assets governed by their own sizing rules; do not force weak-fundamental tail names into buys. Sleeve is macro-aware, horizon is months, confidence is medium because the trigger is clean but the breadth/name-selection work remains asset-level.

---

## Outcome (filled in by /reflect-decisions)

(reserved — pending)
