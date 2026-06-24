---
date: 2026-06-24
asset: CYBL
sleeve: equity-core
horizon: months
confidence: medium
status: pending
trigger_reassessment: "CYBL files a reverse split + Nasdaq/NYSE uplisting OR reports a QoQ decline in shares outstanding (dilution reversing) OR announces K8 receivable/interpleader cash release that materially reduces financing need OR a war-driven pop holds above ~$0.01 for >2 weeks"
---

# CYBL (Cyberlux Corp) — buy / hold / harvest-the-loss assessment

> **Sleeve caveat:** tagged `equity-core` only because the validator's sleeve enum is asset-class-based and CYBL is an equity. This position is the *opposite* of the fund's equity-core mandate (long-only, Buffett-quality, buy-and-hold). It is a speculative OTC micro-cap held at a loss and is explicitly **outside stated strategy** — the user flagged as much. Treat any "core" connotation as inapplicable.

## Frame

CYBL is **Cyberlux Corporation** (Durham, NC; OTC Pink) — a defense-tech micro-cap with three units: Unmanned Aircraft Solutions (drones), Datron Military Communications, and Special Activities. The user holds it at a loss, recalls it approaching ~$1 in 2021, and notes it "pops on war news." The question is directional: **buy (add), hold, or sell to harvest the capital loss for 2026 taxes.** This informs no fund sleeve — it's a one-off assessment of a legacy speculative position. Horizon for the *thesis* is months (does the structural picture support recovery?); the *tax action* is near-term (2026 tax year). **What would change the answer:** evidence that dilution is reversing (share count falling), a credible uplisting/reverse-split catalyst, or a K8/interpleader cash release that materially reduces financing pressure — any would convert "dead, diluting paper" into "lottery ticket worth holding."

**Data-lake caveat (important):** CYBL is **not in the Genkei watchlist or lake** (`genkei prices --ticker CYBL` → "not found"; confirmed absent from `watchlists.yml`). The lake has zero price/fundamentals/insider coverage for it. Every CYBL-specific number below comes from **external web sources** (company press releases, OTC aggregators) — i.e., self-reported OTC-Pink figures with no analyst or institutional verification. Confidence is capped accordingly. The only lake-backed input is the macro spine.

## Macro context

`genkei macro-regime` (lake, latest 2026-06-21): **risk_on, 4/4 inputs** — DGS10 4.51% (Δ30d −0.05), HY OAS 2.66% (tight; no credit stress), VIX 16.78 (benign vol), USD 120.4 (Δ30d +1.1, firming). A benign risk-on tape is mildly supportive of speculative small-caps in general. **But this macro read is largely orthogonal to CYBL's actual driver.** CYBL trades on *geopolitical conflict intensity* (drone/defense war-news catalysts), which none of the lake's macro series track. The honest macro statement is: the regime isn't a headwind, but it isn't the thing that moves this stock either.

## Fundamentals

The genuinely unusual part: **CYBL has a real defense business**, not a hollow shell; it has scaled materially since 2021, but the recent revenue trend is contracting and lumpy.
- **FY2025 revenue $31.4M** (down from **$48.4M in FY2024**), gross margin expanded to **~45%**; completed delivery of all **2,000 K8 UAS** under a **$78.9M DoD contract**; reported **$18.1M backlog** entering 2H25 (company releases via StockTitan / BusinessWire / cyberlux.com).
- 2021 baseline: $8.1M revenue, **$1.9M net income** (was profitable then).

But the equity structure tells the opposite story:
- **Price ~$0.0016** ("trip-zero" sub-penny) as of 2026-06-17; market cap **~$12–15M** (Yahoo / stockanalysis.com / macroaxis).
- **~7.18 billion shares outstanding.**
- **Recent quarter: Q1 2026 revenue $1.8M vs $5.1M in Q1 2025; net loss −$6.017M; net cash used in operating activities −$1.502M** (losses + burn), with a third-party model putting **~47% odds of financial distress**. The quarter was reportedly constrained by the Texas receiver / EDVA interpleader dispute, with **~$26.5M of accounts receivable** largely tied to K8 invoices awaiting settlement and a May 11, 2026 federal order terminating the receiver from the interpleader; that is a real cash-release catalyst if it resolves on schedule. (One source cited a +$2.85M positive-earnings period — likely a different/one-off window; OTC-Pink reporting is inconsistent and I could not reconcile it. Flagged, not relied upon.)
- **Dilution trajectory is the disease:** ~5.8B shares → 5.1B after a PR'd cancellation of 700M "ghost shares" (May 2022), with authorized cut 8.75B → 7.0B "to protect shareholders." Yet outstanding is **now 7.18B — above the 2022 authorized cap**, meaning authorized was subsequently *re-raised* and dilution continued (~+40% shares since 2022). The investor-friendly "reductions" were headlines; the trend is more shares.

The core fact: a company doing $31M of revenue at 45% gross margin "should" be worth more than a ~$12M market cap — **unless the share count keeps outrunning the business.** That gap is the market pricing relentless dilution.

## Flow & positioning

No lake coverage (no insider/13F/institutional data for an OTC Pink name). External read: CYBL is a **retail-driven war-news momentum vehicle**. The clearest documented instance — **Oct 2022**, a Fox News segment (drone-warfare expert Brett Velicovic) urging US drone tech "like Cyberlux" for Ukraine — is exactly the kind of headline that spikes the ticker. The tell is what *didn't* happen: across years of continuous war news (Ukraine 2022→ongoing, Middle East escalations), the stock is still on the floor at $0.0016. **The pops are exit liquidity, not durable value** — momentum traders supply volume that long-term holders and dilution absorb. There is no positioning signal here worth acting on; there is a behavioral pattern (spike → round-trip) worth *not* getting trapped by.

