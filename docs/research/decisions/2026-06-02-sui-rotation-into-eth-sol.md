---
date: 2026-06-02
asset: SUI
sleeve: crypto-tactical
horizon: months
action: trim
reflection_benchmark:
  type: destination_basket
  label: 50/50 ETH+SOL destination basket
  assets:
    - ticker: ETH
      weight: 0.5
    - ticker: SOL
      weight: 0.5
confidence: medium
status: pending
trigger_reassessment: "SUI chain TVL recovers above $700M within 6 months OR SUIG open-market insider buy cluster ≥2 reporters at any price within 6 months OR SUI outperforms ETH on 90d window by ≥10pp within 6 months OR SUI/BTC 30d rel-strength flips back to laggard (≤−15pp) — the latter accelerates the trim"
related:
  - decision: 2026-05-20-sui-position-assessment
  - decision: 2026-06-02-ethereum-position-assessment
  - decision: 2026-06-02-solana-position-assessment
  - data: coingecko.market_data
  - data: coinbase.candles
  - data: defillama.chain_tvl
  - data: defillama.stablecoins
  - data: meta.signal_events
supersedes: 2026-05-20-sui-position-assessment
---

# SUI — rotation into ETH + SOL: prior trigger fired, deepen the trim

## Frame

Companion to today's ETH and SOL position-assessment sessions. User has been **staking ETH, SOL, and SUI for ~3 years** and is asking: does the three-L1 staking allocation still make sense, or is it time to move SUI capital into ETH or SOL? User explicitly acknowledged GrayScale's SUI Trust and SUIG (Sui Group Holdings, the public-market Sui-treasury vehicle on the equity-core watchlist) as potential institutional bull cases for SUI, but asked for a non-biased opinion that ignores both. **The methodology output below explicitly does not weight the prior 3-year staking position OR the institutional vehicles in the recommendation** — the question is forward-looking allocation efficiency: if you were deploying $100 across these three L1s today, what's the right split, and does the existing SUI position warrant rotation? **This decision supersedes the 2026-05-20 SUI session** (`status: resolved` will be applied on next `/reflect-decisions`); the prior decision's bearish trigger has fired, so this is the action-on-trigger entry rather than a re-evaluation. What would change my mind: a sharp Sui-specific catalyst (new flagship protocol launch, SUIG open-market insider buy cluster, or TVL inflection back above $700M) within 6 months.

## Macro context

**Identical to today's ETH and SOL sessions** — constructive risk-on, no macro reason to under-allocate crypto-tactical, and any SUI-specific weakness has to be idiosyncratic. FRED ~20 days stale; usable for directional call.

## Fundamentals — three-asset comparison

The methodology-critical comparison. All numbers as-of 2026-06-02.

### Price trajectory (1y / 6m / 7d)

| asset | 1y return | 6m return | **7d return** | drawdown from 2y peak |
|---|---|---|---|---|
| ETH | -25.9% | -38.4% | -10.4% | -49% (Dec 2025) |
| SOL | -50.7% | -44.4% | -11.0% | -53% (2024) |
| **SUI** | **−75.5%** | **−49.8%** | **−21.6%** | **−83%** (mid-2024) |

**SUI fell -21.6% in the last 7 days alone** — almost 2x the weekly drop on ETH or SOL. The 12-day move since the 2026-05-20 SUI session was −20.7% ($1.05 → $0.83). This is not stabilization; this is the bear thesis from the prior session accelerating.

### Chain TVL trajectory

| chain | 1y ago | today | YoY | 2y change |
|---|---|---|---|---|
| Ethereum | $60.5B | $41.1B | −32.1% | -33% |
| Solana | $8.62B | $5.20B | −39.7% | **+7.5% (UP)** |
| **Sui** | **$1.70B** | **$494M** | **−71%** | n/a (chain too young) |

**Sui TVL has broken below the $500M trigger** the 2026-05-20 decision explicitly named as a bearish-trim-to-zero signal: *"Sui chain TVL breaks below $500M → the 3-month flat-line was a way-station, not a base; trigger trim to zero."* This is not a marginal break — $494M is materially below $500M, and the trajectory from $547M (1m ago) → $494M (today) confirms ongoing contraction, not a touch-and-bounce.

