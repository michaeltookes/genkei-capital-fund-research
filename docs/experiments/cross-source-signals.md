# Cross-Source Signal Correlation Engine

**B-064.** The Phase 6 layer that turns seven independent signal sources into a single "what's actually flashing right now?" surface. The lake answer in one sentence: **emitters write atomic events into `meta.signal_events`; the correlator scans them for multi-source agreement on the same asset within a window, scored by a configurable rule set in `src/genkei/data/signal_rules.yml`.**

```text
$ genkei signals --top 5
Cross-source signal stacks (5 found)
--------------------------------------------------
  window_end   asset    dir      rule                    score  sources  events
  2026-05-26   CRM      bullish  smart_money_buy           2.10        3  insider_clusters/buy_cluster, crowding/crowding_add, eight_k_impact/item_2_02
  ...
```

## Backstory

The data lake reached the point where adding more sources was lower leverage than connecting the existing ones. By 2026-05-26 we had seven Phase 5 experiments — insider clusters, 13F crowding, 8-K impact event study, TVL drawdown classifier, macro regime classifier, watchlist scoring rubric, crypto relative strength — each producing actionable output but each living behind its own CLI. The ValueAct CRM case logged in `docs/research/decisions/2025-12-05-valueact-crm-buy-cluster.md` was the prompt: that decision noted "insiders adding" but couldn't see "and four other watchlist managers are too." B-080 + B-061 closed the data half (13F + crowding monitor); B-064 closes the *integration* half.

## Two artifacts side-by-side

There were already two `meta.signals`-shaped questions in the project, and the cleanest answer kept them separate:

| Table | Shape | Producer | Consumer |
|---|---|---|---|
| `meta.signals`        | One row per `(asset, day, rubric_version)` with a composite *score* + per-component breakdown. | B-065 watchlist scoring rubric. | Daily-brief style "where do the watchlist names rank?" |
| `meta.signal_events`  | One row per atomic event, source-tagged, time-stamped. | Every Phase 5 emitter writes here. | The cross-source correlator (this experiment). |

Renaming the existing `meta.signals` to free up the cleaner name would have been hostile to B-065's already-deployed daily workflow. The sibling `meta.signal_events` reads cleanly: *signals = scored summaries, signal_events = atomic event stream.*

## What the engine actually does

1. **Emitters** (one per experiment) walk their source data and write events into `meta.signal_events`. Each event has an `asset`, a `horizon` sleeve tag, a `ts`, a `(source, signal_kind)` discriminator, a `direction` (`bullish` / `bearish` / `neutral`), a `strength` (typically 0–1), an arbitrary JSONB `payload`, and a non-null `source_ref` natural key that makes re-emission idempotent through the table's UNIQUE constraint on `(asset, ts, source, signal_kind, source_ref, horizon)`.

2. **Rules** (declared in YAML) describe co-occurrence patterns to detect. A rule is a name + description + horizon + direction + list of `RuleComponent`s (`source` + optional `signal_kind` + `weight`) + a `window_days` + a `min_score` + a `min_distinct_sources` threshold.

3. **Correlator** (`signal_store.detect_stacks`) walks the events against each rule:
   - Filter events to those matching the rule's direction and one of its components.
   - Group by asset + asset class and slide a window of `window_days` over each chronologically sorted event stream.
   - For each window, sum `weight × strength` over the matching components. Count distinct sources.
   - Emit a `Stack` if `score ≥ min_score` AND `distinct_sources ≥ min_distinct_sources`.
   - Greedy advance — once a stack emits, skip past its window so a long burst on one asset doesn't produce overlapping stacks.

`min_distinct_sources ≥ 2` is the load-bearing constraint that makes a stack *actually* multi-source rather than one noisy emitter firing twice.

## Starter rule pack

`src/genkei/data/signal_rules.yml` ships with four rules — two bullish, two bearish:

| Rule | Direction | Horizon | Window | Components | Min score |
|---|---|---|---|---|---|
| `smart_money_buy`        | bullish | `equity:core` | 7d  | insider buy cluster (1.0) + crowding add (1.0) + 8-K item 1.01 (0.6) + 8-K item 2.02 (0.5) | 1.5 |
| `activist_position_take` | bullish | `equity:core` | 60d | insider buy cluster (1.0) + crowding jump (1.0)                                            | 1.4 |
| `broad_exit`             | bearish | `equity:core` | 90d | sell cluster (1.0) + crowding exit (1.0)                                                   | 1.5 |
| `deterioration_stack`    | bearish | `equity:core` | 30d | sell cluster (1.0) + 8-K item 5.02 (0.6) + 8-K item 4.02 (0.8)                             | 1.4 |

Rules can use `signal_kind: null` as a wildcard to match any kind from a source — useful for "any 8-K counts" baseline weighting. When both an exact-kind component and a wildcard from the same source match an event, the exact-kind weight wins.

## Emitter status

Two of seven emitters are wired up as of 2026-05-31:

* **`insider_clusters`** (B-064) — the reference adapter that proved the pattern end-to-end.
* **`crowding`** (B-093) — the second source. Because the correlator enforces `min_distinct_sources ≥ 2`, this is the emitter that lets the engine fire its *first* real multi-source stack: insider clusters + crowding both land on `equity:core` assets, so `activist_position_take` and `broad_exit` are now fully fireable and `smart_money_buy` reaches two of its three sources. It turns each `CrowdingRow` quarter-over-quarter delta into `crowding_add` (net positive), `crowding_jump` (net ≥ `--jump-threshold`, default 3 — also emitted alongside `crowding_add` so a big add participates in both the add- and jump-keyed rules), and `crowding_exit` (net negative) events. Strength is `min(abs(net_change) / 4, 1.0)` — the 1 → 4 activist-add pattern saturates at full conviction.

Follow-up emitters (B-094 – B-098, each a separate branch):

* `eight_k_emitter` (B-094) — emits one event per 8-K filing with item-code-conditional strength (Item 1.01 → high strength, etc.). Unblocks the item-code-specific rules; completes all four starter rules once landed.
* `tvl_drawdown_emitter` (B-095) — emits a single event per asset when the drawdown classifier crosses its threshold.
* `macro_regime_emitter` (B-096) — emits regime-change events (the regime itself is a continuous state; only transitions are atomic enough to deserve an event row).
* `watchlist_scoring_emitter` (B-097) — emits when the composite score crosses a configurable threshold band.
* `relative_strength_emitter` (B-098) — emits leadership/laggard crossings on the crypto sleeve.

Until each emitter lands, the corresponding rule components don't fire. The starter rules are pre-configured for the full picture so they light up automatically as emitters arrive.

## Why "events" rather than aggregating each experiment's existing output

Two reasons:

1. **Cross-experiment join becomes a SQL primitive.** With every signal in one table keyed on `(asset, ts)`, "show me everything that fired on AAPL between Dec 1 and Dec 31" is one `SELECT`. Without it, each experiment ships its own narrow query surface and the agent has to call N CLIs and merge in-memory.
2. **Backtesting becomes possible.** A signal_event row carries its strength and timestamp at firing time. Rolling back the clock and asking "what would the smart_money_buy rule have produced on Date X, with only the events known by Date X?" is a date-bounded query, not a re-run of every experiment.

## CLI

`genkei signals` runs the correlator over the rule set and renders the most-recent strongest stacks. Two modes:

* **Default** — `genkei signals [--asset X] [--rule R] [--direction bullish] [--since D] [--until D] [--top N] [--json]` — runs `detect_stacks` and shows the qualifying stacks.
* **`--events`** — dumps raw signal_events instead of running the correlator. Useful for debugging "why doesn't my stack fire" — typically the answer is "because the second emitter hasn't been wired up yet."