## Phase A — case for and case against

**Bull case (steelmanned):**
1. Real revenue, real DoD contracts, 45% gross margin, reported backlog — genuinely rare for a sub-penny stock; this is an operating defense business, not vapor.
2. Optionality on a **corporate action**: a reverse split + Nasdaq uplisting would re-rate the equity off the sub-penny floor and could be a multiple-bagger from $0.0016 *if* paired with a clean cap table.
3. **War-catalyst convexity**: in a major conflict escalation, drone-defense micro-caps can spike several-hundred percent on headlines; a small position is a cheap lottery ticket on that.
4. Downside is already mostly realized — the user holds at a loss; the remaining dollar value is tiny.

**Bear case:**
1. **Dilution overwhelms growth.** Defense contracts are working-capital-hungry (you fund production before getting paid); CYBL funds that by issuing shares. Even when revenue scales over multi-year windows, per-share value doesn't. 7.18B shares and climbing.
2. **Sub-penny "trip zero" is structurally dead money** — at $0.0016 the equity is an option on a corporate action. The no-reverse-split policy reportedly adopted in July 2021 and reaffirmed in the Dec 2024 leadership Q&A means the absence of a reverse-split filing before mid-2026 is not, by itself, bearish evidence; the bear point is that no clean recap/uplist filing is visible today while dilution and burn remain live.
3. **Cash burn + ~47% distress odds** → the financing need that drives more dilution or toxic convertibles is ongoing, though this is the most caveated bear point: Q1 2026 cash use was tied to constrained shipments during the receiver/interpleader dispute, and release of the K8 receivables would reduce near-term financing pressure.
4. **War-news pops round-trip** — years of catalysts have not lifted the floor.
5. **OTC Pink** — minimal disclosure, no audit reliability, no institutional ownership; you cannot trust the financials you're underwriting.

## Phase B — counter-thesis

The strongest case for being *wrong* (i.e., for holding/buying): **what if the business inflects faster than dilution, and management finally recaps?** A defense micro-cap that keeps winning DoD/allied contracts into a rising-defense-spend, multi-conflict world could, with a reverse split + uplisting, re-rate enormously — and from $0.0016 the percentage upside on a true catalyst is asymmetric. The desk's calibration history warns me here: its one decision that aged poorly (ValueAct/CRM) *underweighted* an upside narrative. So I am deliberately not dismissing the lottery convexity.

But the counter-thesis has to be specific, and the specifics cut against the hold:
- The catalyst (reverse split + uplist, or sustained share-count *reduction*) is **observable and absent** today, but the timing needs nuance: CYBL's 2021 no-reverse-split policy ran up to five years and the Dec 2024 leadership Q&A said reverse-split plans had not changed, so the lack of a filing before the June 2026 decision date is expected rather than independently bearish. The bearish evidence is narrower: authorized shares appear to have been re-raised, dilution kept going, and there is still no filed clean recap/uplist plan as the policy window expires.
- Even in the bull scenario, a recapitalization of a 7B-share, cash-burning OTC name typically crushes *existing* holders (reverse split + fresh raise) — the re-rate often accrues to new capital, not the bag held since the top.
- **What would flip me to "hold the lottery ticket":** a reverse-split + Nasdaq/NYSE uplisting filing, OR a reported QoQ *decline* in shares outstanding, OR material K8/interpleader cash release that reduces near-term financing need, OR a war-driven pop that holds above ~$0.01 for >2 weeks (the frontmatter trigger). None are present today.

## Conclusion

**Recommendation: do NOT buy/add; harvest the tax loss (sell) as the base case.** Adding to a diluting sub-penny stock is throwing good money after bad — averaging down here funds the dilution. Between *hold* and *harvest*, the rational portfolio move is to **realize the capital loss for tax-year 2026**: the loss is a certain, usable asset (offset realized gains; up to $3,000 against ordinary income; carry the remainder forward), whereas the hold is a structurally-compromised lottery ticket whose one path to value (a recap/uplist) has no visible filing today and would still have to outrun dilution. The expected value of "certain tax asset now" exceeds "speculative re-rate fought by dilution."

**Sleeve:** none in practice (speculative; outside the equity-core mandate). **Horizon:** months for the thesis; the tax action is 2026. **Confidence:** medium — the *structural* read (dilution + sub-penny + burn = poor risk/reward) is clear and is the kind of skeptical call the desk has gotten right, but it rests entirely on unverified OTC-Pink external data with no lake backing, which caps confidence below high.

**Top risks to this call (what would make harvesting the wrong move):** (1) the no-reverse-split policy expiring and a near-term reverse-split + uplisting filing re-rating the equity before a 31-day re-entry window closes; (2) EDVA interpleader / K8 receivable settlement releasing cash sooner than expected and reducing dilution risk during the wash-sale window; (3) a major-conflict escalation driving a war-news pop that *holds*; (4) the financials being better than the OTC-Pink snapshot suggests (the unreconciled +$2.85M earnings figure).

**Position-sizing / action:** sell to crystallize the loss. *If* the user wants to keep convexity on the war-news/uplist thesis, the disciplined way is a **tiny** re-entry **after 31 days** (to preserve the loss under the **wash-sale rule** — buying substantially identical shares within 30 days before/after the sale disallows the loss), sized as money they are emotionally prepared to see go to zero. Base case remains: harvest and move on.

**Not tax advice.** The mechanics (basis, holding period, wash-sale, how much offsets your 2026 gains) depend on the user's specific records and other positions — confirm with a tax professional before filing. **Trigger to revisit before horizon:** the frontmatter condition (reverse split + uplisting, share-count decline, material K8/interpleader cash release, or a >2-week hold above ~$0.01).

---

## Outcome (filled in by /reflect-decisions)

(reserved — pending)
