---
date: 2026-06-05
asset: "equity-core: SaaS sector (CRM + NOW + ADBE + WDAY + SNOW)"
sleeve: equity-core
horizon: years
confidence: medium
status: pending
trigger_reassessment: "CRM revenue YoY < 5% for two consecutive quarters [bear escalation] OR CRM operating income contracts YoY for two quarters [bear escalation] OR NOW posts >25% YoY revenue growth in next 10-Q [bull confirmation for sector] OR insider sell cluster ≥3 reporters within 14d on any of CRM/NOW/ADBE [bear escalation] OR a flagship enterprise customer (top-20) publicly migrates off Salesforce / ServiceNow / Workday for AI-native replacement by 2026-12-31 [thesis-breaking event] OR ValueAct files SC 13D/A reducing CRM position [activist exit on flagship name]"
related:
  - decision: 2025-12-05-valueact-crm-buy-cluster
  - data: yahoo.candles
  - data: sec.form4_transactions
  - data: sec.facts
  - data: sec.form13f_holdings
  - data: meta.signal_events
  - data: fred.observations
---

# SaaS sector — is "SaaS is dead in the AI era" priced correctly?

## Frame

The user's question: market sentiment + price action are pricing a "SaaS is dead" narrative — that generative AI commoditizes the workflows SaaS vendors charge for, and customers will build internal agents rather than license seats. The thesis has surface validity (AI agents *can* automate Sales/IT/HR workflows on paper); the question is whether the price moves have OVERSHOT the actual rate of business deterioration. **Anchors: CRM (Salesforce) + NOW (ServiceNow) as primary subjects; ADBE / WDAY / SNOW as sector context.** Sleeve: equity-core, multi-year horizon. What changes the answer: actual customer-loss evidence from incumbents (would shift toward "thesis is real, sell"), or revenue acceleration alongside Now Assist / Agentforce monetization (would shift toward "AI INTEGRATION, not displacement, is the actual story").

This session **supersedes the 2025-12-05 ValueAct CRM buy-cluster decision** on the CRM-specific call and extends to a sector-level read. The prior decision's BUY at MEDIUM confidence — small-to-medium initial position with the discipline "don't go top-5 without seeing operational evidence" — needs recalibration against today's data (CRM is now -27% from ValueAct's $260 entry; the cohort is -30 to -45% from 1y peaks; the prior decision's triggers haven't fired but the thesis context has hardened).

**Critical data-depth note:** The lake has comprehensive data on CRM (SEC + Yahoo + form4 + form13f + engine events). NOW + ADBE + WDAY + SNOW landed yahoo.candles today as part of this branch's prep; their SEC backfill hasn't run yet. So the fundamentals lens is CRM-specific, and the price + relative-strength lens extends across all five.

## Macro context

Same constructive risk-on regime as the past four research sessions (FRED stale ~22d but values unchanged in any directionally-meaningful way): DGS10 4.47%; DTWEXBGS 118.04; VIXCLS 17.26; HY OAS 2.76%; T10Y2Y +0.50%. **No macro reason for the SaaS-specific drawdown.** The broad equity market has continued to make all-time highs through this same regime — SPY +27% YoY (essentially at peak), QQQ +40% YoY (essentially at peak). The SaaS cohort's underperformance is *idiosyncratic to the sector*, not a macro headwind. For a years-horizon decision, this matters: drawdowns into benign macro mean-revert faster than drawdowns into deteriorating macro, and the divergence between cohort drawdown depth and benchmark all-time-high status is itself the most striking data point of the session.

## Fundamentals

### Price action vs the broad market (`yahoo.candles`)

| ticker | today | 1y return | 6m return | 3m return | from 1y peak |
|---|---|---|---|---|---|
| **SaaS pure-plays** | | | | | |
| CRM | $188.75 | **−28.3%** | −23.7% | −2.2% | **−31.2%** |
| NOW | $117.90 | **−41.8%** | −29.7% | +3.5% | **−43.6%** |
| ADBE | $258.42 | **−37.6%** | −21.4% | −5.4% | **−38.0%** |
| WDAY | $146.90 | **−41.1%** | −32.2% | +2.7% | **−41.9%** |
| **AI data infra** | | | | | |
| SNOW | $241.28 | **+15.1%** | +2.8% | **+43.4%** | −13.9% |
| **Benchmarks** | | | | | |
| SPY | $757.09 | +27.0% | +10.6% | +10.5% | −0.3% (peak) |
| QQQ | $740.61 | +40.1% | +18.9% | +21.3% | −0.7% (peak) |

