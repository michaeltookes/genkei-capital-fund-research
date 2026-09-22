---
name: reflect-decisions
description: Run the reflection cycle against the Genkei decision log. Walks `docs/research/decisions/` for entries with `status: pending` past their horizon, surfaces manual-exit P&L follow-ups, pulls realized prices via `genkei prices`, computes raw alpha plus action-aware decision alpha vs the relevant benchmark (SPY for equity, BTC for crypto) for action records, grades scenario-ladder records against their stated ladder, and appends an outcome + 2-3 sentence reflection to each entry. Use when the user says "reflect on decisions", "check old decisions", "run the reflection cycle", invokes `/reflect-decisions`, or fires this via `/schedule`. Manual today; reasonable weekly cadence once exercised.
---

# Reflect on decisions

Runs the outcome-pairing cycle defined in `prompts/reflect-on-decisions.md`. Turns the append-only decision log from a write-only audit trail into a feedback loop.

## Pre-flight

1. **Read `prompts/reflect-on-decisions.md`** in full. That prompt is the source of truth for *what* the reflection does, including the elapsed-time mapping per horizon, the raw/decision alpha computation, and the "what makes a good 2-3 sentence reflection" rules.
2. **Verify the data lake is healthy**: `genkei watchlist health`. If `coingecko.market_data` is EMPTY / STALE, crypto outcome pulls will fail; if `yahoo.candles` is EMPTY / STALE, equity outcome pulls will fail. Sanity-check both before computing alpha.
3. **Confirm a clean working tree** (`git status`). The cycle commits one batch of updates; mixing with in-progress work risks a confusing commit.

## Walk the decisions

Walk `docs/research/decisions/*.md` (excluding `_template.md` and `README.md`):

