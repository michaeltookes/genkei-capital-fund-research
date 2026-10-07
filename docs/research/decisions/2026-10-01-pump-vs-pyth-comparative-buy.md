---
date: 2026-10-01
asset: PUMP
sleeve: crypto-tactical
horizon: months
action: buy
reflection_benchmark:
  type: destination_basket
  label: SOL alternative (100%)
  assets:
    - ticker: SOL
      weight: 1.0
confidence: medium
status: inactive
supersedes: 2026-09-06-pump-fun-buyback-assessment
activation_condition: "Flips to pending on Michael's FIRST PUMP fill (either the discipline half-starter or the conviction full starter described in the Conclusion); the actual fill date and price become the reflection baseline — record a reflection_start block with a fill-synchronized SOL quote, per the HYPE precedent. On activation, also move PUMP from `crypto_price_targets` into `crypto.secondary` and add the `pump` parent slug (not just `pump.fun`) to `protocols:` so the group revenue series that actually funds the burn is lake-checkable. Both entries are VETOED while any of these stand: an adverse escalation in Aguilar v. Baton (class certification with damages scoped to fee revenue, an injunction touching the fee/burn pipeline, a receiver, or a settlement redirecting revenue away from the burn contract); verify the docket (CourtListener 1:25-cv-00880; last known entry 2026-09-11) before filling. If no fill by 2026-12-31, flip this record to resolved with a no-entry note."
trigger_reassessment: "AFTER activation — ADD only on the October group print (DeFiLlama parent `Pump` monthly Revenue, first week of Nov) >= $50M with pump.fun leading launchpad daily fees on a majority of October days and no adverse Aguilar development. EXIT / reassess if: parent `Pump` monthly Revenue < $35M for 2 consecutive months (procyclical collapse — the burn shrinks exactly when needed); any rival launchpad takes > 60% of cross-chain launchpad Fees for a full month; SOL 30d relative strength beats PUMP by > 20pp from the fill baseline; Aguilar escalates per the veto list. HARD CALENDAR: the 50%-of-revenue burn contract expires 2027-04-29 — if no on-chain renewal or successor commitment is published by 2027-03-31, exit the position before 2027-04-29 regardless of price; a 'discretionary' buyback is a different asset from the one this file buys. BEFORE activation — do NOT chase a daily close above $0.0070 (+20% from here); re-run the session instead."
related:
  - decision: 2026-10-01-pyth-reentry-trigger-fired-reserve-v2
  - decision: 2026-09-02-robinhood-chain-tokenization-assessment
  - decision: 2026-07-27-hyperliquid-hype-initiation
  - data: coingecko.market_data
  - data: defillama.protocol_fees
  - data: fred.observations
---

# PUMP vs PYTH — which is the better buy right now, and a discretionary PUMP reassessment

## Frame

Michael's prompt, after this morning's PYTH session: "which is the better buy right now between Pyth and Pump.fun?" PUMP has also "gone up pretty significantly" since the desk declined to buy it on 2026-09-06, and Michael notes, correctly, that it is the second name this month the desk said not to touch that then rallied ~40%. Two things are being asked. **(1) The PUMP re-run** — the 9/06 file (status: inactive, staged buy) required both a close above $0.0053 and Pons still out-earning pump.fun. The price leg occurred on 2026-09-29, but pump.fun had retaken the fee lead on Sept 25, so the full clause did not fire. This user-requested comparison is a discretionary reassessment that supersedes the 9/06 record. **(2) The comparison** — at today's prices, with the PYTH add already logged this morning, where does the next tactical dollar go? Sleeve for both: crypto-tactical (PUMP can never be core — a memecoin launchpad is the casino, and the desk's philosophy conflict is resolved by sleeve, not denied). Horizon: months, bounded for both by dated supply/contract events in spring 2027. **Written before the evidence, what would change the PUMP answer:** whether the Pons competitive shock that stopped the desk on 9/06 has resolved in the data, and whether the revenue that funds the burn held through it. **What would change the comparison:** if PUMP's yield advantage is being eaten by vesting faster than the burn retires supply.

## Macro context

Unchanged from this morning's PYTH file: `genkei macro-regime` (2026-09-28) **risk_on 4/4** with deterioration underneath — DGS10 5.26% (+53 bp/30d), **HY OAS 3.02% (+42 bp/30d)**, USD 120.3, VIX 16.1; BTC $83,959 (lake, 10/01). Both candidates are high-beta alt longs into widening credit; macro favors neither over the other but argues for staging any new money and for the hard exit rules below. The tape remains single-narrative leadership (SUI, PYTH, LINK, RENDER on the 9/30 momentum board) — PUMP is not on the board because it is price-target coverage only, but at +51.7%/7d (CoinGecko) it would top it.

