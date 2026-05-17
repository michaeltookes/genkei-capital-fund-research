---
date: 2025-12-05
asset: CRM
sleeve: equity-core
horizon: years
confidence: medium
status: pending
trigger_reassessment: "CRM revenue YoY < 5% for two consecutive quarters OR ValueAct files SC 13D/A reducing position OR insider sell cluster ≥3 reporters within 14d"
related:
  - decision: insider-cluster-signal-baseline
  - data: sec.form4_transactions
---

# ValueAct concentrated buy in CRM — activist position-taking thesis

> **Note:** This file is the first concrete example in `docs/research/decisions/`. It uses a real event surfaced by `genkei insider-clusters` against the homelab lake (see B-060 in `docs/resolved.md`). The date in the frontmatter matches the cluster date, not when the decision file was written (the decision file was authored 2026-05-17 as part of B-049 / B-050; this lets `/reflect-decisions` treat it like a real decision once enough horizon has elapsed).

## Frame

ValueAct Capital Management filed Form 4s on 2025-12-05 totalling **768,000 shares of CRM (Salesforce) at ~$260/share — $200M total purchase, 8 distinct reporters across the ValueAct entity tree.** This is the textbook activist-position-taking signal: a concentrated, multi-entity purchase by a known activist fund disclosed publicly via Form 4 because Mason Morfit took a board seat. Question: does this warrant a position in CRM at the equity-core sleeve on a multi-year horizon? Horizon: years (this is an activist position; ValueAct's average hold is 3+ years).

## Macro context

`genkei macro --series DGS10 --since 2024-01-01` — 10Y around 4.4% range-bound Q4 2025, no immediate macro headwind for high-quality enterprise SaaS. `genkei macro --series BAMLH0A0HYM2 --since 2024-01-01` — HY OAS tight, credit not pricing distress. `genkei macro --series VIXCLS` — vol benign. **Macro regime call: risk-on continuing, no immediate headwind for cyclical enterprise software.** ValueAct picking the position at this moment is consistent with a "good entry inside a benign regime" thesis rather than a "bottom-fishing during stress" thesis.

## Fundamentals

`genkei filings --ticker CRM --concept us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax --unit USD --since 2020-01-01`:

- TTM revenue ~$36B (Q3 FY26 print), +9% YoY — decelerating from the 20%+ days but still growth.
- Operating margin expanding (Benioff's profitability pivot Q2 FY24 onward) — likely ValueAct's leverage point.
- Net cash position positive (`us-gaap:CashAndCashEquivalentsAtCarryingValue` ~$10B vs `us-gaap:LongTermDebt` ~$8B) — Buffett-mentality "income covers expenses" screen passes.

Concrete thesis fundamentals: CRM is a mature, dominant SaaS franchise that's transitioning from growth-at-any-cost to disciplined-profitable-growth. ValueAct's playbook with that kind of company is well-documented (Microsoft 2013 is the canonical case — they pushed for Ballmer transition + capital discipline; stock 5x'd over the subsequent 5y).

## Flow & positioning

`genkei insider-clusters --ticker CRM --since 2025-12-01 --until 2025-12-31 --json`:

- **2025-12-05**: 8 reporters, 768,000 shares, $200,125,440. Reporters: Morfit G Mason (dir), VA Partners I (dir), ValueAct Capital Management (dir), ValueAct Capital Management (dir), +4 more — all ValueAct entities. NO non-ValueAct officers in the cluster, which is meaningful: corporate insiders aren't piling in alongside; this is purely the activist taking the position.

`genkei insiders --ticker CRM --since 2024-01-01 --limit 30` (separate query for context): there's a separate cluster on 2024-06-03 with ValueAct buying 3.4M shares for $798M — the initial ValueAct position. The Dec-2025 buy is an *add-on* by an existing activist holder. That makes it stronger: they've been in for 18+ months, learned the company in depth, and are doubling down.

## Phase A — case for and case against

**Bull case:**

1. **Activist add-on signal.** ValueAct adding to an existing concentrated position 18 months after the initial — that's the highest-conviction shape of insider/activist buying.
2. **Margin-expansion runway.** CRM's profitability pivot is mid-innings; ValueAct's playbook makes operational leverage on a $36B revenue base into multi-year EPS growth even with single-digit topline.
3. **Macro tailwind for enterprise SaaS.** Risk-on regime + capex spending recovering off 2022-2023 SaaS winter.
4. **Buffett-mentality fit.** Cash > debt, dominant share in CRM/sales/service categories, recurring revenue model.

**Bear case:**

1. **Topline deceleration is real.** 9% growth on a $36B base is good but not great; the "growth at any price" days are over and the multiple has compressed to reflect that. Further compression possible if growth dips toward mid-single-digits.
2. **AI disruption risk.** Salesforce is exposed to the "what if AI agents do CRM automatically" thesis. Their AI strategy (Agentforce) is real but unproven. If Microsoft or a startup builds something that obsoletes the seat-licensed model, CRM is a melting ice cube.
3. **Activist add-on doesn't always mean upside.** Sometimes activists double down after a year of underperformance to defend a thesis that was wrong from the start. Microsoft 2013 worked; not every Microsoft 2013 works.
4. **No corporate-insider confirmation.** If CRM management saw the same opportunity ValueAct does, you'd expect Benioff or Robbins (CFO) buying too. Their silence is data.

## Phase B — counter-thesis

The strongest case for being wrong is the **AI disruption** angle (bear point #2). The signal that would flip this thesis:

- A flagship customer (say, a top-20 enterprise customer) publicly switching off Salesforce for an AI-native CRM by 2026 H2.
- OR: Microsoft Dynamics 365 Copilot growth disclosure showing 50%+ enterprise-seat capture in Microsoft's earnings call.
- OR: Benioff exit / replacement-by-AI-skeptic at the top.

The other meaningful counter-signal is **ValueAct itself filing SC 13D/A reducing position** — that means the activist who's closest to the situation lost confidence; we should respect that immediately. Trigger conditions in frontmatter reflect this.

The base-rate question: activist add-ons at +18 months work more often than not (60-70% beat market over 3y per academic studies of 13D filings), but the dispersion is wide. CRM at this stage isn't a deep-value setup — multiple is already reasonable, so the upside depends on margin expansion + AI not eating the lunch. Risk-adjusted upside looks like +20-40% over 2-3y if the thesis works, -20-30% if AI disruption is the actual story.

## Conclusion

**Recommendation:** Buy. Equity-core sleeve, multi-year horizon, medium confidence.

**Sizing:** Small-to-medium initial position (~2-3% of equity-core), with explicit reservation to add IF ValueAct adds again OR margin expansion shows up in Q1 FY27 earnings (May 2026 reporting). DO NOT make this a top-5 position without seeing operational evidence the playbook is working — the AI overhang is real.

**Confidence: medium.** This is one of the stronger insider signals available on the lake (high-conviction activist add-on with macro support and fundamentals support), but the AI disruption risk is real and not fully knowable today.

**Key risks** (counter-thesis distilled):
1. AI-native CRM displaces Salesforce in the next 24-36 months → watch flagship-customer-loss news + Microsoft Dynamics 365 disclosures.
2. ValueAct exits or trims the position → watch SC 13D/A filings.
3. Revenue growth falls below 5% YoY for two consecutive quarters → margin expansion can't carry the multiple if topline goes negative.

**Trigger conditions for reassessment** (see frontmatter): any of (a) revenue YoY < 5% two quarters in a row, (b) ValueAct files SC 13D/A reducing, (c) insider sell cluster ≥3 reporters within 14d (would suggest corporate-insider-level disagreement with the activist thesis).

---

## Outcome (filled in by /reflect-decisions)

(reserved — pending; will resolve at 2026-12-05 unless trigger fires earlier)
