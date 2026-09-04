# Reflect on Decisions

The reflection cycle that turns the decision log from a write-only audit trail into a feedback loop. Walks `docs/research/decisions/` for entries past their horizon, pulls realized data, computes raw alpha plus action-aware decision alpha vs a benchmark, and appends an outcome + 2-3 sentence reflection to each entry.

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
2. Skip if `status: resolved` or `status: deferred` (terminal — already handled), or `status: inactive` (pre-execution / awaiting activation).
3. Skip the template file `_template.md` and `README.md`.
4. Read optional `action` frontmatter. If missing, inspect the decision's recommendation before queuing it: backfill an explicit action for any clear `buy`, `add`, `trim`, `sell`, `avoid`, or `harvest_loss` call and add the file to an `action_backfilled` list for the batch summary/commit; only treat missing action as legacy `hold` when the recommendation is plainly hold/maintain. If the direction is ambiguous, skip the file and report it for manual action tagging rather than grading it. Valid direction values are `buy`, `add`, `hold`, `trim`, `sell`, `avoid`, and `harvest_loss`.
5. **Manual-exit P&L follow-up:** if `pnl_status: pending_missing_exit_inputs` is set, inspect the file's `exited_at` date and outcome note. If returned collateral value, final debt/carry, and realized net P&L are still missing, add the file to a `pnl_follow_up` list, leave `status: pending`, do not force a spot-price horizon grade, and skip remaining scan steps for this file. If those inputs have been supplied, resolve it with the actual leveraged-loop outcome: compute loop equity return from starting net equity to ending net equity over the actual `date` -> `exited_at` holding period, pull BTC over that same window, compute BTC benchmark return, raw alpha, and action-aware decision alpha, flip `status: resolved`, remove `pnl_status: pending_missing_exit_inputs` and `pnl_followup_reason` (or replace them with resolved P&L metadata), add it to a `pnl_resolved` list for the batch summary/commit, and skip remaining scan steps for this file.
6. **Early-resolution check:** if `superseded_by` is set OR `trigger_fired_at` / `trigger_fired: true` is present — and the file is still `pending` — resolve it now (see "Early resolution beats the horizon math" above), add it to the `early_resolved` list for the batch summary/commit, and skip remaining steps for this file.
7. Compute `elapsed_days = (today - frontmatter.date).days`.
8. Skip if `elapsed_days < horizon_days` per the mapping above.
9. Add the rest to the to-reflect queue.

If the queue is empty, `early_resolved` is empty, `pnl_resolved` is empty, `action_backfilled` is empty, and `pnl_follow_up` is empty, report "no decisions past their horizon" and stop. If `pnl_follow_up` has entries, report the missing P&L inputs in the batch summary so the nonterminal queue stays visible; if there are no file edits from `early_resolved`, `pnl_resolved`, or `action_backfilled`, stop without committing. If `early_resolved` or `action_backfilled` has entries, skip realized-data pulling and benchmark math for those files. If `pnl_resolved` has entries, keep the manual-exit branch's actual holding-period loop return and BTC benchmark math, but skip ordinary horizon-based outcome pairing for those files. Then continue to the summary/commit path so resolved files, P&L-resolved manual exits, and action-only frontmatter backfills are persisted.

### Inactive execution records

`status: inactive` is for an auditable decision stub whose trade has not executed yet, such as a limit order awaiting fill. Do not grade it, do not use its file `date` as the price baseline, and do not flip it to `resolved` or `deferred` during reflection. When the order actually fills, update the frontmatter in that decision file before the next reflection run: replace `date` with the actual fill date, flip `status` to `pending`, and keep enough context in the body to explain that the authored/logged date differed from the exposure start date.

If the execution fill or benchmark mark differs from the lake's same-day price snapshot, add a `reflection_start` frontmatter block before marking the file `pending`. `reflection_start.asset_price_usd` is the authoritative starting asset price, and any matching `reflection_start.benchmark_prices[].price_usd` entries are the authoritative starting benchmark prices for the named tickers.

---

## Step 2 — Pull realized data

For each queued decision, pull the price series from the decision date through today. Always include `--limit 1000` on `genkei prices` calls that use `--since` / `--until`; the CLI default is 30 rows, which is too short for the 180-day and 365-day reflection windows and can drop the decision-date endpoint.

### Explicit execution baselines

If frontmatter includes `reflection_start`, treat it as the source of truth for starting prices. Use `reflection_start.date` as the exposure start date, pull realized data from that date through today, and do not overwrite explicit start prices with `price_at_decision_date`:

- `reflection_start.asset_price_usd` replaces the asset's provider-snapshot starting price.
- `reflection_start.benchmark_prices[].price_usd` replaces the matching SPY/BTC/destination-basket component's provider-snapshot starting price.
- If an explicit benchmark mark is `provisional: true`, still use it for the math, but label the outcome as provisional and name the missing data that would refine it. Do not silently fall back to provider snapshots when an explicit execution baseline exists.

### Subject-basket overrides

Before sleeve-specific asset handling, if frontmatter includes `reflection_subject.type: subject_basket`, pull each `reflection_subject.assets[].ticker` over the same date window with `--limit 1000`, compute the weighted basket return from `weight`, and use that as the subject/asset return. This is for any cohort decision whose actual recommendation is a weighted held exposure such as 50/50 ETH+SOL. Label the outcome with `reflection_subject.label` and defer rather than guess if any basket component lacks price data.

### Equity decisions (`sleeve: equity-core` or any equity ticker)

- Asset: `genkei prices --ticker <TICKER> --since <decision_date> --until <today> --limit 1000 --json` — equity tickers route to `yahoo.candles` automatically (B-092). The `price_usd` field is the split/dividend-adjusted close, which is the right input for the return calc.
- **Cohort / sector assets without `reflection_subject`** (`asset: "equity-core: SaaS sector (CRM + NOW + …)"`, `asset: "cohort: VEEV vs CRM"`) aren't always single tickers. Reflect against the **named primary anchor** the decision's Frame calls out (e.g. CRM for the SaaS thesis); when the subject ticker is now in the watchlist (VEEV after B-123), pull that subject directly and say which comparator / benchmark was used. If the current subject still has no price series at all (for example, a non-watchlist name whose pull is empty), defer — see below.
- Benchmark: SPY, pulled the same way (benchmarks live in the watchlist and route to Yahoo per B-102).
- If a pull errors or returns empty (source outage, delisted ticker, un-ingested name), mark the decision `status: deferred` with a note naming the gap — never fabricate. The reflection cycle should still RUN — the failure mode of skipping reflection is worse than the failure mode of incomplete reflection.

### Crypto decisions (`asset: BTC|ETH|SOL|LINK|SUI|PYTH|RENDER` or `sleeve: crypto-*`)

- Asset: `genkei prices --ticker <ASSET> --since <decision_date> --until <today> --limit 1000 --json`
- Benchmark: BTC (the relative-to-BTC question is "did we do better than just holding BTC?" — the foundational crypto core asset). Pull BTC the same way.
- **Rotation / destination-basket override:** if frontmatter includes `reflection_benchmark.type: destination_basket`, pull each `reflection_benchmark.assets[].ticker` over the same date window with `--limit 1000`, compute the weighted basket return from `weight`, and use that basket return instead of BTC for `benchmark_return`. This is for trim/sell rotations where proceeds were explicitly redeployed into named assets; label the outcome with `reflection_benchmark.label` and defer rather than guess if any basket component lacks price data.

### Macro decisions (`sleeve: macro-aware`)

- These aren't about returns; they're about whether the regime call held. Pull the relevant `genkei macro --series …` series from the decision date through today. Compare actual trajectory to what the decision called for. Outcome is qualitative ("regime call held" / "regime call failed" / "still pending — series moved differently than expected but it's early").

---

## Step 3 — Compute outcome

For equity / crypto decisions where you have prices:

- **Asset / subject return:** `(price_today / starting_asset_price) - 1`, where `starting_asset_price` is `reflection_start.asset_price_usd` when present, otherwise `price_at_decision_date`; for `reflection_subject.type: subject_basket`, use the weighted component returns instead. Annualize if horizon > 1y by using `(1 + ret) ** (365/elapsed_days) - 1`.
- **Benchmark return:** same calc, against SPY, BTC, or an explicit `reflection_benchmark` destination basket depending on sleeve and frontmatter. If `reflection_start.benchmark_prices` contains a matching ticker, use that explicit price as the benchmark component's starting price instead of the provider snapshot.
- **Raw alpha:** `asset_return - benchmark_return`. This is always the asset's return minus benchmark return.
- **Action lens:** use frontmatter `action` if present, otherwise the audited legacy `hold` default from Step 1. `buy`, `add`, and `hold` are long-exposure calls; positive raw alpha is good. `trim`, `sell`, `avoid`, and `harvest_loss` are exit/avoid calls; negative raw alpha is good because the avoided asset lagged the benchmark.
- **Decision alpha:** for long-exposure calls, `asset_return - benchmark_return`; for exit/avoid calls, `benchmark_return - asset_return`. Positive decision alpha means the recommendation worked in its intended direction. For `harvest_loss`, also note that the tax value is separate from market alpha and depends on actual sale timing, basis, and wash-sale compliance.
- **Confidence calibration:** use decision alpha, not raw alpha. If the decision said `confidence: high` and decision alpha is -5pp or worse, that's a calibration miss — note it. Same for `confidence: low` and a big positive surprise.

