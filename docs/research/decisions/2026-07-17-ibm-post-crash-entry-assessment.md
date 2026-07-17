---
date: 2026-07-17
asset: IBM
sleeve: equity-core
horizon: months
action: avoid
confidence: medium
status: pending
trigger_reassessment: "IBM's full Q2'26 earnings call on 2026-07-22 is the hinge. Flip to a starter BUY if management reaffirms full-year guidance (>5% cc revenue, software >10% FY, +$1B FCF) with a specific, credible H2 software-reacceleration path AND frames the mainframe/memory-driven miss as transient. Stay AVOID / re-rate toward value-trap if full-year guidance or the software FY target is cut, or the AI-capex rotation is described as structural share loss. Price context: $211 (Jul 15 low) as near-term floor, ~$237 200-DMA as first resistance."
---

# IBM — post-crash entry assessment (should I start a position after the ~30% drop, or wait?)

## Frame

Should I initiate an equity-core position in IBM after it fell from a ~$329 all-time high (Jun 2) to ~$212, including a record −25.2% single day on Jul 14, 2026 — or hold off for more positive news? IBM is **not** a pre-existing holding; this is a start-or-wait timing question, and the sleeve it would inform is equity-core (buy-and-hold, income-oriented). Horizon for grading: months (the "wait" call resolves as the Q2 stumble proves transient or structural over the next print or two). **What would change the answer:** the Jul 22 full earnings call — specifically whether management credibly defends the *maintained* full-year software (>10%) and FCF (+$1B) guidance, or walks it back. First logged IBM decision; no prior call to supersede. IBM was added to the watchlist/lake this session (yahoo candles + SEC facts) so this decision is gradeable and IBM now carries forward coverage.

## Macro context

Regime is **risk_on** (`genkei macro-regime`, 2026-07-14; 4/4 inputs): VIX 16.5 (benign), HY OAS 2.72% (credit tight, no recession pricing), USD 120.5, DGS10 4.55% (Δ30d +0.07). Macro is *not* the driver here — this is an idiosyncratic, single-name earnings event, not a regime-led selloff (the tape is friendly to equity longs). The only macro watch-item for a leveraged, long-duration name is the 10-year; at 4.55% it's supportive, and a break >5.0% would add multiple-compression pressure on top of the company-specific story. Net: macro gives no cover for the drop and no reason to rush in — the catalyst is entirely IBM-specific.

## Fundamentals

The catalyst (Jul 14 preliminary Q2'26 8-K warning — the drop pre-dates the full 10-Q, so it is **not** yet in SEC XBRL; figures below are from IBM's preannouncement/press coverage, with scale + balance sheet grounded in `sec.facts`):

- **The miss:** Q2'26 revenue **$17.2B (+1% YoY)** vs ~$17.86–17.9B consensus (~$700M light); adjusted EPS **$2.93** vs $3.02 expected. By segment: **Infrastructure −7%** (mainframe z17 / Z-systems shortfall + the attached Transaction Processing software stack), **Software +5%** (vs a double-digit Street expectation — the load-bearing disappointment), **Consulting flat** (+1% cc).
- **Management's framing:** CEO Krishna attributed the miss to clients diverting capital toward AI servers / storage / memory ahead of a memory-price spike (global memory shortage) late in June, crowding out mainframe + software spend, plus a worse-than-guided z17 cycle. Critically, IBM **maintained** (did not cut) full-year guidance: **>5% cc revenue growth, software >10% FY, +$1B YoY FCF**. H1 FCF was ~$4.8B. So management is calling Q2 a timing air-pocket, not a structural downgrade — but +5% Q2 software vs a >10% FY target means H2 has to reaccelerate hard for the guide to hold.
- **Scale + balance sheet** (`sec.facts`, through FY2024/latest-landed): ~$15.8B quarterly revenue (~$62–63B annual); long-term debt **~$50B** vs cash ~$14B → **net debt ~$36B** (leveraged, post-Red Hat/Kyndryl — a different profile from the net-cash mega-cap compounders in the core sleeve). Gross profit ~57% — high-margin mix.
- **Valuation + income** (web + `yahoo.candles`): ~$212 now; forward P/E **~17.9** (below the 10-yr median ~21.3), GF Value ~$239 (fairly-to-slightly-cheap). Dividend **$6.76/yr → ~3.1% yield, 25 consecutive years without a cut**; ~$6B/yr dividend cost against ~$12B+ normalized FCF ≈ ~2× covered — safe, but the leverage leaves less cushion than a net-cash name.
- **Price action** (`genkei prices --ticker IBM`, grounded in our lake): $290.23 (Jul 13) → **$217.07 (Jul 14, −25.2%, 67.4M shares** vs ~5–8M normal) → $211.20 (Jul 15) → $219.05 (Jul 16) → ~$212 (Jul 17). Notably the stock was **already rolling over before the warning** — down from the ~$329 Jun-2 ATH and ~$306 (Jul 7) through $290 (Jul 13) — so some money was leaving ahead of the print.

## Flow & positioning

**Partially dark for IBM** — it was only added to the watchlist this session, so `sec.form4` (insiders) and GDELT news matching have no IBM history yet because those collectors are watchlist-driven from the add date forward, and the correlator has no IBM signal events. `13F` crowding is different: the Form 13F pipeline is filer-driven, and IBM's watchlist CUSIP (`459200101`) can be used to query already-normalized historical holdings via `genkei crowding --ticker IBM --all-periods --min-holders 1`. I am therefore treating institutional positioning as **unqueried, not structurally unavailable**; it should be checked before any Jul 22 starter-buy decision, but it is not evidence for or against this call as written. The one positioning read I do have is the tape itself — the pre-warning roll-over (−12% from the ATH before Jul 14) plus the record 67M-share down day says distribution was already underway and the miss confirmed it. Analyst positioning post-miss: 15 Buy / 7 Hold / 1 Sell (23 covering), but targets are being cut — JPMorgan $291→$250, HSBC downgraded to Reduce/$191 — i.e. the sell-side is bifurcating, not uniformly bullish.

