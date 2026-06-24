# Research Methodology

The structured checklist Claude follows for any investment-research session in this repo. The whole project is built so that asking a real question and producing a defensible answer is fast and disciplined — this is what makes the disciplined part discipline.

Loaded automatically by the `/research` skill. Anyone is free to read it directly to understand how a session should run.

**Three rules that drive everything below:**

1. **Query the lake first.** Genkei is a queryable financial-data lake (see `CLAUDE.md`). Never hand-wave a number you could pull. Every claim in a conclusion should be backed by a `genkei …` command whose output you saw.
2. **Two phases, not one.** Phase A builds the case (for AND against). Phase B explicitly tries to break it. Skipping B → premature consensus, which is the failure mode TradingAgents' multi-agent debate was working around.
3. **Tag the horizon.** Every conclusion specifies the sleeve (equity core / crypto core / crypto tactical / macro-aware) and the horizon (weeks / months / years). Without a horizon, the reflection cycle (`/reflect-decisions`) can't measure outcome.

---

## 0. Frame the question (≤ 5 minutes)

Before any tool calls, write down — in the session, out loud — answers to these. Don't skip even if "obvious."

- **What's the asset / cohort?** A specific ticker (AAPL, BTC, DGS10), a sleeve, a sector, or a cross-asset relationship?
- **What's the underlying question?** "Should I buy?" "Why did X move?" "Is this regime persisting?" "What's the bear case I'm missing?"
- **What sleeve does this inform?** Equity core (Buffett-style hold), crypto core (BTC/ETH/SOL/LINK), crypto tactical (SUI/PYTH/RENDER), or macro-aware (informs everything)?
- **What horizon are we deciding on?** Days, weeks, months, years? If the answer is "I don't know" — the session is too vague; either tighten the question or commit to the longest horizon by default (years for equity core, months for crypto tactical).
- **What would change my mind?** Write down the *specific* observation that would flip the conclusion before you start. If you can't articulate that now, you'll rationalize whatever you find later.

Output a one-paragraph frame at the top of the eventual decision file (see `docs/research/decisions/_template.md`).

---

## 1. Macro context (always)

Even single-asset questions have a macro spine — Genkei's stated edge thesis (`CLAUDE.md`) is that equities and crypto are downstream of macro. Skip this only for tactical-horizon (≤ 2 weeks) questions where macro doesn't have time to matter.

**Default queries (always run for any new asset/cohort):**

```
genkei macro --series DGS10 --since YYYY-01-01     # risk-free benchmark + curve
genkei macro --series T10Y2Y --since YYYY-01-01    # curve slope (inversion = recession signal)
genkei macro --series VIXCLS --since YYYY-01-01    # equity volatility regime
genkei macro --series DTWEXBGS --since YYYY-01-01  # USD strength (crypto + EM equity inverse)
genkei macro --series BAMLH0A0HYM2 --since YYYY-01-01  # HY credit spread (risk-on/off)
```

**Start with the classifier, then drill into series.** `genkei macro-regime` (B-059) collapses the series above into a risk-on / risk-off / mixed call for the latest day; `--since/--until` gives the trajectory and `--summary` the regime distribution over a range. Use it as the opening read, then pull 2-4 raw series to understand *why* the regime is what it is — the per-series queries are where the nuance lives.

**Pick 2-4 of these as relevant for your question.** If you're researching crypto, USD index + HY spread matter more than the curve. If you're researching a bank stock, the curve is the whole story.

**Use vintage-awareness when timing matters.** `genkei macro --series GDPC1 --as-of 2024-06-15` answers "what did we believe about Q1 GDP on 2024-06-15" — critical for back-testing or for reading old decisions in context.

**Output a one-paragraph macro regime call.** Example: *"Curve has steepened 50 bps since Jan; HY OAS at 320 bps — credit not pricing recession; VIX 14 — vol regime benign. Net: risk-on continuing, no immediate macro headwind for cyclical equity longs."*

---

## 2. Asset fundamentals

Split by sleeve. Always pull the data; don't paraphrase from memory.

### Equity (`genkei filings`)