1. Parse YAML frontmatter (between `---` fences). Skip files with terminal statuses: `resolved` (already reflected) and `deferred` (explicitly postponed because required data was unavailable), and skip `inactive` files whose trade/event has not executed yet. Note counts in the run summary. Scenario-ladder evidence that is merely late should not use terminal `deferred`; keep it `status: pending` with `scenario_status: pending_missing_evidence`.
2. Read optional `reflection_type`. If it is `scenario_ladder`, treat the file as a non-action scenario record: require date-only `grade_date`, do not require or backfill `action`, and do not grade it on action-aware alpha. Queue it only after a completed `grade_date` close is available (usually `today > grade_date`), unless a dated trigger/terminal event resolves it earlier. If a scenario-ladder file also has `action`, report the frontmatter conflict for manual repair.
3. For ordinary action records, read optional `action` frontmatter. If it is missing, inspect the decision's recommendation before queuing it: backfill an explicit action for any clear `buy`, `add`, `trim`, `sell`, `avoid`, or `harvest_loss` call and add the file to an `action_backfilled` list for the batch summary/commit; only treat missing action as legacy `hold` when the recommendation is plainly hold/maintain. If the direction is ambiguous, skip the file and report it for manual action tagging rather than grading it.
4. **Manual-exit P&L follow-up (before early resolution or horizon math).** If `frontmatter.pnl_status: pending_missing_exit_inputs` is set, inspect the file's `exited_at` date and existing outcome note. If returned collateral value, final debt/carry, and realized net P&L are still missing, add the file to a `pnl_follow_up` list, leave `status: pending`, do NOT queue it for spot-price outcome pairing, and skip the rest of the scan for this file. If those inputs have been supplied, resolve it with the actual leveraged-loop outcome: compute loop equity return from starting net equity to ending net equity over the actual `date` -> `exited_at` holding period, pull BTC over that same window, compute BTC benchmark return, raw alpha, and action-aware decision alpha, flip `status` to `resolved`, remove `pnl_status: pending_missing_exit_inputs` and `pnl_followup_reason` (or replace them with resolved P&L metadata), add it to a `pnl_resolved` list for the run summary/commit, and skip the rest of the scan for this file.
5. **Scenario evidence follow-up.** If `frontmatter.scenario_status: pending_missing_evidence` is set, re-check the named missing inputs. If they are still unavailable, add the file to a `scenario_follow_up` list, leave `status: pending`, do not append an outcome block, and skip the rest of the scan for this file. If the evidence has arrived, remove `scenario_status` / `scenario_followup_reason` (or replace them with resolved evidence metadata) and continue so the scenario can be graded.
6. Apply horizon mapping from the prompt: `weeks` -> 28d, `months` -> 180d, `years` -> 365d.
7. **Early-resolution check (before the horizon math).** If the decision was superseded before horizon, it resolves *now* with a forward-link, not by benchmark pairing (see `docs/research/README.md` -> "Supersession and trigger-fire"). Specifically, if `frontmatter.superseded_by` is set and the file is still `pending`, flip it to `resolved`, write a short `## Outcome` note pointing at the superseding/successor decision (no alpha; it was carried forward, not graded), and add it to an `early_resolved` list for the batch summary/commit. If `frontmatter.trigger_fired: true` or `frontmatter.trigger_fired_at` appears before the normal horizon, retain that forward-link-only behavior for ordinary action records. For `reflection_type: scenario_ladder`, require an actual terminal date: use `trigger_fired_at`, or derive the first terminal-event date from the capped OHLC/non-price evidence. Compare that terminal date with `grade_date`, not `date + horizon_days`; if it is on or before `grade_date`, queue the record immediately for scenario grading capped at the terminal date so terminal/failure outcomes are recorded in the ladder. Do not cap scenario grading at the reflection run's `today` for a bare `trigger_fired: true`; report the missing terminal date/evidence for manual repair. Note these as "early-resolved (supersession/trigger)", "scenario terminal trigger", or "scenario trigger missing terminal date" in the run summary.
8. Compute `elapsed_days = (today - frontmatter.date).days`.
9. If this is a scenario-ladder record and the completed `grade_date` close is not yet available, skip - not yet eligible for reflection unless a dated trigger/terminal event resolved it earlier. For ordinary records, if `elapsed_days < horizon_days`, skip - not yet eligible for reflection. Note in the run summary.
10. If this is a scenario-ladder record that survived the grade-date or terminal-trigger check, queue it for scenario outcome pairing regardless of nominal `horizon`. For ordinary records, if `elapsed_days >= horizon_days`, queue for outcome pairing.

If the eligible queue is empty, `early_resolved` is empty, `pnl_resolved` is empty, `action_backfilled` is empty, `pnl_follow_up` is empty, and `scenario_follow_up` is empty, report "no decisions past their horizon" and stop. If `pnl_follow_up` or `scenario_follow_up` has entries, report the missing inputs in the run summary so the nonterminal queue stays visible; if there are no file edits from `early_resolved`, `pnl_resolved`, `action_backfilled`, or newly tagged scenario follow-ups, stop without committing. If `early_resolved` or `action_backfilled` has entries, skip outcome pairing for those files. If `pnl_resolved` has entries, keep the manual-exit branch's actual holding-period loop return and BTC benchmark math, but skip ordinary horizon-based outcome pairing for those files. Continue to the summary/test/commit path for file edits from early resolution, P&L resolution, action backfills, or newly tagged scenario follow-ups.

### Inactive execution records

`status: inactive` is for an auditable decision stub whose trade or triggering event has not executed yet, such as a limit order awaiting fill. Do not grade it, do not use its authored file `date` as the price baseline, and do not flip it to `resolved` or `deferred` during reflection.

When the order/event actually activates, update the decision frontmatter before the next reflection run: replace `date` with the actual exposure-start date, flip `status` to `pending`, and keep enough body context to explain that the authored/logged date differed from the exposure baseline. This activation step is the only case where the decision file's frontmatter `date` should change.

## Outcome pairing (per queued decision)

For each decision in the queue:

1. **Resolve the `asset` to a price series first.** A clean ticker (`LINK`, `CRM`, `VEEV`) pulls directly. Before sleeve-specific handling, a decision with `reflection_subject.type: subject_basket` uses the weighted basket from `reflection_subject.assets[].ticker` as the subject return for any sleeve, labeled with `reflection_subject.label`; defer if any component lacks price data. A decision with `reflection_type: scenario_ladder` uses OHLC-capable candles capped at `grade_date`, plus every load-bearing non-price input named by the ladder or `related` sources, to grade the stated ladder directly rather than action alpha. If load-bearing scenario evidence is expected but not yet published, leave the file pending with `scenario_status: pending_missing_evidence` / `scenario_followup_reason`, add it to `scenario_followup_tagged` for the run summary/commit, and retry on later runs. For crypto scenario records, query `coinbase.candles` directly or use an OHLC-capable CLI/query surface; close-only `genkei prices` output is insufficient for any-point target prints and peak-to-trough drawdowns. A cohort/sector label without `reflection_subject` (`"equity-core: SaaS sector (CRM + NOW + …)"`, `"cohort: VEEV vs CRM"`) is NOT always a valid `--ticker` by itself — reflect it against the decision's named primary anchor, and say which subject/comparator/benchmark was used. After B-123, VEEV is a watchlist equity with Yahoo candles, so the 2026-06-11 VEEV-vs-CRM decision should pull `genkei prices --ticker VEEV` directly when it becomes eligible. If the current subject or anchor still has no price series at all, defer — see the deferred path below.
2. **Pull realized prices** per the prompt's instructions. Include `--limit 1000` on all `genkei prices` calls with `--since` / `--until`; the CLI default is 30 rows and can omit the decision-date endpoint for months/years horizons.
   - Crypto decisions: `genkei prices --ticker <ASSET> --since <date> --until <today> --limit 1000 --json`. Same for BTC benchmark.
   - Rotation decisions with `reflection_benchmark.type: destination_basket`: pull each `reflection_benchmark.assets[].ticker` with the same date window and `--limit 1000`, compute the weighted basket return using `weight`, and use that as `benchmark_return` instead of BTC. Label the output with `reflection_benchmark.label`; defer if any basket component lacks price data.
   - Equity decisions: same command — equity tickers route to `yahoo.candles` (B-092), and `price_usd` is the split/dividend-adjusted close, the right input for the return calc. Benchmark is SPY, pulled the same way.
   - Macro decisions: pull the relevant `genkei macro --series … --since <date> --until <today>` series. Compare actual trajectory vs the regime call qualitatively.
   - Any sleeve: if a pull errors or returns empty, mark `status: deferred` with a clear note naming the gap — DO NOT fabricate outcome data. The reflection still runs, just with the deferred status.
3. **Compute raw alpha and decision alpha** per the prompt for ordinary action records. Raw alpha is always `asset_return - benchmark_return`. For `buy` / `add` / `hold`, decision alpha is the same raw alpha. For `trim` / `sell` / `avoid` / `harvest_loss`, decision alpha is `benchmark_return - asset_return`, because avoided underperformance means the call worked. Annualize if horizon > 1y. For `reflection_type: scenario_ladder`, instead compute the ladder facts using only OHLC observations through `grade_date` or the earlier terminal trigger date: intraperiod high/low, grade-date or trigger-date close, peak-to-trough drawdown, named threshold hits, hold/failure conditions, required non-price evidence, realized tier (including any ETH-flip tail tier defined by the record), and confidence calibration versus the stated probability ladder. Do not compute action returns, action-aware decision alpha, or action-record confidence calibration for scenario ladders.
4. **Write the `## Outcome` block** in the decision file, replacing the `(reserved - pending)` placeholder. For action records, include resolution date, action, asset return, benchmark return, raw alpha, decision alpha, trigger-condition status, and a 2-3 sentence reflection. For scenario-ladder records, include resolution date, `Reflection type: scenario_ladder`, realized tier, observed path, non-price evidence, scenario confidence calibration, trigger-condition status, and a 2-3 sentence reflection; do not include action alpha. Leave the file `status: pending` with `scenario_status: pending_missing_evidence` instead of classifying a tier when load-bearing scenario evidence is expected but not yet published. For `harvest_loss`, note that tax value is separate from market alpha and depends on actual sale timing, basis, and wash-sale compliance.
5. **Flip the frontmatter `status`** from `pending` → `resolved` (or `deferred` if required data was genuinely unavailable for an ordinary action record). Scenario-ladder records with late load-bearing evidence remain `pending` until the evidence arrives or the source window closes.
6. **Update the frontmatter `date`** — no for normal pending decisions. The original date is the decision date; resolution is a property of the outcome block. The only exception is activating a previously `inactive` execution record before reflection, where `date` must become the actual exposure-start date so return windows use the real baseline.