For macro decisions:

- Compare regime indicators today vs the prediction. Did DGS10 in fact break 5.0% as the counter-thesis warned? Did the curve invert further or steepen? Outcome is qualitative.

---

## Step 4 — Reflect (2-3 sentences)

Append to the decision file's `## Outcome (filled in by /reflect-decisions)` section:

```markdown
## Outcome

- **Resolved:** YYYY-MM-DD (reflection ran at horizon)
- **Action:** buy | add | hold | trim | sell | avoid | harvest_loss
- **Asset return:** +X.X% over Y days (annualized: +Z.Z%)
- **Benchmark return (SPY|BTC|destination basket):** +X.X%
- **Raw alpha:** +X.Xpp (asset beat | lagged | in-line vs benchmark)
- **Decision alpha:** +X.Xpp (worked | missed | in-line for the action lens)
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

**Resolved (crypto, decision alpha vs BTC)** — 2026-05-17 LINK, low-confidence hold:

```markdown
## Outcome

- **Resolved:** 2026-06-12 (dry-run exercise; real horizon is years)
- **Action:** hold
- **Asset return:** −20.1% over 26 days (LINK 9.82 → 7.84)
- **Benchmark return (BTC):** −19.3% (BTC 78,493 → 63,337)
- **Raw alpha:** −0.8pp (in-line — LINK fell with the whole crypto tape, not idiosyncratically)
- **Decision alpha:** −0.8pp (in-line for a hold)
- **Trigger-condition status:** not fired (no 15pp underperformance vs ETH; ETH TVL above $35B)
- **Reflection:** Over this window LINK was pure beta to BTC — the structural oracle-share thesis hadn't begun to play out, which is consistent with the low confidence the call carried. Nothing to recalibrate yet; the real test is whether LINK diverges from BTC over the year, and a 26-day −0.8pp decision alpha is noise. Takeaway: low-confidence crypto-core calls need the full horizon — short-window alpha says nothing.
```

**Previously deferred, now data-backed after B-123** — 2026-06-11 VEEV vs CRM:

```markdown
## Outcome

- **Status:** still pending until the `years` horizon or a trigger fires.
- **Data note:** B-123 added VEEV to `equities: primary` and backfilled Yahoo candles, so the old dry-run deferral is obsolete. A future reflection should pull `genkei prices --ticker VEEV ...` directly, compare against SPY for equity-core raw/decision alpha, and mention CRM only as the thesis comparator when interpreting the outcome.
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
- Average decision alpha by sleeve, with raw alpha context for exit/avoid calls.

Write the result to `docs/research/aggregate-YYYY-MM-DD.md` and link from `docs/research/README.md`. This is the meta-feedback the methodology + log was built to enable.

---

## Skills + shortcuts

- `/reflect-decisions` — runs this prompt against current `docs/research/decisions/`. Manual today; wire to `/schedule` weekly (or after each new decision lands) once you've exercised the cycle enough to trust the output.
- `/research <question>` — the methodology prompt; loads the most recent 5-10 reflections into context so new sessions are informed by past calibration data.
- `genkei prices --ticker <X> --since YYYY-MM-DD --until YYYY-MM-DD --limit 1000 --json` — the workhorse query for outcome pulls, for both crypto (CoinGecko/Coinbase) and equities (Yahoo). JSON shape feeds directly into the return and alpha calc; `price_usd` is already adjusted for equities.

## What this prompt is NOT

- Not a forecasting framework — outcome pairing is backward-looking and the alpha calc is just bookkeeping.
- Not a substitute for re-evaluating decisions when trigger conditions fire mid-horizon. If the counter-thesis trigger fires before the horizon, the decision should be reassessed THEN, not waited out.
- Not a precision exercise — short horizons get noisy returns. The value is patterns *across* many decisions over time, not the alpha of any single decision.
