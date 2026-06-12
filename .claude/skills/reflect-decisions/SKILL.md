---
name: reflect-decisions
description: Run the reflection cycle against the Genkei decision log. Walks `docs/research/decisions/` for entries with `status: pending` past their horizon, pulls realized prices via `genkei prices`, computes alpha vs the relevant benchmark (SPY for equity, BTC for crypto), and appends an outcome + 2-3 sentence reflection to each entry. Use when the user says "reflect on decisions", "check old decisions", "run the reflection cycle", invokes `/reflect-decisions`, or fires this via `/schedule`. Manual today; reasonable weekly cadence once exercised.
---

# Reflect on decisions

Runs the outcome-pairing cycle defined in `prompts/reflect-on-decisions.md`. Turns the append-only decision log from a write-only audit trail into a feedback loop.

## Pre-flight

1. **Read `prompts/reflect-on-decisions.md`** in full. That prompt is the source of truth for *what* the reflection does, including the elapsed-time mapping per horizon, the alpha computation, and the "what makes a good 2-3 sentence reflection" rules.
2. **Verify the data lake is healthy**: `genkei watchlist health`. If `coingecko.market_data` is EMPTY / STALE, crypto outcome pulls will fail; if `yahoo.candles` is EMPTY / STALE, equity outcome pulls will fail. Sanity-check both before computing alpha.
3. **Confirm a clean working tree** (`git status`). The cycle commits one batch of updates; mixing with in-progress work risks a confusing commit.

## Walk the decisions

Walk `docs/research/decisions/*.md` (excluding `_template.md` and `README.md`):

1. Parse YAML frontmatter (between `---` fences). Skip files with terminal statuses: `resolved` (already reflected) and `deferred` (explicitly postponed because required data was unavailable). Note both counts in the run summary.
2. Compute `elapsed_days = (today - frontmatter.date).days`.
3. Apply horizon mapping from the prompt: `weeks` → 28d, `months` → 180d, `years` → 365d.
4. Check trigger metadata if present. If `frontmatter.trigger_fired: true`, or a trigger timestamp such as `frontmatter.trigger_fired_at` / `frontmatter.triggers.triggered_timestamp` is on or before `frontmatter.date + horizon_days`, exclude the decision from outcome pairing; it should be re-evaluated via the trigger path, not the horizon path. Note trigger exclusions in the run summary.
5. If `elapsed_days < horizon_days`, skip — not yet eligible for reflection. Note in the run summary.
6. If `elapsed_days >= horizon_days`, queue for outcome pairing.

If the eligible queue is empty, report "no decisions past their horizon" and stop. Don't make commits in this case.

## Outcome pairing (per queued decision)

For each decision in the queue:

1. **Pull realized prices** per the prompt's instructions:
   - Crypto decisions: `genkei prices --ticker <ASSET> --since <date> --until <today> --json`. Same for BTC benchmark.
   - Equity decisions: same command — equity tickers route to `yahoo.candles` (B-092), and `price_usd` is the split/dividend-adjusted close, the right input for the return calc. Benchmark is SPY, pulled the same way.
   - Macro decisions: pull the relevant `genkei macro --series … --since <date> --until <today>` series. Compare actual trajectory vs the regime call qualitatively.
   - Any sleeve: if a pull errors or returns empty, mark `status: deferred` with a clear note naming the gap — DO NOT fabricate outcome data. The reflection still runs, just with the deferred status.
2. **Compute alpha** per the prompt (asset return − benchmark return; annualize if horizon > 1y).
3. **Write the `## Outcome` block** in the decision file, replacing the `(reserved — pending)` placeholder. Include resolution date, asset return, benchmark return, alpha, trigger-condition status, and a 2-3 sentence reflection.
4. **Flip the frontmatter `status`** from `pending` → `resolved` (or `deferred` if required data was genuinely unavailable).
5. **Update the frontmatter `date`** — no. The original date is the decision date; resolution is a property of the outcome block. Don't overwrite the original date.

## Reflection content guidelines (from the prompt)

The 2-3 sentence reflection must be specific:

- Identify which signal actually carried (or wrecked) the call. Not "macro shifted" — *"insider buys were the decisive signal; macro proved noise"*.
- Note confidence calibration: if `confidence: high` and alpha is +5% or worse, that's a calibration miss; flag it. If `confidence: low` and alpha is meaningfully positive, that's also a miss in the other direction.
- Pull forward a takeaway for *future* decisions in the same sleeve / signal pattern. The point isn't to grade the past decision; it's to inform the next one.

Bad reflection: "Decision worked out, +12% alpha."
Good reflection: "Insider-cluster signal carried this; macro turned hostile mid-horizon but didn't dent the thesis. Calibration was right — medium confidence matched the +8% alpha. Takeaway: when activist add-on (vs initial position) shows up, treat as higher-conviction than the same shape from corporate insiders."

## Commit + push

After processing the queue:

1. Run `python3 -m unittest discover -s tests` before committing — the frontmatter validator should still pass since you've only flipped status + added body content; if it fails, something went wrong with the YAML edit.
2. **One commit per run** is the convention. Subject: `Reflect on N decisions (resolved: X, deferred: Y)`. Body: short summary of which decisions were touched.
3. Push.

## Aggregate snapshot (optional)

After every ~10 reflections, the prompt recommends writing a `docs/research/aggregate-YYYY-MM-DD.md` snapshot summarizing hit rate by sleeve / confidence / primary signal + average alpha. Not required per-run; do it when there's a meaningful sample size. Link it from `docs/research/README.md`.

## Constraints

- **Never modify the original decision body** (Frame, Fundamentals, Phase A/B, Conclusion). Only update frontmatter `status` and the `## Outcome` block. The audit trail depends on the original being preserved.
- **Never fabricate realized data.** If a CLI query returns empty / errors, defer the decision rather than guess. The cycle's value is honest record-keeping.
- **One run per cadence period.** Running the cycle daily on the same decision set produces duplicate outcome blocks; the prompt's logic already skips `resolved` so it's idempotent, but the convention is weekly-or-after-new-decisions.

## Skill boundary

This skill resolves past decisions. It does NOT:

- Make new decisions (that's `/research`).
- Modify past decisions other than the `## Outcome` block + frontmatter `status`.
- Execute trades.