`--rules-path` overrides the YAML location so tests can use a minimal rule set without touching the packaged file.

## Module shape

* `src/genkei/experiments/signal_store.py` — `SignalEvent` / `CorrelationRule` / `RuleComponent` / `Stack` dataclasses; `emit_signal` / `emit_signals_bulk` / `query_events` persistence; `detect_stacks` pure correlator.
* `src/genkei/experiments/signal_rules.py` — YAML loader + validator (separate so tests can exercise the correlator on synthetic rules without pulling `yaml`).
* `src/genkei/experiments/emitters/` — one module per Phase 5 source. Today: `insider_clusters_emitter.py` (B-064) + `crowding_emitter.py` (B-093).
* `src/genkei/cli/signals.py` — Typer wrapper.
* `src/genkei/data/signal_rules.yml` — the declarative rule set.

## Run the emitter, query the engine

```bash
# Populate meta.signal_events from existing sec.form4_transactions data
python -m genkei.experiments.emitters.insider_clusters_emitter --since 2024-01-01

# Now query
genkei signals --top 10
genkei signals --events --asset AAPL --top 50      # raw events for AAPL
```

## Edge cases pinned by tests

* **`min_distinct_sources` blocks fake stacks** — two events from the *same* source within the window don't combine into a multi-source stack.
* **Cross-asset events don't combine** — a cluster on AAPL + a crowding add on MSFT never form a stack on either name.
* **Greedy window advance** — once a stack emits at window-start `t0`, scanning resumes at `t0 + window_days` so a long burst doesn't manufacture three overlapping stacks.
* **Sort order is recent-strongest-first** — the default CLI render shows what's *currently actionable*, not historical clutter.
* **`ON CONFLICT DO UPDATE`** — re-emitting the same `(asset, ts, source, signal_kind, source_ref, horizon)` updates the existing row rather than failing or duplicating. The Postgres integration test pins this explicitly, including the empty-string `source_ref` fallback for emitters without a natural source ref.

## What B-064 deliberately does NOT cover

* **The five remaining emitters** (B-094 – B-098) — each is a separate follow-up branch. With `crowding` (B-093) now live alongside `insider_clusters`, the two crowding-only rules (`activist_position_take`, `broad_exit`) fire; `smart_money_buy` still needs `eight_k` for its third source.
* **Live homelab evidence** — the insider-cluster emitter hasn't been run against the homelab yet (one command, `python -m genkei.experiments.emitters.insider_clusters_emitter --since 2024-01-01`, when you want real data). Tests prove correctness against synthetic events.
* **Decay / weighting by event age** — every event inside the window contributes equally regardless of how recent it is. A v2 could add a half-life so a 6-day-old event contributes less than a today event in a 7-day window.
* **SPY / benchmark adjustment** — events fire on absolute thresholds, not abnormal-return-conditional thresholds. The macro-regime split partially captures this; full benchmark adjustment is a B-064.4 concern.
* **Stack outcome backtesting** — the correlator surfaces what's firing *now*. Joining each historical stack to its realized forward return (`yahoo.candles` / `coinbase.candles` / `coingecko.market_data`) and asking "do stacks predict drift?" is the natural next experiment after the emitters all land.

## References

* `src/genkei/experiments/signal_store.py` — persistence + pure correlator.
* `src/genkei/experiments/signal_rules.py` — YAML loader.
* `src/genkei/experiments/emitters/insider_clusters_emitter.py` — reference emitter.
* `src/genkei/cli/signals.py` — Typer command.
* `src/genkei/data/signal_rules.yml` — rule definitions.
* `migrations/versions/20260528_create_meta_signal_events.py` — schema.
* `docs/research/decisions/2025-12-05-valueact-crm-buy-cluster.md` — the decision that prompted this design.
* `docs/experiments/13f-crowding-monitor.md`, `docs/experiments/8k-filing-impact.md` — the upstream experiments whose outputs feed this engine.