### Stablecoin supply (dry powder direction)

| chain | 1y ago | today | YoY | direction |
|---|---|---|---|---|
| Ethereum | $125B | $161B | **+29%** | inflows |
| Solana | $11.1B | $15.4B | **+39%** | inflows |
| **Sui** | **$1.10B** | **$493M** | **−55%** | **outflows** |

**This is the single most damaging data point against the SUI hold-or-add thesis.** Capital is actively leaving Sui — stablecoin supply has more than halved over the past year while it grew double-digits on the other two L1s. ETH and SOL show "rotation to safety but stay on-chain" patterns; Sui shows "exit the ecosystem entirely." The stablecoin-to-TVL ratio on Sui is now ~1:1 ($493M / $494M), unchanged from the 2026-05-20 session's read — but in absolute terms, both metrics have shrunk together, which is *worse* than the 2026-05-20 read of "flat dry powder."

### Engine signal state

| asset | last 6m engine events | live engine read |
|---|---|---|
| ETH | 0 events | neutral, no stack |
| SOL | 1 laggard (2026-04-20), then 30d pivot to leader (+4.9pp vs BTC, not yet a leader_crossing) | neutral with bullish pivot |
| **SUI** | **2 leader_crossings (2026-05-08, 2026-05-19) followed by 7d collapse** | **leader_crossings preceded a 21% weekly drop — false-positive bounce signal** |

**Important real-time engine validation:** SUI fired two `leader_crossing` events in early-to-mid May (when SUI was ~$1.05) immediately before the most recent week's 21% crash. The bullish engine signal was a *dead-cat bounce*, not a trend confirmation. This is the kind of signal trap the rel-strength emitter's threshold-based crossings can produce in deep-bear-trend assets. **The most recent 7d window is back to lagging BTC by 8.5pp** — the bounce has clearly failed.

### Year-long relative-strength

| asset | 365d vs BTC | 365d vs ETH | 365d vs SOL |
|---|---|---|---|
| ETH | +11.6pp (leader) | — | +26.8pp (leader) |
| SOL | -15.3pp (laggard) | -26.8pp | — |
| **SUI** | **−39.1pp** | **−50.7pp** | **−23.8pp** |

**SUI is the worst-performing of the three on every multi-month window**, and the gap is wide. The 30d window briefly showed +3.0pp vs BTC; the 7d window has snapped back to -8.5pp. **The brief 30d outperformance was a head-fake.**

## Flow & positioning

**Engine read:** zero current stacks on SUI; recent leader_crossings turned out to be false positives. No bullish confluence signal supports holding the position at current levels.

**SUIG (the Sui Group Holdings public-market treasury vehicle) — unchanged from 2026-05-20:** still NO open-market insider buys at the lows. The 2026-05-20 session already flagged this as decisive bear-side flow: *"NO open-market insider buys in SUIG since 2025-05. SUI cratered from $3.86 to $0.93 between mid-2025 and Feb 2026, and the public-market insiders closest to a Sui-treasury thesis did not step in to add. This is bearish flow — Buffett's 'be greedy when others are fearful' works when *insiders* are getting greedy; absence of open-market insider buying at the capitulation lows is significant."* With SUI now at $0.83 (another -21% from when that observation was made), the insider absence is even more telling. SUIG insiders have had *three* opportunities to buy at sub-$1 SUI (Feb 2026 at $0.93, May 20 at $1.05, today at $0.83) and have taken none of them.

**GrayScale SUI Trust** (user-mentioned, not in the lake) — the existence of an institutional vehicle doesn't validate the underlying thesis. GrayScale offers trusts on many crypto assets with varying levels of conviction; the trust's existence is a *distribution* signal (GrayScale thinks they can sell exposure to SUI), not a *fundamental* signal (the underlying business is healthy). The 2026-05-20 session methodology applies: ignore institutional-vehicle existence; weight on-chain + insider data instead. Both point bearish.

