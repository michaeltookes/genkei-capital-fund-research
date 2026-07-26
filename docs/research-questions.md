# Open research questions

A lightweight, append-only scratch log for threads worth a later look that surface mid-session and would otherwise evaporate. The **agent** (Claude, during a `/research` session or any analysis) appends questions here; **Michael** triages them by flipping status in place.

This is the lightweight cousin of the decision log (`docs/research/decisions/`, see `docs/research/README.md`): no frontmatter validator, no reflection cycle, no audit guarantee. It's a structured scratchpad — the point is zero friction to append. A question graduates out of here when it either becomes a real `/research` decision file or gets answered inline below.

## Entry format

Newest entries on top. One entry per question:

```markdown
### YYYY-MM-DD — <one-line question>
- **status:** open | resolved
- **context:** which session / decision / asset / backlog item surfaced this, and why it matters
- **outcome:** (filled in when resolved — one line on the answer or where it went)
```

**Appending:** add a new `###` block at the top of the log below. **Resolving:** flip `status: open` → `status: resolved` and add an `outcome:` line — don't delete the entry, the trail is the value.

---

## Log

### 2026-07-25 — Token-necessity test across the core sleeve: which holdings' tokens are the product vs a loyalty coin attached to a business?
- **status:** open
- **context:** Surfaced in the VIRTUAL hold-vs-ETH-swap session (`2026-07-25-virtuals-protocol-hold-vs-eth-swap`). Dissecting VIRTUAL's indirect value accrual led Michael to the same skepticism about LINK ("a stock without the legal claim") and an emerging view that only BTC/ETH/SOL clear the bar — tokens that ARE the product (money / gas+burn / fees+MEV to stakers) vs tokens grafted onto a valuable business with no enforceable claim on its cash flows. Questions for a future `/research` pass: (1) **LINK** — does CCIP payment abstraction (enterprises pay fiat/stables, protocol converts) permanently cap token demand, and is mandatory large-scale staking for CCIP security a real path to giving the token a job, or a perpetual what-if? Reassess against `2026-06-04-chainlink-position-reassessment`. (2) **JUP** — the most LINK-shaped holding in the book (real business, real fees, indirect token capture); apply the same test before LINK, since JUP lacks even the staking what-if. (3) **ZEC** — passes the test by design (monetary bet, token is the product); confirm the frame rather than lumping it into "everything else is noise." Michael is holding LINK for the staking/CCIP what-if meanwhile — this thread governs whether that stays a conviction position or gets resized as a lottery ticket.

### 2026-07-06 — Does the "BTC : ZEC :: HTTP : HTTPS" privacy thesis hold up, and does ZEC trade as a BTC-analog?
- **status:** open
- **context:** ZEC added to the crypto-core watchlist 2026-07-06 (primary tier, buy-and-hold) — a BTC-analog by monetary design (21M cap, PoW, halvings) around zk-SNARK privacy. Questions for a `/research` session: (1) **correlation** — how much of ZEC's move is BTC beta vs idiosyncratic privacy-narrative alpha (currently +14.7% rel-strength vs BTC over 30d; ZEC $453, mcap rank ~15, $7.6B)? (2) **adoption signal** — is there measurable usage (shielded-pool share, tx counts) beyond price to validate the thesis, or is it pure narrative? (3) **bear case** — regulatory / privacy-coin delisting risk (several exchanges have delisted ZEC historically; Coinbase currently lists it). Data now in the lake at full crypto parity: `coingecko.market_data` + `coinbase.candles` (2020-12→present) + GDELT news via `gdelt_terms`.

### 2026-06-24 — Is the `yahoo.candles` NOW (ServiceNow) magnitude bug isolated, or does it affect other tickers?
- **status:** resolved
- **context:** Surfaced by the B-118 reflection dry run and tracked as backlog **B-124**. `yahoo.candles` carries NOW at ~$101–118 across 2026 where the real security trades ~10× higher (~$1,000); the IPO-date row matches exactly, so it's the right instrument at the wrong magnitude. Return-based reflection alpha cancels a constant scaling offset (why B-124 is low priority), but any absolute-price logic (valuation screens, position sizing, alert thresholds) would be misled. Worth a spot-check of ~5–10 watchlist equities' latest `adj_close` vs an external reference to tell "isolated to NOW" from "systematic split-adjustment bug."
- **outcome:** Neither isolated nor systematic — **there was no bug** (B-124 audit, 2026-07-05, `docs/sources/yahoo.md`). Spot-checked 6 equities vs *current* external references: NOW ($106) and MU ($976) are real 2026 AI-market prices; 4 of 6 matched to the penny. The "10× off" was a stale-reference artifact — comparing correct current data to a pre-2026 recollection. `adj_close` is correctly split + dividend adjusted. No code change, no re-backfill.

### 2026-06-24 — Does CME futures open interest lead spot crypto on institutional rotation?
- **status:** open
- **context:** Surfaced during the ETH/SOL "OG sellers" research sessions and tracked as backlog **B-104** (deferred — the CmeWS endpoint is now TOS-blocked to non-browser requests). The open question is analytical, not just sourcing: *if* a free daily OI feed reappears, does total-institutional-exposure (daily OI trajectory) actually lead spot price turns, and how does it relate to the weekly CFTC COT positioning view (B-031, shipped)? Answering it would tell us how much to invest in reopening B-104 vs. leaning on COT alone.

---

*Seed entries above demonstrate the format. Append new questions at the top of the Log section.*