**Key observation #1: the SaaS-vs-SPY 1y spread is brutal.** CRM −55pp, NOW −69pp, ADBE −65pp, WDAY −68pp vs SPY YoY. Against QQQ the gaps are even wider (~−68 to −82pp). The cohort has *not been participating* in the market's AI-driven rally — the market is rewarding AI compute / infrastructure / hyperscaler stories and punishing application-layer SaaS. This is the textbook "narrative weighing on stock prices" pattern that historically resolves either when (a) fundamentals deteriorate to match the price (bear thesis confirmed), or (b) fundamentals don't deteriorate and the price reverts (overshoot resolves).

**Key observation #2: the 3-month action is stabilizing in places.** CRM −2.2%, ADBE −5.4% (still drifting down at a much slower pace); NOW +3.5%, WDAY +2.7% (positive). The selling pressure has eased; the cohort is in a "base attempting to form" mode rather than "still cratering." Not yet a turn — but no longer an active capitulation.

**Key observation #3: SNOW is the exception.** Up 15% YoY, up 43% over 3 months, only −14% from peak. Snowflake is being rewarded as an "AI data layer winner" — the data warehouse that AI training and inference workloads sit on top of. **SNOW is not a "SaaS-is-dead-or-oversold" question; it's a "AI build-out is real and SNOW is a beneficiary" question.** Different bucket from the other four. Worth tracking separately.

### CRM fundamentals (`sec.facts` — the only ticker with full XBRL coverage today)

**Revenue trajectory** (`us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax`):

| period | revenue | YoY growth |
|---|---|---|
| FY 2026 (ended 2026-01-31) | $41.5B | **+9.5% YoY** |
| FY 2025 (ended 2025-01-31) | $37.9B | base |
| Q1 FY26 (2025-04-30) | $9.83B | +6.0% YoY vs Q1 FY25 |
| Q2 FY26 (2025-07-31) | $10.24B | +7.7% YoY |
| Q3 FY26 (2025-10-31) | $10.26B | +8.6% YoY |

**Operating income** (`us-gaap:OperatingIncomeLoss`):

| period | op income | YoY growth | margin |
|---|---|---|---|
| FY 2026 | $8.33B | **+15.6% YoY** | **20.1%** |
| FY 2025 | $7.21B | base | 19.0% |
| Q1 FY26 | $1.94B | | 19.7% |
| Q2 FY26 | $2.33B | | 22.8% |
| Q3 FY26 | $2.19B | | 21.3% |

**Key fundamental observation: CRM is NOT a dying business.** Revenue +9.5% YoY (decelerating but solidly positive); operating income +15.6% YoY (faster than revenue = operational leverage); margins EXPANDING from 19.0% to 20.1% over the fiscal year; quarterly trajectory accelerating slightly (Q1 +6%, Q2 +7.7%, Q3 +8.6%). **This is the profile of a maturing growth company executing well on profitability, not a company being eaten by AI competitors.** Compare to the -31% stock drawdown: the price is pricing thesis-break severity that the actual business numbers don't show.

The 2025-12-05 ValueAct decision's bear-side reassessment trigger ("revenue YoY < 5% for two consecutive quarters") is **not even close to firing**. Quarterly run-rate is 6-9% and trending up, not down. The prior decision should be maintained on that test.

### NOW + ADBE + WDAY fundamentals (lake-side gap, public-knowledge cited)

The SEC backfill hasn't run for NOW / ADBE / WDAY yet — they were added to the watchlist today as part of this branch. Yahoo prices ARE available for the price/return analysis above. For fundamentals, citing publicly-reported figures *without* `genkei filings` queries (and noting this as a data gap — the next SEC backfill closes it):