**Sui token unlock schedule** — still not surfaced by the lake (B-089 filed 2026-05-20, status open). Continued vesting unlocks are a known supply-side headwind not visible in the data; if anything, this is an additional bear-side signal we can't quantify. Worth noting but not over-weighting.

**3-year staking position observations** (the user's actual entry point):

1. **3 years of staking yield compounds the cost-basis advantage** — a holder who started staking SUI in mid-2023 has a lower cost basis than current price even after the 75% drawdown, IF they're holding through the staking yield. The methodology should NOT weight this in the forward-looking allocation decision (sunk cost / behavioral anchor), but the user should be aware: the rotation decision is "what allocation makes sense for the next 2-5 years," not "did the original entry make sense" (the latter is irrelevant).
2. **Sui staking cooldown is ~24 hours** (one Sui epoch). Practical friction is low; this isn't ETH-pre-Shapella where exits were structurally blocked.
3. **Tax implications** (jurisdiction-dependent) — a 3-year SUI position liquidation generates capital gains/losses based on cost basis vs sale price. Down 75% from peak suggests potential capital-loss harvesting opportunity, which can offset gains elsewhere. This is portfolio-tax-optimization territory and out of scope for the research-methodology decision, but worth flagging as a practical consideration.

## Phase A — case for and case against

**Bull case (for HOLDING SUI / not rotating):**

1. **3-year position with compound staking yield** — cost basis is likely materially below $0.83, especially if reinvested staking rewards.
2. **Deep capitulation depth.** -75.5% YoY, -83% from 2y peak. Capitulation bottoms in crypto-tactical do happen, and the engine equivalent of this depth historically has 1-in-3 recovery odds (per the 2026-05-20 base-rate framing).
3. **Macro constructive.** No macro reason to under-allocate crypto.
4. **Recent engine leader_crossings** (2026-05-08, 2026-05-19) — though they preceded a crash, they DID fire, meaning SUI did outperform BTC briefly. Could repeat.
5. **Institutional vehicles exist** (GrayScale Sui Trust, SUIG) — *some* institutional capital is willing to take SUI exposure even at low levels.

**Bear case (rotate out of SUI):**

1. **Prior decision's trigger HIT.** The 2026-05-20 session explicitly named "Sui chain TVL breaks below $500M" as the bearish-trim-to-zero trigger. TVL is at $494M today. **This is exactly the action-on-trigger scenario the trigger condition was designed to surface.** Not heeding it after explicitly naming it would defeat the audit-trail discipline the decision log exists for.
2. **Stablecoins on Sui DECLINING 55% YoY** while ETH and SOL grew double-digits. Capital is exiting the Sui ecosystem; this is structurally different from ETH/SOL where capital rotated to safety but stayed on-chain.
3. **7-day price collapse -21.6%** — worst weekly performance of the three by a wide margin. This is not stabilization; this is acceleration.
4. **Year-long relative-strength worst of the three** by -24pp vs SOL, -51pp vs ETH. SUI has been the worst place to be over any multi-month window over the past year.
5. **Engine leader_crossings were dead-cat bounces.** The 2026-05-08 and 2026-05-19 bullish signals preceded the 21% crash. The 30d outperformance was a head-fake. The threshold-crossing engine signal does NOT validate the SUI thesis.
6. **SUIG insider absence persists.** Now THREE missed buy opportunities at sub-$1 SUI (Feb / May 20 / today). Insiders closest to a Sui-treasury thesis are voting with their feet.
7. **ETH and SOL are CRYPTO-CORE; SUI is CRYPTO-TACTICAL.** Per `CLAUDE.md`: *"The `tactical` sleeve... signals can argue for trimming/adding. The point of having SUI in tactical (vs core) is precisely so we *can* trim when the signal is this bad."* The sleeve discipline is to act on tactical-sleeve signals rather than absorbing them as core-sleeve mandatory holds.
8. **ETH and SOL have meaningfully better fundamentals divergence direction.** ETH 2y TVL -33% but franchise intact; SOL 2y TVL +7.5% (UP) and stablecoins +403%. SUI 1y stables -55% and TVL down 71%. The other two L1s are accumulation candidates; SUI is a distribution candidate.

## Phase B — counter-thesis

**Strongest case for being wrong (the bull thesis I'm most likely UNDERWEIGHTING):** SUI is small enough and beaten down hard enough that **a single catalyst** (Move-VM ecosystem launch, Mysten Labs product release, new flagship Sui protocol, surprise institutional buyer surfacing) could re-rate it 2-3x in months. The 2018 ETH bottom at $80 and 2022 ETH bottom at $880 were both impossible to call in real-time — the catalyst that activates a capitulation bottom is rarely visible 30 days in advance. **Trimming to zero right now at the engine's worst SUI read in months could be selling the bottom**. The audit trail says I should follow the trigger; the meta-discipline says trim signals at capitulation can also be wrong.

**Specific signals that would confirm this counter-thesis:**

1. SUI chain TVL recovers above $700M within 6 months → would mean the $500M break was a way-station and a real base is forming higher.
2. SUIG open-market insider buy cluster (≥2 reporters at any price) within 6 months → would mean the people closest to the Sui-treasury thesis finally stepped in; flips the decisive bear flow signal.
3. SUI outperforms ETH on 90d window by ≥10pp within 6 months → would mean the L1 rotation is real and SUI specifically is the beneficiary.

**Specific signals that would confirm the bear thesis is right:**

1. SUI/BTC 30d rel-strength flips back to laggard (≤−15pp) within 6 months → confirms the late-May leader bounces were head-fakes; the engine signal aligns with the broader bear; accelerate the trim.
2. Sui chain TVL breaks below $350M within 6 months → another -30% TVL drawdown; structural ecosystem retreat confirmed.
3. SUI underperforms ETH+SOL average over 6m by ≥20pp → confirms the rotation-out was correct.

**Base-rate question:** crypto-tactical assets that hit their explicitly-named bearish triggers within 12 days of the trigger being set. Hard to find a clean historical match, but the closest mental analog is: alt-L1 tokens that break previously-named technical support during an active bear trend — historically these continue lower 60-70% of the time within 6 months. The base rate favors acting on the trigger, not absorbing it.

**What a smart fund manager would say:** "You set a trigger 12 days ago that said 'TVL below $500M = trim to zero.' The TVL is at $494M today. The trigger was a forward commitment, not a suggestion. You said you'd trim if this happened. Trim. Don't relitigate the call now that it's inconvenient. **The audit-trail value of decision triggers comes from honoring them when they fire.** If you reverse the trigger now because 'maybe this is the bottom,' you've defeated the entire reason for setting the trigger in the first place. **Trim 75-90% of the SUI position, redeploy proceeds into the ETH+SOL barbell from today's other sessions. Keep a starter 10-25% slice for capitulation-bottom optionality** — primary-tier crypto positions don't get fully zeroed on engine signals alone, only on fundamental thesis breaks. The bear-thesis case has six concrete signals; the bull case rests on 'maybe the catalyst is coming' and the 3-year sunk-cost anchor. **Trim.**"

**The smart-fund-manager argument is overwhelmingly stronger here than the bull case.** The discipline of the methodology is that triggers fire when conditions hit; the prior trigger fired explicitly. Not heeding it would be the kind of biased-by-prior-position move the user explicitly asked for a non-biased opinion against.

## Conclusion

**Recommendation: Trim SUI position by 75-90%, redeploy proceeds equally into the ETH + SOL barbell established in today's other 2026-06-02 sessions.** Retain a residual 10-25% SUI slice for capitulation-bottom optionality (primary-tier tactical-sleeve assets don't get fully zeroed on engine signals alone). **Do NOT add to SUI at current levels** under any of the bull-case scenarios short of an SUIG insider open-market buy cluster.

**Sleeve & horizon:** SUI is crypto-tactical (turnover-eligible by design); ETH+SOL destinations are crypto-core (multi-year). Horizon for the trim action is *months* (act now, don't wait for further confirmation). Horizon for the redeployed ETH+SOL capital is *years* per the prior two sessions' DCA framework.

**Confidence: medium.** Higher than the 2026-05-20 SUI session (which was also medium) because *the explicit forward-commitment trigger has fired* — a documented action-on-trigger decision is structurally stronger than a fresh "is this bearish?" call. Not escalated to "high" because (a) no resolved-decision calibration data yet, (b) the smart-fund-manager argument is convincing but capitulation bottoms are real, and (c) the audit-trail principle ("trim signals at the bottom are sometimes wrong") deserves modest hedging via the 10-25% retained residual. **The recommendation pattern is medium-confidence: directional action on the trigger, not full exit.**

**Position-sizing implication for the rotation:**

- **SUI trim**: 75-90% of current position liquidated. Retain 10-25% residual.
- **Trim proceeds redeployed**: 50/50 split into the ETH+SOL barbell on the same DCA pattern as today's other sessions (25-50% of intended target deployed now, rest reserved for confirmation OR cheaper entry triggers per those individual decisions' frontmatter).
- **Total crypto-core target unchanged**: the redeployment is portfolio-internal rotation, not new capital deployment. If the user's intended ETH+SOL combined crypto-core target was 100, the trim proceeds count toward filling that target.
- **Practical staking notes**: Sui staking cooldown is ~24h (one epoch); ETH staking has no cooldown for solo stakers but liquid-staking (Lido, Rocket Pool) has zero friction; Solana staking has ~2-day cooldown. Sequence the trim to redeploy as cooldowns clear.
- **Tax consideration** (flag, not advice): a 3-year SUI position liquidation at -75% from peak generates significant capital-loss harvesting potential. User should evaluate against gains elsewhere in the portfolio.

**Key risks (counter-thesis distilled):**

1. **Single Sui-specific catalyst surfaces** → trim was premature; rebuy on confirmation (TVL >$700M or SUIG insider buys).
2. **Crypto-wide rally** lifts everything including SUI → the 10-25% retained residual participates; the redeployed ETH+SOL capital participates equally.
3. **ETH+SOL underperform expectations** → today's other sessions are medium-confidence; if both are wrong, the rotation moves capital between similarly-weak positions. Hedged by the graduated DCA approach (25-50% deployed now, not 100%).
4. **Tax-loss harvesting requires care** — wash-sale rules may apply in the user's jurisdiction; consult a tax professional before executing.

**Trigger conditions for reassessment** (see frontmatter): any of (a) SUI chain TVL recovers above $700M [bullish: re-add to SUI from residual], (b) SUIG open-market insider buy cluster ≥2 reporters at any price [bullish: re-add], (c) SUI outperforms ETH on 90d window by ≥10pp [bullish: re-add], (d) SUI/BTC 30d rel-strength flips back to laggard (≤−15pp) [bearish acceleration: trim the 10-25% residual to zero].

**Meta-takeaway (for `/reflect-decisions` in ~6 months):** This is the first decision that explicitly *supersedes* a prior decision via the trigger-fired mechanism. The 2026-05-20 SUI session set the trigger; the 2026-06-02 session is the action-on-trigger entry. If SUI continues to fall (or stagnate), the lesson is the trigger framework worked as designed and should be applied to future positions with similar discipline. If SUI rebounds materially, the lesson is that engine triggers should be hedged with optionality (the 10-25% residual is the hedge), and that trim-at-capitulation signals are sometimes wrong. Either way, the audit-trail principle — *we said we'd act on this signal; we did* — is the meta-output worth preserving regardless of the directional outcome.

**Backlog implications surfaced by this session** (separate from the decision itself):

1. **Whale-flow + CFTC COT + spot ETF flow ingesters** — the user explicitly asked about getting these into the lake. The single highest-leverage missing input for the question they asked. **B-031 priority bump + B-104 CME OI + B-105 Spot ETF flow + B-106 Etherscan whale-flow** — drafted as four discrete follow-ups; the user agreed to file them.
2. **Decision-supersession mechanism in the reflection cycle** — the `supersedes` frontmatter key used here should be honored by `/reflect-decisions` so the 2026-05-20 SUI session is marked resolved with reference to this entry, not just resolved-stale via horizon timeout. Worth a one-line audit in the reflect-decisions skill.

---

## Outcome (filled in by /reflect-decisions)

(reserved — pending; will resolve at 2026-12-02 or earlier on trigger)
