---
name: reflect-decisions
description: Run the reflection cycle against the Genkei decision log. Walks `docs/research/decisions/` for entries with `status: pending` past their horizon, surfaces manual-exit P&L follow-ups, pulls realized prices via `genkei prices`, computes raw alpha plus action-aware decision alpha vs the relevant benchmark (SPY for equity, BTC for crypto), and appends an outcome + 2-3 sentence reflection to each entry. Use when the user says "reflect on decisions", "check old decisions", "run the reflection cycle", invokes `/reflect-decisions`, or fires this via `/schedule`. Manual today; reasonable weekly cadence once exercised.
---

# Reflect on decisions

Runs the outcome-pairing cycle defined in `prompts/reflect-on-decisions.md`. Turns the append-only decision log from a write-only audit trail into a feedback loop.

## Pre-flight

1. **Read `prompts/reflect-on-decisions.md`** in full. That prompt is the source of truth for *what* the reflection does, including the elapsed-time mapping per horizon, the raw/decision alpha computation, and the "what makes a good 2-3 sentence reflection" rules.
2. **Verify the data lake is healthy**: `genkei watchlist health`. If `coingecko.market_data` is EMPTY / STALE, crypto outcome pulls will fail; if `yahoo.candles` is EMPTY / STALE, equity outcome pulls will fail. Sanity-check both before computing alpha.
3. **Confirm a clean working tree** (`git status`). The cycle commits one batch of updates; mixing with in-progress work risks a confusing commit.

## Walk the decisions

Walk `docs/research/decisions/*.md` (excluding `_template.md` and `README.md`):

1. Parse YAML frontmatter (between `---` fences). Skip files with terminal statuses: `resolved` (already reflected) and `deferred` (explicitly postponed because required data was unavailable), and skip `inactive` files whose trade/event has not executed yet. Note counts in the run summary.
2. Read optional `action` frontmatter. If it is missing, inspect the decision's recommendation before queuing it: backfill an explicit action for any clear `buy`, `add`, `trim`, `sell`, `avoid`, or `harvest_loss` call and add the file to an `action_backfilled` list for the batch summary/commit; only treat missing action as legacy `hold` when the recommendation is plainly hold/maintain. If the direction is ambiguous, skip the file and report it for manual action tagging rather than grading it.
3. **Manual-exit P&L follow-up (before early resolution or horizon math).** If `frontmatter.pnl_status: pending_missing_exit_inputs` is set, inspect the file's `exited_at` date and existing outcome note. If returned collateral value, final debt/carry, and realized net P&L are still missing, add the file to a `pnl_follow_up` list, leave `status: pending`, do NOT queue it for spot-price outcome pairing, and skip the rest of the scan for this file. If those inputs have been supplied, resolve it with the actual leveraged-loop P&L, flip `status` to `resolved`, add it to a `pnl_resolved` list for the run summary/commit, and skip the rest of the scan for this file.
4. Apply horizon mapping from the prompt: `weeks` -> 28d, `months` -> 180d, `years` -> 365d.
5. **Early-resolution check (before the horizon math).** If the decision was superseded or its trigger fired before horizon, it resolves *now* with a forward-link, not by benchmark pairing (see `docs/research/README.md` -> "Supersession and trigger-fire"). Specifically, if `frontmatter.superseded_by` is set, OR `frontmatter.trigger_fired: true`, OR a date such as `frontmatter.trigger_fired_at` is on or before `frontmatter.date + horizon_days` - and the file is still `pending` - flip it to `resolved`, write a short `## Outcome` note pointing at the superseding/successor decision (no alpha; it was carried forward, not graded), and add it to an `early_resolved` list for the batch summary/commit. Do NOT queue it for outcome pairing. Note these as "early-resolved (supersession/trigger)" in the run summary. This catches the failure mode where a superseded decision sits `pending` and re-queues every run.
6. Compute `elapsed_days = (today - frontmatter.date).days`.
7. If `elapsed_days < horizon_days`, skip - not yet eligible for reflection. Note in the run summary.
8. If `elapsed_days >= horizon_days`, queue for outcome pairing.

If the eligible queue is empty, `early_resolved` is empty, `pnl_resolved` is empty, `action_backfilled` is empty, and `pnl_follow_up` is empty, report "no decisions past their horizon" and stop. If `pnl_follow_up` has entries, report the missing P&L inputs in the run summary so the nonterminal queue stays visible; if there are no file edits from `early_resolved`, `pnl_resolved`, or `action_backfilled`, stop without committing. If `early_resolved`, `pnl_resolved`, or `action_backfilled` has entries, skip outcome pairing for those files but continue to the summary/test/commit path; those file edits are work done for this reflection batch.

### Inactive execution records