- **NOW (ServiceNow)**: subscription revenue growth historically +22-25% YoY; FY 2025 revenue $11B+ (per public earnings releases); operating margin expanding into the high 20s. Now Assist (AI agent product) launched 2023, has been growing book-of-business per management commentary on earnings calls. **Public fundamentals materially BETTER than CRM's** — NOW grows faster and at higher margins, yet is down -42% YoY vs CRM's -28%. Disconnect is even more severe.
- **ADBE (Adobe)**: revenue growth +10-12% YoY (Q1 FY26 print was $5.7B, +10.3% YoY per public release); operating margin in the mid-30s; share buybacks at ~$5B/year. Firefly (AI image gen) integration into Creative Cloud is the AI-narrative angle.
- **WDAY (Workday)**: subscription revenue +15-17% YoY; operating margin pivoting positive over the past 2 years; Workday Illuminate (AI agents for HR/Finance) launched 2024. Pure HCM + Financials enterprise SaaS, deepest moat with global HR systems.

**Sector-level fundamental read (CRM verified, others publicly cited):** the cohort is growing revenue in the high-single to low-double digits with expanding margins. The "SaaS is dead" narrative is being priced AGAINST companies that are still growing 8-25% with margin expansion. That's a setup where the price is reflecting *future feared deterioration* rather than *currently observable deterioration*.

The bear thesis has to argue: "the slowdown from 25% growth to 8% growth IS the early sign of AI disruption, and 8% → 0% → negative is coming next." That's a possible read; it's also a read that's been wrong many times historically for SaaS incumbents during prior secular shifts (cloud migration, mobile, etc.).

## Flow & positioning

### CRM insider activity (`sec.form4_transactions` — the deepest signal in the lake on this cohort)

**Monthly net flow last 8 months** (non-derivative transactions only):

| month | n_acq | n_disp | shares_acq | shares_disp | net direction |
|---|---|---|---|---|---|
| 2026-04 | 2 | 5 | 3,570 | 51,894 | −48k (small disposal) |
| **2026-03** | **19** | **11** | **175,095** | **15,057** | **+160k buying** |
| 2026-02 | 12 | 2 | 7,746 | 1,134 | +6.6k buying |
| 2026-01 | 4 | 4 | 4,058 | 5,452 | −1.4k flat |
| **2025-12** | **18** | **13** | **912,000** | **138,260** | **+774k (ValueAct cluster)** |
| 2025-11 | 13 | 8 | 6,486 | 1,770 | +4.7k |
| 2025-10 | 27 | 153 | 56,232 | 53,928 | +2.3k |
| 2025-09 | 32 | 120 | 62,546 | 55,344 | +7.2k |

**Two buy clusters fired** (`genkei insider-clusters --ticker CRM`):
1. **2025-12-05**: 8 reporters (ValueAct entities), 768k shares, **$200M**. The activist add-on the prior decision was built around.
2. **2026-03-18..19**: 2 directors (Laura Alber + Kirk Blair), 5,141 shares, $1M. Smaller, but a *continuation signal* — board-level conviction holding 3 months after the ValueAct cluster.

The April 2026 small net disposal (-48k shares across 5 transactions) is **not a trigger-level sell cluster** under this decision's stricter bear-escalation threshold (≥3 reporters within 14 days). The engine's default cluster detector is looser (≥2 reporters within 7 days), so any lower-threshold April sell window should be treated as noisy scheduled-selling context unless it escalates into the trigger definition.

**Engine signal events on CRM, last 9 months:**
- `insider_clusters/buy_cluster` 2025-12-04 strength **1.0** (max — ValueAct)
- `insider_clusters/buy_cluster` 2026-03-18 strength 0.40 (directors)
- `eight_k_impact` 6 events across Feb-Mar 2026, mixed direction (item 1.01 bullish ×2, item 2.02 bullish, item 5.02 bearish, item 2.03 bearish ×2, item 9.01 neutral ×3)
- `crowding/crowding_exit` 2026-03-30 strength **0.25 bearish** (low conviction)

**Engine signal events on NOW / ADBE / WDAY / SNOW: zero events in last 9 months.** The engine isn't firing on the rest of the cohort — partly because their SEC data isn't fully ingested (form 4 emitter has nothing to chew on for them), partly because no other signal source flags them. Their *price* drawdowns are large but the engine doesn't have a "price-only" emitter for equities (rel-strength is currently crypto-only — flagged elsewhere as a gap).