## Phase A — case for and case against

**Bull case (this is the buying opportunity):**
1. **Valuation reset to fair/cheap** — forward P/E ~17.9 vs 10-yr median ~21.3; a ~35% haircut from the ATH on a single quarter has priced in a lot of bad news.
2. **Income anchor** — 3.1% yield, 25-year no-cut streak, ~2× FCF-covered; a genuine floor for a Buffett-style income holding and a paid-to-wait dynamic.
3. **Management maintained full-year guidance** (software >10% FY, +$1B FCF) and frames the miss as transient (memory-shortage capex diversion + mainframe lumpiness) — both plausibly self-correcting; z17 cycles are lumpy and recover, and memory-price normalization removes the crowding-out.
4. **$12.5B gen-AI book of business** (largest among non-hyperscalers) — the AI story that supposedly hurt this quarter is also a multi-year tailwind IBM is monetizing.

**Bear case (this is a value trap / wait):**
1. **The software line is the whole thesis, and it cracked** — +5% vs a double-digit expectation. IBM's entire re-rating from "melting ice cube" to "growth" rests on software-led acceleration (Red Hat/automation/data). A single soft quarter there is exactly the datum that breaks the narrative, and the market voted −25% on it.
2. **The AI-capex rotation could be structural, not transient** — if clients are permanently redirecting IT budget from IBM software/mainframe toward building their own AI infra, "software >10% FY" is not credible and guidance gets cut next.
3. **Guidance maintained ≠ guidance believed** — management kept the full-year target, but +5% Q2 software requires an implausible H2 snap-back to hit >10% FY. The market is pricing a walk-back on Jul 22.
4. **Leverage limits the margin of safety** — ~$36B net debt means less balance-sheet cushion than a net-cash compounder if the turnaround stalls; the pre-warning roll-over suggests informed sellers were already leaving.

## Phase B — counter-thesis

The strongest case that **"wait" is the wrong call** (i.e. I should buy now): the biggest, most durable losses in quality names are made buying the capitulation day, and by waiting for the Jul 22 "all-clear" I guarantee I pay up — if management reaffirms guidance credibly, IBM likely gaps up 8–12% off $212 and the discount I'm being paid to wait for evaporates. The signal I'm most likely *overweighting* is the software +5% print: one quarter distorted by a genuinely transient, industry-wide memory shortage (which IBM named specifically) is thin evidence of structural share loss, and I may be pattern-matching "software decel = thesis broken" too mechanically (the same over-reaction the −25% tape embodies). A smart bull at lunch says: *"You have a 25-year dividend aristocrat at a below-median multiple, management reaffirmed the guide, the culprit is a dated memory shortage that's already reversing, and you're waiting to pay 10% more for 'confirmation' — that's not discipline, it's recency bias."* **Why I still land on wait:** the counter-thesis is real but asymmetric in *knowability*, not just probability. The single datum that adjudicates transient-vs-structural (management's defense of the FY software path + the mainframe explanation) arrives in **5 days**, on Jul 22. Starting a full position before a known, imminent, thesis-determining catalyst — on a name where the core growth engine just missed by half — is avoidable blindness, and the 3.1% dividend means the cost of waiting one week is trivial. The trade-off is explicitly "give up some of the bounce for a large reduction in the odds of catching a value trap," which for a buy-and-hold sleeve (not a trader) is the right side.

## Conclusion

**Recommendation: HOLD OFF / do not initiate at ~$212 — wait for the Jul 22 earnings call, then act on what guidance shows.** This is a *timing* hold, not a structural rejection of IBM: the drop has reset valuation to fair (forward P/E ~17.9) with a well-covered 3.1% dividend, which is a legitimately attractive setup — but the one piece of information that decides whether this is a cyclical air-pocket (buy) or a structural crack in the software thesis (avoid) lands in 5 days, and buying blind ahead of it is the avoidable mistake. **Sleeve:** equity-core (income/turnaround profile — a different, lower-quality-of-growth, more-leveraged animal than the AAPL/MSFT/GOOGL/AMZN compounders, so it warrants a *smaller* slot even if the thesis confirms). **Horizon:** months. **Confidence:** medium — high conviction in the *process* call (wait for the imminent catalyst), genuinely two-sided on the *directional* outcome, and consistent with this repo's calibration of not over-claiming on a single dramatic datum (here, the −25% tape).

**Top risks (counter-thesis distilled):** (1) I pay up — if Jul 22 reaffirms guidance credibly, IBM gaps up and waiting cost me 8–12%; (2) the memory-shortage/mainframe framing really is transient and I'm over-reading one soft software print; (3) conversely, if I *do* buy on a Jul 22 "reaffirm" and it's management defending an undeliverable target, I catch the value trap one week later.

**Position-sizing:** zero today. On a Jul 22 confirmation, a **starter** (roughly half a normal core-initiation slot given the leverage + lower growth quality), scaling only as software re-acceleration actually prints — not on management's word alone. **Trigger for reassessment:** see frontmatter — the Jul 22 call is the hinge (reaffirmed-credible → starter buy; cut/structural → stay out), with $211 as the near-term floor and ~$237 (200-DMA) as first resistance.

---

## Outcome (filled in by /reflect-decisions)

(reserved — pending)