- **Latest 10-K + most recent 10-Q:** `genkei filings --ticker AAPL --form 10-K --limit 1`, same for `--form 10-Q`. Read the actual filing — `accession_number` + `primary_document` gives you the SEC.gov path.
- **Revenue + earnings trajectory:** `genkei filings --ticker AAPL --concept us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax --unit USD --since 2020-01-01`. Plot the trajectory in your head. Is it accelerating, decelerating, or seasonal?
- **Margin trajectory:** Query `us-gaap:GrossProfit` / `us-gaap:OperatingIncomeLoss` / `us-gaap:NetIncomeLoss` over the same window.
- **Balance sheet:** Cash + debt → `us-gaap:CashAndCashEquivalentsAtCarryingValue`, `us-gaap:LongTermDebt`. Net cash position is the Buffett-mentality covering-expenses screen.
- **Price history:** `genkei prices --ticker AAPL --since 2024-01-01` — equity tickers route to `yahoo.candles` (B-092); `price_usd` is the adjusted close.

### Crypto (`genkei tvl`, `genkei prices`)

- **Chain TVL trajectory:** `genkei tvl --chain Ethereum --since 2024-01-01`. Same for Solana, Bitcoin, Sui per asset relevance.
- **Asset price + market cap:** `genkei prices --ticker BTC --since 2024-01-01`. Note 30-day and 90-day momentum.
- **Per-protocol TVL:** `genkei tvl --protocol aave-v3 --since 2024-01-01` — populated for the watchlist `protocols:` slugs (oracle / lending / DEX / liquid-staking / CDP categories; B-081). For protocols outside the watchlist, `defillama.protocol_tvl` won't have rows — extend the watchlist or note the gap rather than substituting chain TVL.
- **Protocol fees + revenue:** `defillama.protocol_fees` (B-083) carries daily `fees_usd` / `revenue_usd` per watchlist slug — no typed surface yet, query via `genkei query`. `genkei revenue-divergence` (B-062) flags protocol-revenue vs token-price divergence for watchlist protocols with a token mapping.

---

## 3. Flow & positioning (who's buying / selling)

The "who's positioning" angle. This is where insider data + on-chain data + stablecoin supply matter.

### Insider flow (`genkei insiders`, `genkei insider-clusters`)