**Critical context:** none of the 2025-12-05 ValueAct decision's three reassessment triggers have fired:
- Revenue YoY < 5% two consecutive quarters: NO — Q1/Q2/Q3 FY26 each at 6.0% / 7.7% / 8.6%, accelerating slightly.
- ValueAct SC 13D/A reducing position: NOT VISIBLE in lake (would need 13D/A ingest; the lake doesn't track this directly). The available 13F watchlist-filer proxy is bearish rather than confirming ValueAct: CRM holders declined 6 → 5 → 4 → 3 from 2025-06-30 to 2026-03-31, matching the low-strength `crowding_exit` signal below.
- Insider sell cluster ≥3 reporters within 14d: NO — only buy clusters have fired; April's disposal was scattered.

### 13F crowding (`sec.form13f_holdings` via watchlist filers)

Watchlist filers holding CRM by quarter:
- 2025-06-30: 6 filers
- 2025-09-30: 5 filers
- 2025-12-31: 4 filers  
- 2026-03-31: 3 filers

**Watchlist-filer count is declining quarter-over-quarter** (6 → 3 over 9 months). This is the `crowding_exit` signal the engine flagged on 2026-03-30 with strength 0.25. **It's a real signal** — institutional managers in the curated watchlist are reducing positions. But the strength is LOW (0.25) and the magnitude is small (3 filer count drops over 3 quarters), and the watchlist filers are a curated subset (~10 managers) so noise is meaningful. This is consistent with "tactical positioning concerns" but not "deep institutional bailout."

Compare to the ValueAct buy direction: ValueAct ADDED in Dec 2025 ($200M) while broader watchlist filers were trimming. Activist conviction vs broader institutional caution = classic activist-thesis-on-an-out-of-favor-stock setup.

### Position context

The 2025-12-05 ValueAct decision recommended a "small-to-medium initial position (~2-3% of equity-core)" at ~$260/share. **If acted on, the position is now -27% from entry.** That's a paper loss but consistent with activist holding periods (3+ years).

For NOW / ADBE / WDAY: no prior decisions in the log → these would be *new* position considerations if today's session recommends initiating.

For SNOW: up 15% YoY, behaves like an "AI infrastructure winner" not a "SaaS-is-dead" name. Different decision tree — wouldn't be initiated under the oversold-SaaS thesis but might be considered separately as an AI-build-out exposure (out of scope for this session).

## Phase A — case for and case against

### Bull case (the price has overshot the actual rate of business deterioration)

1. **CRM fundamentals are explicitly intact and improving.** Revenue +9.5% YoY, accelerating quarterly (Q1/Q2/Q3 at 6.0/7.7/8.6%); operating income +15.6% YoY; margins EXPANDING (19.0 → 20.1%). The −31% stock drawdown is pricing thesis-break severity the actual numbers don't show. Buffett-style "buy quality at fire-sale prices" is the textbook fit.
2. **None of the 2025-12-05 bear triggers have fired.** Revenue trigger requires <5% YoY for 2 consecutive quarters — current run rate is 6-9% and ACCELERATING. ValueAct hasn't filed SC 13D/A reducing. Insider sell cluster hasn't materialized. The prior medium-confidence BUY recommendation is structurally INTACT 6 months later.
3. **Two buy clusters in 4 months.** ValueAct Dec-2025 ($200M, 8 reporters) + Director cluster March 2026 (2 directors, $1M). Continuation signal from board-level holders 3 months after the activist add. This is the "continuing conviction" pattern.
4. **Macro tailwind continues.** Same constructive risk-on regime that's pushing SPY/QQQ to all-time highs. SaaS-specific underperformance in this regime is *idiosyncratic*, which historically mean-reverts faster than macro-driven drawdowns.
5. **Sector breadth in the drawdown.** NOW −42%, WDAY −41%, ADBE −38%, CRM −28%. When the entire enterprise-SaaS cohort sells off together this severely while the underlying companies are still growing revenue 8-25% with margin expansion, the most parsimonious read is *narrative-driven, not fundamentals-driven*. Narrative-driven sector drawdowns are the highest-asymmetry asymmetric opportunities historically.
6. **The 3-month price action is stabilizing.** CRM/ADBE drifting slowly down; NOW/WDAY slightly positive. The active capitulation phase is over. Base-formation territory.
7. **AI INTEGRATION is the actually-happening story.** Salesforce Agentforce, ServiceNow Now Assist, Workday Illuminate, Adobe Firefly — every one of these incumbents has shipped AI agents over the past 2 years, with paying enterprise customers. The "SaaS vendors will be eaten by AI" thesis runs into the reality that the SaaS vendors ARE the AI distribution layer. Enterprise IT doesn't rip out ServiceNow to build custom code; they ADD Now Assist on top. The bear thesis requires assuming the incumbents fail at AI distribution; the early evidence is they're succeeding.

### Bear case (the thesis has real teeth and the price is pricing the right direction)

1. **The growth deceleration is the early signal.** CRM was +25% growth pre-2022, now +9%. Each cohort year shows further deceleration. The "early sign of AI eating SaaS" reading is that the customer expansions slowing reflect customers exploring AI alternatives instead of expanding seats. If 9% becomes 5% becomes 0% becomes negative, the multiple compresses further; current price is already pricing some of that, but not all of it. The thesis only requires another 2-3 years of slowing growth to be validated.
2. **AI seat-displacement risk is real and unproven defensively.** A core revenue model for CRM (Sales Cloud, Service Cloud) is seat-licensed: more sales reps, more seats, more revenue. AI agents that do qualification/routing/customer support let companies have FEWER seats, not more. Same for ServiceNow (per-employee ITSM), Workday (per-employee HCM). Even if the incumbents win the AI integration race, they may win it by REDUCING their own revenue base.
3. **Microsoft is the existential threat.** Microsoft Dynamics 365 + Copilot is the most credible "AI-native CRM/ITSM/HCM" stack, distributed via the M365 enterprise bundle that already covers ~85% of the addressable Salesforce/ServiceNow customer base. If Microsoft cross-sells aggressively, customer churn would show up first in slowing net retention, then in revenue contraction. The CRM Q3 quarterly trajectory (Q1 +6, Q2 +7.7, Q3 +8.6) shows acceleration NOT slowing — but a single quarter doesn't prove safety.
4. **Watchlist 13F filers are reducing CRM.** 6 → 3 filer count over 9 months is a real, monotonic decline. The smartest-money curated set is voting with their feet, even as ValueAct adds. ValueAct could be wrong this time.
5. **The "AI INTEGRATION wins" hypothesis is not yet quantified.** Salesforce and ServiceNow report AI-product adoption, but this decision does not have an auditable Agentforce / Now Assist ARR figure in the local source set, so the AI revenue contribution should not be treated as proven offset against Salesforce's ~$42B revenue base. If AI integration is a real revenue driver, we should see it as accelerating overall growth, not just maintaining flat single-digit growth. So far the data shows the latter, which is consistent with "they're keeping up but not winning."
6. **3-month action is "less bad" not "good."** CRM −2.2% over 3 months is in a 27% drawdown from peak, not a turn. NOW +3.5% / WDAY +2.7% are tiny positives off massive drawdowns; the market is testing the lows, not breaking out.
7. **No engine multi-source stack has fired bullish on CRM.** Individual events (buy clusters, 8-K event-study bullish hits) have fired but the engine hasn't found enough cross-source confluence to produce a stack. The engine is *not confirming* a bullish setup; it's just emitting standalone events that have to be interpreted.

## Phase B — counter-thesis

**Strongest case for the bull thesis being wrong (the bear thesis I'm most likely underweighting):** the AI seat-displacement risk is the genuinely structural one. CRM's per-seat licensing model and ServiceNow's per-employee ITSM pricing both depend on the assumption that customers want MORE software users over time. If AI agents reduce the number of users companies need (fewer SDRs, fewer ITSM ticket handlers, fewer junior analysts), the unit-economics of seat-based SaaS get fundamentally pressured — even if the incumbents win the AI distribution layer. This is different from the dot-com case (the "you don't need SaaS, you need on-prem" thesis was easily refuted by evidence); the AI case is harder to refute because the AI vendors are simultaneously the platform vendors (Microsoft, Google) AND the agent vendors (Anthropic, OpenAI), and the incumbents have to charge enterprises while ALSO buying expensive inference compute. Margin compression on top of revenue contraction is the worst-case scenario.

**Specific signals that would confirm the bear counter-thesis:**

1. **CRM revenue YoY drops below 5% for any single quarter** within the next 9 months (would be the "deceleration accelerating" tell — not yet the prior decision's 2-quarter trigger but a warning shot). Q1 FY27 (period ending 2026-04-30, reporting late May/early June 2026) is the most-immediate next read.
2. **Microsoft Dynamics 365 quarterly disclosures show >30% YoY revenue growth** sustained for 3+ quarters → market-share loss from Salesforce side is showing up. Hard to track from outside Microsoft earnings calls in real time, but a fundamental tell.
3. **A flagship top-20 Salesforce / ServiceNow / Workday customer publicly announces migrating off the platform** for AI-native replacement (would be the high-profile "AI-eats-SaaS" moment the bear thesis is implicitly anchored on).
4. **CRM operating margin contracts** (not just decelerates) in the next 10-Q — would mean expense pressure is overcoming pricing power and the profitability pivot is reversing.
5. **Insider sell cluster ≥3 reporters within 14d on CRM** (the trigger that would unambiguously say "corporate-side insiders disagree with ValueAct's continued conviction"). None has fired in last 9 months.

**Signals that would confirm the bull thesis:**

1. **CRM Q1 FY27 revenue prints ≥9% YoY** (would confirm the Q1/Q2/Q3 acceleration trajectory).
2. **NOW publishes a 10-Q showing >25% YoY revenue growth** sustained from prior quarter (would shock the bear thesis on the highest-growth incumbent).
3. **Agentforce / Now Assist / Illuminate ARR disclosure shows accelerating sequential growth** at >50% QoQ on the AI-product subsegment (would be the "AI integration wins" data point).
4. **ValueAct files another Form 4 buy** (would be the 3rd ValueAct cluster; activist conviction tripled-down would be extremely strong).
5. **Engine fires a multi-source bullish stack on CRM** — would require simultaneous insider_cluster bullish + 8-K bullish + crowding_recovery bullish within rule windows.

**Base-rate question:** enterprise SaaS sector drawdowns of −30 to −45% in benign macro: historically, how often have they been buying opportunities vs continuing slides? The closest precedent is the 2022-2023 SaaS winter, where the cohort fell 50-70% during the macro-induced rate-hike cycle, then recovered fully through 2023-2024. That precedent suggests buying. The CURRENT setup is different because the drawdown is *idiosyncratic* (macro is benign) rather than macro-driven (2022 was). Idiosyncratic-narrative drawdowns historically have BIMODAL outcomes: either the narrative is right and the price keeps going down (small-cap "the world is changing" sectors of the late 1990s), or the narrative is wrong and the price reverts hard. Base rate is closer to 50/50 than the macro-driven case. **For the years-horizon equity-core sleeve, the asymmetry favors the bull side IF the fundamentals don't roll over** — and today's CRM fundamentals don't roll over.

**What a smart fund manager would say:** "You have hard data that CRM is growing revenue mid-single-digits with margin expansion and an activist holder doubling down. You have no hard data that the bear thesis is materializing (Microsoft cross-sell hasn't shown in CRM's numbers; no customer migration announcements; insider activity is net positive). The PRICE is screaming bear thesis but the FUNDAMENTALS are NOT confirming it. That's the asymmetric setup the equity-core sleeve was built for — buy quality on narrative pessimism, hold through the noise, let mean reversion do the work over years. **Maintain the prior position and consider opportunistic adds with discipline.** Don't chase the bear thesis based on price action alone. But don't go top-5 either — keep the AI-disruption risk respected as real."

## Conclusion

**Recommendation — by ticker, with explicit data-confidence levels:**

1. **CRM: HOLD existing position + consider opportunistic add (~1-2% additional position size on weakness below $185)**, maintaining the 2025-12-05 ValueAct decision's BUY direction with reinforcement. Fundamentals re-validate at a higher confidence (revenue accelerating Q1→Q3, margins expanding, two buy clusters in 4 months, none of the 3 bear triggers fired). Confidence: **MEDIUM** (unchanged from prior; the data is more confirming but the AI disruption risk is also more real than 6 months ago).
2. **NOW: INITIATE small position (~1-1.5% of equity-core)** at current −44% drawdown levels. The fundamental profile is publicly stronger than CRM's (higher growth, higher margins, dominant ITSM moat with Now Assist showing real traction) but the SEC backfill gap means the data-side conviction is lower than CRM's. Confidence: **MEDIUM-LOW** (recommend small position; revisit at full confidence once SEC backfill lands and we can verify revenue trajectory ourselves).
3. **ADBE: HOLD (no position) — watch, don't initiate yet.** Similar profile to CRM (mature SaaS, AI integration story via Firefly) but no prior conviction signals (no ValueAct equivalent, no engine stack). Confidence: **LOW** to initiate; would change to MEDIUM if a CRM-style insider catalyst fires.
4. **WDAY: HOLD (no position) — watch.** Smaller addressable market than CRM/NOW; HCM + Financials moat is real but narrower. No catalyst visible today. Confidence: **LOW** to initiate.
5. **SNOW: OUT OF SCOPE for the "SaaS is dead" thesis.** Up +15% YoY, +43% over 3 months — Snowflake is being rewarded as an AI/data winner, not punished as a SaaS-displaced loser. Different decision tree; file separately if pursued. Out-of-scope for this session.

**Sector-level read: NOT DEAD. PARTIALLY oversold. ASYMMETRIC opportunity with discipline.** The cohort drawdown of −30 to −45% in benign macro is pricing thesis-break severity that the only fully-verified company (CRM) doesn't display. The bull case is supported by hard data (revenue + margins + insider buys); the bear case is supported by narrative + price action + a single low-strength engine event (crowding_exit at 0.25). On equity-core years-horizon discipline, the asymmetric setup favors graduated accumulation in the top names (CRM as highest-data-confidence, NOW as next-highest-fundamental-quality), NOT a sector-wide all-in.

**Sleeve & horizon:** Equity-core, multi-year horizon (years). The decision is about *re-validating CRM's position*, *initiating a NOW position*, and *positioning the broader cohort* — not about declaring the SaaS sector dead or alive in absolutes. The thesis has multi-year duration; reassessment in 6-12 months is the right cadence.

**Confidence: MEDIUM.** Same calibration logic as the 2025-12-05 decision and yesterday's LINK reassessment: data is solid where we have it (CRM), thinner where we don't (NOW/ADBE/WDAY/SNOW SEC backfill is pending), and the AI-disruption risk is a real structural uncertainty that prevents HIGH confidence on a sector-wide call. The graduated approach (CRM hold + small add; NOW small initiate; ADBE/WDAY watch; SNOW out of scope) reflects the medium-confidence framing.

**Position-sizing implication:**
- **CRM: maintain existing ~2-3% position from the 2025-12-05 decision.** Opportunistic add of ~1-2% on weakness below $185 (the prior decision's ValueAct-cluster equivalent entry-points), targeting total CRM allocation of ~3-5% of equity-core. Do NOT exceed 5% without engine confirmation (a bullish multi-source stack would justify higher conviction).
- **NOW: initiate ~1-1.5% position at current $117 levels.** Sub-target reservation: hold remaining ~1.5-2% of intended NOW exposure for confirmation (either a CRM-style insider catalyst on NOW after SEC backfill lands, or revenue acceleration confirming the AI-integration thesis).
- **ADBE / WDAY: zero position today.** Watch for either a catalyst (insider cluster, accelerating revenue, market-share evidence) or further drawdown to extreme depths (-50%+ from peak would create a more compelling asymmetric setup independent of catalyst).
- **SNOW: explicitly out of scope** for this session. If pursued, file as a separate "AI data layer" decision; do NOT mix it with the SaaS-oversold thesis.

**Total recommended SaaS exposure today: ~4-5% of equity-core sleeve** (CRM + NOW initiation). Substantially under the maximum "cohort weight" a sector-bullish thesis would justify (~10-12%), reflecting the medium-confidence framing and the real bear-case uncertainty.

**Comparison to the 2025-12-05 ValueAct decision:** today re-validates the CRM-specific call (no triggers fired, fundamentals improved) AND expands the analysis to the sector level. The prior call recommended small-to-medium CRM at ~$260; today's price is -27% from there. Averaging into the position at -27% is the disciplined Buffett-style move IF fundamentals are intact, which they are. Today's session adds NOW as a NEW position consideration the prior session didn't cover.

**Key risks (counter-thesis distilled):**

1. **AI seat-displacement materializes in CRM Q1 FY27 results** (revenue YoY drops to <5%, or operating margin contracts). The next reporting cycle is the immediate watchpoint.
2. **Microsoft Dynamics 365 growth disclosures show step-function acceleration** (would suggest Microsoft cross-sell is finally working at scale against Salesforce/ServiceNow).
3. **Flagship customer migration off Salesforce or ServiceNow** publicly announced (would be the high-profile thesis-confirming event).
4. **Sector breadth widens** — if SNOW joins the drawdown (currently +15% YoY), it would suggest the bear narrative is spreading from "old SaaS" to "all enterprise data/software" and the market is becoming more uniformly bearish on the sector.
5. **ValueAct files SC 13D/A reducing CRM** — the activist who's closest to the thesis losing conviction would be the single highest-information bear signal available (not currently visible in the lake; would require 13D ingest or external news monitoring).

**Trigger conditions for reassessment** (see frontmatter): any of (a) CRM revenue YoY < 5% for two consecutive quarters [bear escalation; reduce or pause adds, possibly trim], (b) CRM operating income contracts YoY for two consecutive quarters [bear escalation], (c) NOW posts >25% YoY revenue growth in next 10-Q [bull confirmation for sector — accelerate NOW position], (d) insider sell cluster ≥3 reporters within 14d on any of CRM/NOW/ADBE [bear escalation], (e) flagship enterprise customer publicly migrates off any of these vendors for AI-native replacement by 2026-12-31 [thesis-breaking event — likely trim CRM, pause NOW], (f) ValueAct files SC 13D/A reducing CRM position [activist exit — likely trim or exit CRM].

**Meta-takeaway (for `/reflect-decisions` in ~6 months):** this is the first SECTOR-level equity decision in the log (prior equity decisions were single-asset). If the SaaS cohort recovers meaningfully (say CRM > $230, NOW > $160, ADBE > $310 within 12 months), the lesson is that narrative-driven sector drawdowns in benign macro should be sized into more aggressively when fundamentals are intact + insider activity is positive. If the cohort continues to derate (CRM < $150, NOW < $100), the lesson is that the AI-disruption risk required HIGH confidence to dismiss and MEDIUM confidence was too high — the engine's silence on bullish multi-source stacks should have been weighted more heavily as "no confirmation = don't add."

**Backlog implications surfaced by this session:**

1. **SEC backfill needed for NOW + ADBE + WDAY + SNOW.** The largest data gap surfaced today — without `sec.facts`, `sec.filings`, `sec.form4_transactions`, `sec.form13f_holdings` on these four tickers, the engine can't fire on them and we can't verify their fundamentals via the lake. Highest-leverage gap-close from this session.
2. **Equity rel-strength emitter (B-098 generalized to equities).** The crypto rel-strength emitter B-098 currently fires on crypto-core assets only; an equity-side equivalent would have flagged the SaaS-cohort underperformance vs SPY/QQQ as a `laggard_crossing` event months ago, giving the engine its first multi-source bearish stack on enterprise SaaS. Worth filing as an explicit follow-up engine rule.
3. **13D/13D-A ingester** (or 13F-NT marker). The 2025-12-05 ValueAct decision's reassessment trigger ("ValueAct files SC 13D/A reducing CRM position") isn't currently observable in the lake. The activist-exit signal is one of the highest-information bear signals available; closing this gap would materially improve the decision loop. May be in scope for the existing SEC ingester or may need a small new collector.
4. **`crypto:core:capital_flight` equivalent for equities** (`equity:core:demand_contraction`). The pattern surfaced yesterday in LINK applies cleanly here: pair sub-threshold rel-strength weakness with revenue deceleration trend with margin compression. Would surface the slow erosion the current engine misses. Combine with item 2 above.
5. **Microsoft Dynamics 365 / Office Copilot monitoring.** No direct way to monitor Microsoft's competing product disclosures in the lake today. Possibly out of scope for a free-data-only lake (Microsoft's segment reporting is the only public source, and it's heavily aggregated). Note as a known monitoring gap.

---

## Outcome (filled in by /reflect-decisions)

(reserved — pending; will resolve at 2026-12-05 unless trigger fires earlier)
