# Threshold-Based Alert Engine

**B-068.** The layer that turns the [cross-source correlator](cross-source-signals.md)'s passive "here's every stack that qualified" into a *pushed* "this one crossed the page threshold — act on it." One sentence: **the alert engine runs the B-064 correlator, evaluates the rules in `src/genkei/data/alert_rules.yml` against the detected stacks, and lands one row per (alert-rule, stack) that clears its threshold into `meta.alerts` — deduped per stack, with an optional Discord ping for the newly-fired rows.**

```text
$ genkei alerts --severity critical --top 4
Threshold alerts (4 found)
--------------------------
  triggered    sev       asset    dir      alert_rule             via_rule                 score  src status       notified
  2026-07-15   critical  SNOW     bearish  critical_equity_exit   deterioration_stack       4.42    2 open         -
  2026-07-14   critical  MU       bearish  critical_equity_exit   equity_rel_strength_exit   1.90    2 open         -
  2026-07-09   critical  AVGO     bearish  critical_equity_exit   equity_rel_strength_exit   3.81    2 open         -
  2026-07-09   critical  WDAY     bearish  critical_equity_exit   equity_rel_strength_exit   4.54    2 open         -
```

## Why a layer on top of the correlator

The correlator already finds every multi-source stack and `genkei signals` surfaces them — but the flow was still *pull*. Michael had to remember to run the query. The correlator's `min_score` / `min_distinct_sources` are the floor at which a stack *exists*; they aren't the bar at which something is worth interrupting your day. The alert engine adds that second bar: **correlation rules define what a stack is; alert rules define what's worth paging about.** A rule with `min_score: 0` alerts on any qualifying stack; raise the floor and only the strong ones page, leaving the marginal stacks to the weekly digest.

The split mirrors the emitter/CLI split elsewhere in the lake: the engine (`python -m genkei.experiments.alert_engine`) *writes* `meta.alerts`; `genkei alerts` *reads* it — exactly as the anomaly detector writes `meta.anomalies` and `genkei anomalies` reads it.

## What the engine does

1. **Correlate.** Load `signal_rules.yml`, query `meta.signal_events` over the window, run `detect_stacks` — identical to what `genkei signals` does.
2. **Evaluate.** For each stack, walk every alert rule. A stack that clears a rule's `match` filters (`rules` / `asset_class` / `horizon` / `direction`, each an any-of list; omit = match any) AND its `min_score` / `min_distinct_sources` floors produces one candidate `Alert`. A single stack can trip several rules (a broad `info` catch-all and a narrow `critical` both), each with its own fingerprint.
3. **Persist.** Insert candidates with `ON CONFLICT (fingerprint) DO NOTHING RETURNING`, so only the *newly-created* rows come back. The fingerprint is `{alert_rule}:{correlation_rule}:{asset}:{horizon}:{triggered_at date}` — a stack's `window_end` is stable, so a daily re-run over the trailing window inserts nothing for stacks already seen. A genuinely new stack on a later date gets a fresh fingerprint → a fresh alert.
4. **Notify (optional).** With `--notify`, the newly-created rows are posted to the Discord webhook (`DISCORD_WEBHOOK_URL`) as a single severity-colored embed, and their `notified_at` is stamped. No webhook configured → graceful no-op; the persisted rows are the durable record, Discord is the ping (B-119). A non-2xx / transport error is a warning, not a failure — the alert is already in `meta.alerts`.

## Starter alert pack

`src/genkei/data/alert_rules.yml` ships with three rules:

| Alert rule | Severity | Matches | Floor |
|---|---|---|---|
| `critical_equity_exit` | critical | equity, bearish | score ≥ 1.8, ≥ 2 sources |
| `equity_entry`         | warning  | equity, bullish | score ≥ 1.6, ≥ 2 sources |
| `crypto_stress`        | warning  | crypto, bearish | score ≥ 1.5, ≥ 2 sources |

Bearish equity exits page loudest — a multi-source agreement that smart money is leaving is the signal most likely to need same-day action on the buy-and-hold equity-core sleeve. Bullish entries and crypto stress are `warning` — actionable but not time-critical. Tune the `min_score` floors to trade recall for noise; the pack is deliberately conservative so a marginal two-source stack goes to the digest, not the pager.

## `meta.alerts` schema

Plain table (only threshold-clearing stacks land — a handful per week), keyed on a `BIGSERIAL alert_id` with a `fingerprint` UNIQUE for dedup. Notable columns: `alert_rule` vs `correlation_rule` (the B-068 threshold vs the B-064 rule whose stack fired), `severity` (`info`/`warning`/`critical`), `triggered_at` (the stack's `window_end`), `status` (`open`/`acknowledged`/`resolved` — the ack workflow is a follow-up, but the column exists so the lister can filter from day one), `notified_at` (stamped when the Discord ping delivered), and a `payload` JSONB carrying the stack summary. See `migrations/versions/20260716_create_meta_alerts.py`.

## How it runs

- **Daily workflow** — `.github/workflows/alerts-daily.yml`, cron `0 14 * * *` (14:00 UTC), on the self-hosted Beelink runner. Runs after the signal emitters that feed the correlator (the anomaly detector + its projection run at 13:00), over a 120-day trailing window (comfortably covers the widest rule window, `broad_exit` at 90d), with `--notify`.
- **Health** — tracked as a recurring `(alert_engine, evaluate)` ingest-run heartbeat in `genkei watchlist health` (like the anomaly detector: sparse writes, so a heartbeat rather than table liveness — no new alerts on a quiet day is healthy).
- **Idempotent** — replaying any window is safe; the fingerprint dedup makes a re-run a no-op for already-seen stacks, and `notified_at IS NULL` guards against double-notifying.

## Query path

`genkei alerts` reads `meta.alerts` with `--asset` / `--severity` / `--status` / `--since` / `--until` / `--json`. The default view is the most-recent alerts across every asset; the JSON shape is one row per alert with the full stack payload for the agent.

## Follow-ups

- **Acknowledge / resolve workflow** — the `status` column exists but nothing flips it off `open` yet. A `genkei alerts --ack <id>` (or an agent-driven resolve when a decision file is logged against the alert) would close the loop.
- **Non-stack thresholds** — today every alert is sourced from a correlator stack. A future rule shape could threshold directly on a single metric (a raw drawdown %, a macro-series level) without requiring multi-source agreement — useful for "hard floor" alerts that shouldn't wait for a stack to form.
- **Cooldown windows** — dedup is per stack-date. A genuinely persistent condition (a name that stays in a bearish stack for weeks) can re-alert as new daily stacks form; a per-(alert_rule, asset) cooldown would suppress the repeats if that proves noisy.