## Fundamentals — what changed on PUMP since Sept 6

**The Pons shock resolved — in pump.fun's favor, and fast.** DeFiLlama daily launchpad fees (API pull this session): Pons out-earned pump.fun for **27 straight days (Aug 29 → Sept 24)**, peaking at **$11.4M/day on Sept 5** vs pump.fun's $0.68M (the desk's 9/06 session fell on the worst day of the attack). Pons then collapsed — **$1.3–1.7M/day by Sept 25–Oct 1, −88% from peak** — and **pump.fun has led on every day since Sept 25 (six consecutive sessions, $1.7–2.7M/day)**. Pons's September total was $137M of *fees* but only **$23.4M of revenue** (a 70/30 creator split; 17% take) against pump.fun's **$46.4M fees / $33.1M revenue (71% take)** — the "subsidy-flavored, novelty-driven" read in the 9/06 Phase B was right, and faster than the four-week stabilization bar the file set. ([DeFiLlama pump.fun](https://defillama.com/protocol/pump.fun), [Pons](https://defillama.com/protocol/pons))

**Revenue held through a month-long attack — and the 9/06 activation bar was mis-specified.** The 9/06 file keyed activation to the *launchpad-only* `pump.fun` slug (≥$40M/month Revenue). September printed **$33.1M** on that slug — a miss — but **the burn is funded by 50% of *group* net revenue**, and DeFiLlama's parent **`Pump` entity printed $53.1M of September revenue (fees $165.4M)** vs $58.1M in August (−9% MoM) — PumpSwap alone $13.7M. Cross-check: the token dashboard's late-September burns of **$1.02–1.46M/day** are ~50% of the parent's $2.3–3.1M/day revenue, not of the launchpad's $1.2–2.0M — the parent slug is the right series and this file re-keys every threshold to it. Resilience read: group revenue fell 9% in the month a Robinhood-distributed rival took the daily launchpad lead for four weeks; that is a stronger datapoint than a quiet month at $40M would have been. ([DeFiLlama Pump parent](https://defillama.com/protocol/pump); [pump.fun token dashboard](https://pump.fun/pump-token))

**The burn: now $470.35M cumulative, 169.21B PUMP, 16.92% of max supply destroyed.** Up from ~$446–449M on 9/06 → **~$22–24M spent in 25 days (~$0.9M/day, ~$27M/month)**; the late-September run-rate is higher (**~$1.19M/day ≈ $35M/month**). Yield on the $2.71B market cap: **~12% (monthly average) to ~16% (late-Sept run-rate) per year** — versus PYTH's conditional ~0.9% scenario, which assumes repeated August-sized remittances fully allocated to purchases. The contract is irreversible until **2027-04-29**, after which the dashboard itself says future purchases "carry no obligation."

**The dilution reality the yield headline hides.** Circulating supply is 465B; the **Oct 12 unlock releases 9.17B (~2.0% of circulating, ~$53M)**, and that is the *monthly* linear cadence through 2029 (team 20% + investors 13% ≈ 330B still vesting; ~45% of max supply unvested on 9/06). At $0.0058 the monthly burn retires **~4.6B tokens — about half of what vests each month**. So PUMP is deflationary against *max* supply and still **net-inflationary against circulating supply (~+1%/month) at this price**; the burn only outpaces vesting below roughly $0.003, or if revenue roughly doubles. The 9/06 file's "one of only two programs that actually shrink net supply" claim needs this qualifier.

**Legal: unchanged, unresolved, still a veto.** *Aguilar v. Baton* (S.D.N.Y. 1:25-cv-00880): RICO claims proceeding against Baton and founders per the Aug 31 opinion (Solana/Jito dismissed); relief sought includes **rescission of all PUMP transactions and a receiver**; class-certification motion is 60+30 days after the answer or MTD decision, so plausibly Q1 2027. Last known docket entry **Sept 11**; the fetcher could not read CourtListener this session (403) — verify before any fill. The widely-cited **Sept 25 SEC staff FAQ** on token buybacks is staff-level, not a rule, and was **tightened on Sept 28 to require "no central party"** — pump.fun has one (Baton Corp), so the guidance is less protective for PUMP than the rally narrative implies. ([CourtListener docket](https://www.courtlistener.com/docket/69593359/aguilar-v-baton-corporation-ltd-dba-pumpfun/); [SEC FAQ coverage](https://cryptobriefing.com/sec-staff-says-certain-crypto-buybacks-and-staking-tokens-fall-outside-securities-laws/))

## Flow & positioning

Lake tape, 9/06 → 10/01: **PUMP $0.00402 → $0.00567 (+41%)**, peak **$0.00592 on 9/30**; live CoinGecko $0.00582, **+51.7%/7d, +33.5%/30d**, market cap $2.71B, 24h volume $330M (~12% of cap — deep). Counterfactuals over the same window: **SOL $106.49 → $118.01 (+10.8%)**; **PYTH $0.0562 → $0.0772 (+37%)**. The move came in two legs: a quiet $0.0038–0.0045 base from Sept 17–26 while Pons was still ahead, then +38% in four sessions (Sept 26–30) as the fee flip became visible and the SEC FAQ headline landed. Both desk declines this month (PUMP 9/06, PYTH 9/17) preceded ~+40% moves — logged in the reflection section.

## Phase A — the comparison, side by side

| | **PYTH** (add logged this morning) | **PUMP** |
|---|---|---|
| Value-return mechanism | Reserve V2: initial treasury conversion required; future revenue allocations discretionary | 50% of group net revenue → buy-and-burn, irreversible to 2027-04-29 |
| Buyback yield on mcap | **~0.9%/yr scenario** if August remittances repeat and are fully allocated; not a committed yield | **~12–16%/yr**, scaling with memecoin volume |
| Revenue base | ~$0.72M August reported gross revenue; $0.434M DAO distribution; customer cash collections and churn unverified | ~$53M/month group net; **−80% peak-to-trough twice in two cycles**; −9% MoM in Sept |
| Market cap / annual revenue | ~71x annualized August reported gross revenue (not audited cash revenue) | **~4.3x** group net revenue |
| Competitive position | #2 oracle; LINK rallying alongside (+28%/14d) | Repelled a Robinhood-distributed attack in 4 weeks; #1 Solana app by revenue |
| Supply | Clean to **2027-05-19 cliff: +27% of circulating at once** | **~2%/month linear vesting through 2029**; burn offsets ~half at this price |
| Legal / regulatory | None material | **RICO proceeding; rescission + receiver sought**; SEC FAQ carve-out likely doesn't apply (central party) |
| Entry extension | +37% since 9/17, −7% off peak, 3d −5.8% | +41% since 9/06, **+52%/7d**, 2% off peak |
| Near-term binaries | September revenue and conversion/churn disclosure expected ~Oct 8; ~Nov 1 V2 report must separate treasury sweep from new-revenue purchases | Oct 12 unlock (9.17B); October group revenue print (~Nov 1); contract-renewal signal before Apr 2027 |
| Desk philosophy fit | Infrastructure with a mechanism — the sleeve's thesis | The casino's own buyback — sleeve-quarantined, never core |

**Case for PUMP over PYTH:** the yield gap would be ~15x against PYTH's fully allocated remittance scenario; the revenue multiple gap is ~16x using reported figures with different gross/net definitions; the thing that stopped the desk (Pons) resolved in the data; the burn is contractually locked for seven more months; liquidity is deep enough to size and exit. **Case for PYTH over PUMP:** recurring revenue vs the most cyclical cash flow in crypto; no legal tail; the mechanism just inflected (the trade is *early* in its evidence, PUMP's is *late* in its rally); a 7% pullback vs a 52%-in-a-week chart.

## Phase B — counter-thesis and reflection

**Strongest case against buying PUMP here:** the desk is about to buy, +52% in a week, the asset whose revenue falls 80% whenever the memecoin cycle turns, days before a ~$53M unlock, with a RICO case seeking rescission of every token sale, on a yield that is currently *outpaced by insider vesting* — and it is doing so because it feels bad about missing the move. The 9/06 Phase B said the counter-side "most overweights an August revenue print as if it were a run-rate"; September's $53M is one month lower, and the burn-contract expiry in April 2027 means the single best feature of this asset has a seven-month shelf life unless Baton renews it voluntarily. **Mitigations:** stage the entry so half the capital waits for either the pullback or the October print; key every exit to the *parent* revenue series monthly; make contract renewal a hard calendar rule, not a hope; keep the Aguilar veto absolute.

**Strongest case against preferring PYTH:** at ~71x annualized August reported gross revenue, PYTH is priced as a growth story with undisclosed churn and unverified customer cash collections; its ~0.9% purchase scenario depends on future allocations that V2 does not mandate; and its +27% cliff is a single-day event, which markets front-run — PUMP's dilution is at least linear and already in the daily tape.

**Reflection (both files):** the desk's 9/06 and 9/17 calls were each followed by ~+40% in the named asset within two to four weeks. On PUMP the *reasoning* was right (Pons was a subsidy-flavored shock that would fade) and the *bar* was wrong (a four-week stabilization test plus a mis-specified revenue slug, when the daily fee series resolved the question in ten days). On PYTH the reasoning itself was wrong (see this morning's file). The common thread is the one Michael keeps flagging: in a tape that pays for mechanisms, the desk has demanded resolution of headline risk before entering and has been systematically late. The fix is not to abandon conditions — HYPE, entered on conditions, is the sleeve's best trade — it is to key conditions to the *fastest* decisive series (daily fees, on-chain purchases) rather than to calendar waits.

## Conclusion

**Direct answer: PUMP is the better buy right now on fundamentals per dollar; PYTH is the lower-risk entry today. For a tactical-sleeve dollar with a months horizon, the desk picks PUMP — staged — and keeps the PYTH initial add explicitly discretionary, with both disclosure and recurring-funding gates required for its second discipline tranche.** The reason the desk declined PUMP on 9/06 — an unresolved, Robinhood-distributed competitive attack — has been answered by the data: Pons's fees are down 88% from peak and pump.fun has led every day since Sept 25, while group revenue held $53M through the attack month. The mechanism gap is not close: ~12–16% of market cap per year burned under an irreversible contract, at ~4x revenue, versus PYTH's conditional ~0.9% purchase scenario at ~71x annualized reported gross revenue. PYTH's recurring allocations are uncommitted and its multiple uses gross rather than group net revenue, so these are indicative comparisons. What keeps this from a full-conviction market buy is also not close: +52% in a week, a 9.17B unlock on Oct 12, vesting that currently outruns the burn at this price, a live RICO action, and a burn contract that expires 2027-04-29.

**Sizing (discipline and conviction, per Michael's standing instruction; "starter" = tactical-secondary weight, the same unit HYPE was initiated at): Discipline:** half a starter on a **pullback to $0.0045–0.0050** (the Sept 17–26 base top / Aug 24 high), the other half on the **October parent-revenue print ≥ $50M** (~Nov 1) with pump.fun still leading daily fees; if neither arrives and PUMP closes above **$0.0070**, re-run rather than chase. **Conviction:** a full starter at market now (~$0.0058), treating the Oct 12 unlock and the Nov 1 print as hold/cut tests, with the first add only on the ≥ $50M October print. **Both paths:** exit if parent `Pump` monthly revenue prints < $35M for two consecutive months, if any rival takes > 60% of launchpad fees for a month, if SOL beats PUMP by > 20pp over 30 days from fill, or on any adverse Aguilar step; and **exit before 2027-04-29 unless a renewal or successor to the 50% burn contract is published by 2027-03-31**. Verify the docket before the first fill. **If Michael deploys across both names today, the split that matches this file's read is roughly 60/40 PUMP/PYTH of the new money** — PUMP for expected return, PYTH for the lower tail — with each name's own rules governing.

Sleeve: crypto-tactical. Horizon: months (to the spring-2027 contract/cliff dates). Confidence: **medium** — the competitive and revenue evidence is primary and decisive, the mechanism math is quantitative, but the entry is extended, the dilution nuance cuts against the headline yield, and the legal tail is unpriceable. **Top risks:** (1) memecoin volume rolls over and the procyclical burn shrinks into a falling price (the realistic bear, seen twice); (2) Aguilar escalates toward the fee pipeline; (3) Baton lets the burn contract lapse in April 2027 — the asset this file buys stops existing on that date.

**Housekeeping:** the 2026-09-06 file is closed (`status: resolved`, `superseded_by` this file, no `trigger_fired_at`: the breakout-plus-Pons-lead predicate was not met) with a note that no activation branch fired and that its revenue slug was mis-specified; its 12/31 reminder in `docs/research-questions.md` is resolved and this file carries its own 2026-12-31 expiry. On any fill: send date, price, and size for `reflection_start`, and the watchlist/protocol moves in `activation_condition` get made.

---

## Outcome (filled in by /reflect-decisions)

(reserved — inactive until first fill)
