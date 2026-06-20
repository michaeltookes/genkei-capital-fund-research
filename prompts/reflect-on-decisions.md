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

A decision is **eligible for reflection** when `today - date >= horizon_days`.

**Early resolution beats the horizon math.** A decision that was superseded (frontmatter `superseded_by` set) or whose trigger fired before horizon (`trigger_fired_at` / `trigger_fired: true`) resolves *now*, with a forward-link to the successor decision rather than a benchmark-paired outcome — see `docs/research/README.md` → "Supersession and trigger-fire". Don't grade it on a benchmark it was never held to, and don't leave it `pending` (a superseded decision left pending re-queues every run — the exact bug the B-118 dry run caught on the 2026-05-20 SUI decision). Resolve it with a short note and count it as batch work to summarize and commit, even if no horizon-eligible decisions remain.

---

## Step 1 — Scan

Walk `docs/research/decisions/*.md`. For each file:

1. Parse the YAML frontmatter (between the `---` fences).
2. Skip if `status: resolved` or `status: deferred` (terminal — already handled).
3. Skip the template file `_template.md` and `README.md`.
4. **Early-resolution check:** if `superseded_by` is set OR `trigger_fired_at` / `trigger_fired: true` is present — and the file is still `pending` — resolve it now (see "Early resolution beats the horizon math" above), add it to the `early_resolved` list for the batch summary/commit, and skip remaining steps for this file.
5. Compute `elapsed_days = (today - frontmatter.date).days`.
6. Skip if `elapsed_days < horizon_days` per the mapping above.
7. Add the rest to the to-reflect queue.

If the queue is empty and `early_resolved` is empty, report "no decisions past their horizon" and stop. If `early_resolved` has entries, skip realized-data pulling and benchmark math, then continue to the summary/commit path so those resolved files are persisted.

---

## Step 2 — Pull realized data

For each queued decision, pull the price series from the decision date through today.

### Equity decisions (`sleeve: equity-core` or any equity ticker)

- Asset: `genkei prices --ticker <TICKER> --since <decision_date> --until <today> --json` — equity tickers route to `yahoo.candles` automatically (B-092). The `price_usd` field is the split/dividend-adjusted close, which is the right input for the return calc.
- **Cohort / sector assets** (`asset: "equity-core: SaaS sector (CRM + NOW + …)"`, `asset: "cohort: VEEV vs CRM"`) aren't always single tickers. Reflect against the **named primary anchor** the decision's Frame calls out (e.g. CRM for the SaaS thesis); when the subject ticker is now in the watchlist (VEEV after B-123), pull that subject directly and say which comparator / benchmark was used. If the current subject still has no price series at all (for example, a non-watchlist name whose pull is empty), defer — see below.
- Benchmark: SPY, pulled the same way (benchmarks live in the watchlist and route to Yahoo per B-102).
- If a pull errors or returns empty (source outage, delisted ticker, un-ingested name), mark the decision `status: deferred` with a note naming the gap — never fabricate. The reflection cycle should still RUN — the failure mode of skipping reflection is worse than the failure mode of incomplete reflection.

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

### Worked examples

Real blocks produced during the B-118 dry run (2026-06-12), pulling live prices via `genkei prices`. Horizons were treated as elapsed to exercise the machinery — none of these decisions is naturally past horizon yet, so these illustrate the *shape*, not final resolutions.

**Resolved (crypto, alpha vs BTC)** — 2026-05-17 LINK, low-confidence hold:

```markdown
## Outcome

- **Resolved:** 2026-06-12 (dry-run exercise; real horizon is years)
- **Asset return:** −20.1% over 26 days (LINK 9.82 → 7.84)
- **Benchmark return (BTC):** −19.3% (BTC 78,493 → 63,337)
- **Alpha:** −0.8pp (in-line — LINK fell with the whole crypto tape, not idiosyncratically)
- **Trigger-condition status:** not fired (no 15pp underperformance vs ETH; ETH TVL above $35B)
- **Reflection:** Over this window LINK was pure beta to BTC — the structural oracle-share thesis hadn't begun to play out, which is consistent with the low confidence the call carried. Nothing to recalibrate yet; the real test is whether LINK diverges from BTC over the year, and a 26-day −0.8pp alpha is noise. Takeaway: low-confidence crypto-core calls need the full horizon — short-window alpha says nothing.
```

**Previously deferred, now data-backed after B-123** — 2026-06-11 VEEV vs CRM:

```markdown
## Outcome

- **Status:** still pending until the `years` horizon or a trigger fires.
- **Data note:** B-123 added VEEV to `equities: primary` and backfilled Yahoo candles, so the old dry-run deferral is obsolete. A future reflection should pull `genkei prices --ticker VEEV ...` directly, compare against SPY for equity-core alpha, and mention CRM only as the thesis comparator when interpreting the outcome.
- **Reflection:** Do not terminally defer this decision for missing VEEV prices unless the current pull actually returns empty again. The right behavior after B-123 is a normal data-backed reflection when the horizon/trigger condition makes the decision eligible.
```

**Early-resolved (supersession + trigger-fire)** — 2026-05-20 SUI, superseded by the 2026-06-02 rotation:

```markdown
## Outcome

- **Resolved:** 2026-06-12 (early — superseded, not horizon-paired)
- **Superseded by:** 2026-06-02-sui-rotation-into-eth-sol
- **Trigger fired:** 2026-06-02 (SUI −20.7% post-decision; bear thesis accelerated, not stabilized)
- **Reflection:** The bearish call was right and the trigger caught it fast — the successor decision carries the action (deepen the trim). No benchmark alpha is computed here because the position was rotated via the successor, not held to this decision's horizon. Takeaway: when a trigger fires this cleanly, file the successor promptly and resolve the parent — don't let it linger pending.
```

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