- **Recent insider activity for the ticker:** `genkei insiders --ticker AAPL --since 2024-01-01`.
- **Buy clusters across the watchlist:** `genkei insider-clusters --since 2024-01-01 --min-reporters 3`. Looking for the rare conviction signal (multiple officers buying same week is one of the strongest signals on Genkei's edge list).
- **Sell clusters with context:** `genkei insider-clusters --sell --since 2024-01-01 --min-reporters 4` — sell clusters happen all the time, but coordinated CEO+CFO+COO selling within a week is worth noting.

### Cross-source positioning (when relevant)

- **Stablecoin supply** (proxy for "dry powder on-chain"): `genkei stablecoin-flow --chain Ethereum --since 2024-01-01` for one chain's trajectory, `--all-chains` for the comparative snapshot + rotation signal, `--by-stablecoin` for the per-asset (USDT/USDC/DAI) split (B-108).
- **TVL drawdown signal:** `genkei tvl-drawdown [--chain Ethereum]` (B-058) — runs the chain-level drawdown classifier on each (chain, native token) watchlist pair; TVL drawdowns precede rotations (one of the four edge types). For protocol-level drawdowns, pull `genkei tvl --protocol <slug>` and eyeball the trajectory.

---

## 4. Cross-source signals (Phase A — case for and against)

This is the heart of the methodology. Look for places where two or more sources agree or contradict. Agreement strengthens; contradiction is where the real signal often is.

**Examples of meaningful cross-source patterns:**

- Insiders buying + revenue accelerating + macro tailwind → bull triangle.
- Insiders selling + revenue still up + macro deteriorating → top-of-cycle warning.
- Macro hostile + asset price up + insiders silent → check what we're missing; the price is telling us something the slow signals haven't caught up to.
- All three sources point the same way + price already moved → the easy money is gone; what's the entry?

**Write down both sides explicitly.** Don't just list the bull case — list the bear case with the same rigor. Phase A is *neutral assembly*, not advocacy.

---

## 5. Counter-thesis (Phase B — what would make this wrong?)

Phase B is the discipline. Even if Phase A points strongly one way, force yourself to construct the strongest counter-thesis possible. Three angles:

- **What signal am I most likely overweighting?** If insider buys + 2 macro tailwinds + 1 fundamentals datum → you're probably overweighting the insiders (rare + dramatic feels more important than it is). Reweight.
- **What would a smart, well-informed opponent say?** If you can't imagine someone reasonable disagreeing, you haven't tried hard enough. Imagine a fund manager arguing the opposite at lunch. What's their best line?
- **What's the historical base rate?** "Insider cluster buys precede +X% returns" — true on average across many cases, but how often does the specific situation we're in match the historical pattern? Is something different this time? (Usually "this time is different" is wrong — but not always.)

**Counter-thesis must be specific.** Not "macro could deteriorate" — *"if DGS10 breaks 5.0%, this rotation reverses; current 4.47%, watch 4.80%+."*

---

## 6. Conclusion

Pull everything into a single conclusion. Required components:

- **Recommendation.** Buy / hold / sell / no-trade / watch. Be specific.
- **Sleeve.** Equity core / crypto core / crypto tactical / macro-aware / no-action.
- **Horizon.** Weeks / months / years.
- **Confidence.** Low / medium / high — calibrated against your own track record from prior decisions (look at the most recent reflections — are you usually over-confident?).
- **Key risks (the counter-thesis distilled).** Top 2-3 specific things that would flip the conclusion.
- **Position-sizing implication** (if buy/sell). What share of the sleeve does this warrant?
- **Trigger condition for reassessment.** What observation would make you revisit this before the horizon? Either a price level, a macro level, or a new filing.

Output: 2-4 paragraphs at the bottom of the decision file. Concise. The reflection cycle reads this in 6-12 months and needs to know what you actually claimed.

---

## 7. Decision-log entry (always)

Append a new file to `docs/research/decisions/<YYYY-MM-DD>-<topic>.md` using `docs/research/decisions/_template.md`. The frontmatter is the contract:

```yaml
---
date: 2026-05-17
asset: AAPL                    # or BTC, or "macro: USD", or "cohort: software"
sleeve: equity-core
horizon: years
action: hold                   # buy/add/hold/trim/sell/avoid/harvest_loss
confidence: medium
status: pending                # → resolved after /reflect-decisions runs
trigger_reassessment: "DGS10 above 5.0% OR quarterly revenue YoY < 0"
---
```

The body is what you wrote during the methodology. The footer is a placeholder for the reflection cycle to fill in:

```markdown
## Outcome (filled in by /reflect-decisions)

(reserved — pending)
```

**Commit the decision file in the same session.** Don't let it sit uncommitted. The audit trail is the whole point.

---

## Skills + shortcuts

- `/research <question>` — loads this methodology + the last 5–10 reflections, runs the session against your question. Use this rather than freelancing.
- `/reflect-decisions` — runs the reflection cycle against decisions past their horizon. Manually trigger periodically (weekly to start), wire to `/schedule` when comfortable.
- `genkei watchlist health` — sanity-check the data lake before relying on a query result. If a source is STALE or EMPTY, your conclusions may be drawing on stale data.
- `genkei macro-regime` — one-shot risk-on/risk-off/mixed classifier over the macro series; the opening read for section 1.
- `genkei stablecoin-flow` / `genkei tvl-drawdown` / `genkei revenue-divergence` — typed surfaces for the flow & positioning checks in sections 2–3; prefer them over re-deriving via `genkei query`.

**Keep this document in sync with the CLI.** When a new `genkei` subcommand ships (or a table flips EMPTY → populated), update the sections above in the same PR — stale claims here propagate into every future session. See `docs/research/README.md` for the sync checklist.

## What this methodology is NOT

- Not a trade ticket — it's a research-decision log. Position-sizing implication is documented but execution is separate.
- Not a forecast — it's a thesis with explicit reassessment triggers. Forecasts age badly; theses with triggers age into evidence.
- Not a substitute for thinking — the checklist is scaffolding so the thinking doesn't drift, not a replacement for the thinking.
