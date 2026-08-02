---
date: 2026-07-27
asset: HYPE
sleeve: crypto-tactical
horizon: months
action: buy
reflection_benchmark:
  type: destination_basket
  label: SOL alternative (100%)
  assets:
    - ticker: SOL
      weight: 1.0
confidence: low
status: inactive
activation_condition: "Activate only if the Coinbase HYPE limit order fills: replace `date` with the actual fill date, flip `status` to `pending`, and use that fill date as the HYPE-vs-SOL reflection baseline."
trigger_reassessment: "After activation, add only if HYPE holds the actual fill entry zone without a liquidity break; exit/reduce if spot loses $45 after fill, if Hyperliquid open interest or volume share materially reverses for 2+ consecutive weeks, or if SOL 30d relative strength beats HYPE by >15pp from the fill-date baseline."
related:
  - decision: 2026-07-26-render-exit-into-sol
  - research-question: render-execution-override-2026-07-27
  - data: coingecko.market_data
---

# Hyperliquid (HYPE) - inactive starter-capped initiation after RENDER exit

## Frame

This record governs the HYPE destination created by the RENDER execution override. The 2026-07-26 RENDER decision recommended selling the full RENDER stub into SOL. That call was initially overridden, then closed on 2026-07-28 when the full RENDER position was sold at $1.40 and proceeds moved to USDC for a Coinbase limit buy in HYPE at $54.25. The unresolved governance gap was that the scratch log referenced HYPE add/exit triggers and a HYPE-vs-SOL reflection, but no decision file existed for `/reflect-decisions` to walk.

This is therefore an execution-governance entry, not a reconstructed full `/research` session. It records the intended position, the benchmark, and the reassessment triggers so the destination is auditable if the order fills. The frontmatter remains `status: inactive` until then; activation requires replacing the record date with the actual fill date so `/reflect-decisions` does not grade a nonexistent or later-starting exposure.

## Coverage

HYPE is configured as a CoinGecko-backed price-only target in `src/genkei/data/watchlists.yml` with `coingecko_id: hyperliquid`. That gives `genkei prices --ticker HYPE` and the CoinGecko daily ingest a route for outcome/reflection coverage without enrolling HYPE in the recurring tactical signal stack before the starter position earns that promotion.

## Decision

**Initiate HYPE at starter size only if the Coinbase limit order fills near $54.25.** This is the sanctioned second-ranked destination from the RENDER exit discussion, chosen instead of the originally logged SOL destination after the "too early?" probability walk put the joint early-entry risk in the ~5-10% range. The position is capped because HYPE is a high-beta, still-young L1/perps venue whose upside case is real but timing-sensitive. Until a fill occurs, this is not an active buy decision and should not enter the reflection queue.

The grading benchmark is explicit once activated: compare HYPE against the SOL alternative from the actual HYPE fill date. If HYPE outruns SOL over the horizon, the desk was right to accept the added timing and platform risk rather than defaulting to core-quality SOL. If SOL outruns HYPE, the RENDER exit should have followed the original destination.

## Add And Exit Triggers

Add only after the limit order fills at starter size and market structure stays intact around the fill entry zone. No averaging up just because HYPE rallies; a larger allocation requires a fresh decision or a clear improvement in Hyperliquid usage/liquidity that is not merely price beta.

Exit or reduce if HYPE loses $45 after fill, if Hyperliquid open interest or volume share materially reverses for 2+ consecutive weeks, or if SOL 30d relative strength beats HYPE by more than 15pp from the fill-date baseline. Those triggers intentionally separate "starter thesis is intact but volatile" from "the SOL alternative was the better destination."

## Conclusion

**Recommendation: inactive until filled; if filled, starter-capped buy graded vs SOL from the actual fill date.** Sleeve: crypto-tactical. Horizon: months. Confidence: low, because the decision is explicitly an early entry after acknowledging a non-trivial "too early" risk. Position-sizing implication: keep it starter-sized until the usage/liquidity case strengthens enough to justify a separate add decision.

---

## Outcome (filled in by /reflect-decisions)

(reserved - inactive until fill)
