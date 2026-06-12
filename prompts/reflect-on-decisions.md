# Reflect on Decisions

The reflection cycle that turns the decision log from a write-only audit trail into a feedback loop. Walks `docs/research/decisions/` for entries past their horizon, pulls realized data, computes alpha vs a benchmark, and appends an outcome + 2-3 sentence reflection to each entry.

Loaded automatically by the `/reflect-decisions` skill. Run manually to start; wire to `/schedule` (weekly cadence is a reasonable starting point) once the cycle has been exercised a few times.

**The reason this exists:** without an outcome-pairing step, the decision log is just notes. With it, every decision becomes data the next decision draws on. Pattern recognition over your own track record is the only way to find out whether you're systematically over-confident on one sleeve, under-weighting macro, etc.

---

## What counts as "past horizon"

`horizon` is one of `weeks` / `months` / `years` in the decision file's frontmatter. Translate to a concrete number for the elapsed-time check:

- `weeks` → 4 weeks (28 days)
- `months` → 6 months (~180 days)
- `years` → 12 months (~365 days)

A decision is **eligible for reflection** when `today - date >= horizon_days`. Skip decisions where the trigger-condition has already fired (those should have been re-evaluated at trigger time, not at horizon time).

---

## Step 1 — Scan

Walk `docs/research/decisions/*.md`. For each file:

1. Parse the YAML frontmatter (between the `---` fences).
2. Skip if `status: resolved` already (already reflected).
3. Skip the template file `_template.md` and `README.md`.
4. Compute `elapsed_days = (today - frontmatter.date).days`.
5. Skip if `elapsed_days < horizon_days` per the mapping above.
6. Add the rest to the to-reflect queue.

If the queue is empty, report "no decisions past their horizon" and stop.

---

## Step 2 — Pull realized data

For each queued decision, pull the price series from the decision date through today.

### Equity decisions (`sleeve: equity-core` or any equity ticker)

- Asset: `genkei prices --ticker <TICKER> --since <decision_date> --until <today> --json` — equity tickers route to `yahoo.candles` automatically (B-092). The `price_usd` field is the split/dividend-adjusted close, which is the right input for the return calc.
- Benchmark: SPY, pulled the same way (benchmarks live in the watchlist and route to Yahoo per B-102).
- If a pull errors or returns empty (source outage, delisted ticker), mark the decision `status: deferred` with a note naming the gap — never fabricate. The reflection cycle should still RUN — the failure mode of skipping reflection is worse than the failure mode of incomplete reflection.

### Crypto decisions (`asset: BTC|ETH|SOL|LINK|SUI|PYTH|RENDER` or `sleeve: crypto-*`)

- Asset: `genkei prices --ticker <ASSET> --since <decision_date> --until <today> --json`
- Benchmark: BTC (the relative-to-BTC question is "did we do better than just holding BTC?" — the foundational crypto core asset). Pull BTC the same way.

### Macro decisions (`sleeve: macro-aware`)

- These aren't about returns; they're about whether the regime call held. Pull the relevant `genkei macro --series …` series from the decision date through today. Compare actual trajectory to what the decision called for. Outcome is qualitative ("regime call held" / "regime call failed" / "still pending — series moved differently than expected but it's early").

---

## Step 3 — Compute outcome

For equity / crypto decisions where you have prices:

- **Asset return:** `(price_today / price_at_decision_date) - 1`. Annualize if horizon > 1y by using `(1 + ret) ** (365/elapsed_days) - 1`.
- **Benchmark return:** same calc, against SPY or BTC depending on sleeve.
- **Alpha:** `asset_return - benchmark_return`. Positive = beat the benchmark; negative = underperformed.
- **Confidence calibration:** if the decision said `confidence: high` and alpha is +5% or worse, that's a calibration miss — note it. Same for `confidence: low` and a big positive surprise.

For macro decisions:

- Compare regime indicators today vs the prediction. Did DGS10 in fact break 5.0% as the counter-thesis warned? Did the curve invert further or steepen? Outcome is qualitative.

---

## Step 4 — Reflect (2-3 sentences)

Append to the decision file's `## Outcome (filled in by /reflect-decisions)` section:

```markdown
## Outcome

- **Resolved:** YYYY-MM-DD (reflection ran at horizon)
- **Asset return:** +X.X% over Y days (annualized: +Z.Z%)
- **Benchmark return (SPY|BTC):** +X.X%
- **Alpha:** +X.X% (beat | lagged | in-line)
- **Trigger-condition status:** fired on YYYY-MM-DD | not fired
- **Reflection:** [2-3 sentences. What was right about the original thesis? What was wrong? Was confidence well-calibrated? What's the takeaway for *future* decisions in the same sleeve / signal pattern?]
```

Then flip `status: pending` → `status: resolved` in the frontmatter, save, and commit. One commit per reflection batch is fine.

**Reflection rules of thumb:**

- **Don't rewrite the original conclusion.** The whole point is to compare what you said then against what happened. Editing the original undermines the audit trail.
- **Be specific about which signal led you astray** (or which one carried the call). "Insider buys were the decisive signal" or "I overweighted macro vs the bottoms-up case." Vague reflections won't help future decisions.
- **Track confidence calibration over time.** If you're consistently `confidence: high` and getting only 50% calls right, you're over-confident; flag it. The reflections are your record-keeping, not just per-decision notes.

---

## Step 5 — Aggregate snapshot (optional, every ~10 reflections)

When you've accumulated ~10 reflections, run a one-time aggregate pass:

- Hit rate by sleeve (equity-core / crypto-core / crypto-tactical).
- Hit rate by confidence bucket.
- Hit rate by primary signal (macro-led, fundamentals-led, insider-led, technical-led).
- Average alpha by sleeve.

Write the result to `docs/research/aggregate-YYYY-MM-DD.md` and link from `docs/research/README.md`. This is the meta-feedback the methodology + log was built to enable.

---

## Skills + shortcuts

- `/reflect-decisions` — runs this prompt against current `docs/research/decisions/`. Manual today; wire to `/schedule` weekly (or after each new decision lands) once you've exercised the cycle enough to trust the output.
- `/research <question>` — the methodology prompt; loads the most recent 5-10 reflections into context so new sessions are informed by past calibration data.
- `genkei prices --ticker <X> --since YYYY-MM-DD --until YYYY-MM-DD --json` — the workhorse query for outcome pulls, for both crypto (CoinGecko/Coinbase) and equities (Yahoo). JSON shape feeds directly into the alpha calc; `price_usd` is already adjusted for equities.

## What this prompt is NOT

- Not a forecasting framework — outcome pairing is backward-looking and the alpha calc is just bookkeeping.
- Not a substitute for re-evaluating decisions when trigger conditions fire mid-horizon. If the counter-thesis trigger fires before the horizon, the decision should be reassessed THEN, not waited out.
- Not a precision exercise — short horizons get noisy returns. The value is patterns *across* many decisions over time, not the alpha of any single decision.