`status: inactive` is for an auditable decision stub whose trade or triggering event has not executed yet, such as a limit order awaiting fill. Do not grade it, do not use its authored file `date` as the price baseline, and do not flip it to `resolved` or `deferred` during reflection.

When the order/event actually activates, update the decision frontmatter before the next reflection run: replace `date` with the actual exposure-start date, flip `status` to `pending`, and keep enough body context to explain that the authored/logged date differed from the exposure baseline. This activation step is the only case where the decision file's frontmatter `date` should change.

## Outcome pairing (per queued decision)

For each decision in the queue:

1. **Resolve the `asset` to a price series first.** A clean ticker (`LINK`, `CRM`, `VEEV`) pulls directly. A cohort/sector label (`"equity-core: SaaS sector (CRM + NOW + …)"`, `"cohort: VEEV vs CRM"`) is NOT always a valid `--ticker` by itself — reflect it against the decision's named primary anchor, and say which subject/comparator/benchmark was used. After B-123, VEEV is a watchlist equity with Yahoo candles, so the 2026-06-11 VEEV-vs-CRM decision should pull `genkei prices --ticker VEEV` directly when it becomes eligible. If the current subject or anchor still has no price series at all, defer — see the deferred path below.
2. **Pull realized prices** per the prompt's instructions. Include `--limit 1000` on all `genkei prices` calls with `--since` / `--until`; the CLI default is 30 rows and can omit the decision-date endpoint for months/years horizons.
   - Crypto decisions: `genkei prices --ticker <ASSET> --since <date> --until <today> --limit 1000 --json`. Same for BTC benchmark.
   - Rotation decisions with `reflection_benchmark.type: destination_basket`: pull each `reflection_benchmark.assets[].ticker` with the same date window and `--limit 1000`, compute the weighted basket return using `weight`, and use that as `benchmark_return` instead of BTC. Label the output with `reflection_benchmark.label`; defer if any basket component lacks price data.
   - Equity decisions: same command — equity tickers route to `yahoo.candles` (B-092), and `price_usd` is the split/dividend-adjusted close, the right input for the return calc. Benchmark is SPY, pulled the same way.
   - Macro decisions: pull the relevant `genkei macro --series … --since <date> --until <today>` series. Compare actual trajectory vs the regime call qualitatively.
   - Any sleeve: if a pull errors or returns empty, mark `status: deferred` with a clear note naming the gap — DO NOT fabricate outcome data. The reflection still runs, just with the deferred status.
3. **Compute raw alpha and decision alpha** per the prompt. Raw alpha is always `asset_return - benchmark_return`. For `buy` / `add` / `hold`, decision alpha is the same raw alpha. For `trim` / `sell` / `avoid` / `harvest_loss`, decision alpha is `benchmark_return - asset_return`, because avoided underperformance means the call worked. Annualize if horizon > 1y.
4. **Write the `## Outcome` block** in the decision file, replacing the `(reserved - pending)` placeholder. Include resolution date, action, asset return, benchmark return, raw alpha, decision alpha, trigger-condition status, and a 2-3 sentence reflection. For `harvest_loss`, note that tax value is separate from market alpha and depends on actual sale timing, basis, and wash-sale compliance.
5. **Flip the frontmatter `status`** from `pending` → `resolved` (or `deferred` if required data was genuinely unavailable).
6. **Update the frontmatter `date`** — no for normal pending decisions. The original date is the decision date; resolution is a property of the outcome block. The only exception is activating a previously `inactive` execution record before reflection, where `date` must become the actual exposure-start date so return windows use the real baseline.

## Reflection content guidelines (from the prompt)

The 2-3 sentence reflection must be specific:

- Identify which signal actually carried (or wrecked) the call. Not "macro shifted" — *"insider buys were the decisive signal; macro proved noise"*.
- Note confidence calibration against decision alpha, not raw alpha: if `confidence: high` and decision alpha is -5pp or worse, that's a calibration miss; flag it. If `confidence: low` and decision alpha is meaningfully positive, that's also a miss in the other direction.
- Pull forward a takeaway for *future* decisions in the same sleeve / signal pattern. The point isn't to grade the past decision; it's to inform the next one.

Bad reflection: "Decision worked out, +12% decision alpha."
Good reflection: "Insider-cluster signal carried this; macro turned hostile mid-horizon but didn't dent the thesis. Calibration was right — medium confidence matched the +8% decision alpha. Takeaway: when activist add-on (vs initial position) shows up, treat as higher-conviction than the same shape from corporate insiders."

## Commit + push

After processing the queue and any early-resolved, P&L-resolved, or action-backfilled decisions:

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
- Modify past decisions other than the `## Outcome` block, frontmatter `status`, and one-time legacy `action` backfills.
- Execute trades.