## Reflection content guidelines (from the prompt)

The 2-3 sentence reflection must be specific:

- Identify which signal actually carried (or wrecked) the call. Not "macro shifted" — *"insider buys were the decisive signal; macro proved noise"*.
- For action records, note confidence calibration against decision alpha, not raw alpha: if `confidence: high` and decision alpha is -5pp or worse, that's a calibration miss; flag it. If `confidence: low` and decision alpha is meaningfully positive, that's also a miss in the other direction. For scenario-ladder records, calibrate confidence against the realized tier and stated probability ladder instead.
- Pull forward a takeaway for *future* decisions in the same sleeve / signal pattern. The point isn't to grade the past decision; it's to inform the next one.

Bad reflection: "Decision worked out, +12% decision alpha."
Good reflection: "Insider-cluster signal carried this; macro turned hostile mid-horizon but didn't dent the thesis. Calibration was right — medium confidence matched the +8% decision alpha. Takeaway: when activist add-on (vs initial position) shows up, treat as higher-conviction than the same shape from corporate insiders."

## Commit + push

After processing the queue and any early-resolved, P&L-resolved, action-backfilled, or newly scenario-follow-up-tagged decisions:

1. Run `python3 -m unittest discover -s tests` before committing — the frontmatter validator should still pass since you've only flipped status + added body content; if it fails, something went wrong with the YAML edit.
2. **One commit per run** is the convention. Subject: `Reflect on N decisions (resolved: X, deferred: Y, tagged: Z)`. Body: short summary of which decisions were touched, including any early-resolved supersession/trigger files and action-only backfills. Early-resolved-only and action-backfill-only runs still get committed.
3. Push.

## Aggregate snapshot (optional)

After every ~10 reflections, the prompt recommends writing a `docs/research/aggregate-YYYY-MM-DD.md` snapshot summarizing hit rate by sleeve / confidence / primary signal + average decision alpha. Not required per-run; do it when there's a meaningful sample size. Link it from `docs/research/README.md`.

## Constraints

- **Never modify the original decision body** (Frame, Fundamentals, Phase A/B, Conclusion). Only update frontmatter `status`, one-time legacy `action` backfills, inactive-record activation frontmatter (`date` and `status`), and the `## Outcome` block. The audit trail depends on the original being preserved.
- **Never fabricate realized data.** If a CLI query returns empty / errors, defer the decision rather than guess. For manual exits with `pnl_status: pending_missing_exit_inputs`, keep the file pending and report the missing returned collateral, final debt/carry, and realized net P&L inputs instead of making it terminal. The cycle's value is honest record-keeping.
- **One run per cadence period.** Running the cycle daily on the same decision set produces duplicate outcome blocks; the prompt's logic already skips `resolved` so it's idempotent, but the convention is weekly-or-after-new-decisions.

## Skill boundary

This skill resolves past decisions. It does NOT:

- Make new decisions (that's `/research`).
- Modify past decisions other than the `## Outcome` block, frontmatter `status`, one-time legacy `action` backfills, and repairs to reflection metadata needed to prevent mis-grading.
- Execute trades.
