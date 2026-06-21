# Cross-Source Signal Correlation Engine

**B-064.** The Phase 6 layer that turns seven independent signal sources into a single "what's actually flashing right now?" surface. The lake answer in one sentence: **emitters write atomic events into `meta.signal_events`; the correlator scans them for multi-source agreement on the same asset within a window, scored by a configurable rule set in `src/genkei/data/signal_rules.yml`.**

```text
$ genkei signals --top 5
Cross-source signal stacks (5 found)
--------------------------------------------------
  window_end   asset    dir      rule                    horizon             score  sources  vs_bench  events
  2026-05-26   CRM      bullish  smart_money_buy         equity:core          2.10        3    +4.25%  insider_clusters/buy_cluster, crowding/crowding_add, eight_k_impact/item_2_02
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

Six of seven emitters are wired up (five equity/crypto by 2026-06-01, macro added 2026-06-21):

* **`insider_clusters`** (B-064) — the reference adapter that proved the pattern end-to-end.
* **`crowding`** (B-093) — the second source. Because the correlator enforces `min_distinct_sources ≥ 2`, this is the emitter that lets the engine fire its *first* real multi-source stack: insider clusters + crowding both land on `equity:core` assets, so `activist_position_take` and `broad_exit` are now fully fireable and `smart_money_buy` reaches two of its three sources. It turns each `CrowdingRow` quarter-over-quarter delta into `crowding_add` (net positive), `crowding_jump` (net ≥ `--jump-threshold`, default 3 — also emitted alongside `crowding_add` so a big add participates in both the add- and jump-keyed rules), and `crowding_exit` (net negative) events. Strength is `min(abs(net_change) / 4, 1.0)` — the 1 → 4 activist-add pattern saturates at full conviction.
* **`eight_k_impact`** (B-094) — the third source. Completes the component coverage for `smart_money_buy` (insider + crowding + 8-K item 1.01/2.02) and `deterioration_stack` (sell cluster + 8-K item 5.02/4.02): both rules now have every component live. Each 8-K fans into one event per item code in its `items` field — a "2.02,9.01" earnings filing emits two events (`item_2_02` bullish + `item_9_01` neutral) under the same `accession_number` source_ref, distinguished by `signal_kind` in the UNIQUE key. Direction + strength are item-code-conditional via `ITEM_CODE_PROFILES` in the module: 4.02 (non-reliance) at 0.9 bearish, 5.02 (officer departures) at 0.7 bearish, 1.01 (material agreement) at 0.6 bullish, 2.02 (earnings) at 0.5 bullish, plus broader coverage of the SEC item code catalog and a neutral 0.3 default for uncurated codes. `ts` uses B-057's after-hours-adjusted `event_date` (next trading day at UTC midnight) rather than `filed_at`, so a Friday-5pm-ET filing lands at Monday's open. No separate "any-8-K baseline" event is emitted; rules that want any-8-K matching declare a `signal_kind: null` wildcard component, which the correlator already matches against per-item events without double-counting.
* **`tvl_drawdown`** (B-095) — **the engine's first crypto-side emitter**. Adapts B-058's three-condition TVL-stress classifier (TVL 30d change < -10%, TVL drawdown from 90d peak > 15%, TVL z-score < -1.0) into atomic events keyed by each token's CoinGecko ID for cross-source joins: Ethereum→ethereum, Solana→solana, Sui→sui, with the ticker preserved in payload (BTC excluded per B-058 — its price drivers are not on-chain DeFi). Emits ONE event per stress *episode onset* — the first day the classifier flips from not-firing to firing — and skips continued-firing days, following the macro_regime emitter's "transition not state" precedent. A multi-week stress run produces one event, not 14+. Direction is always `bearish`; strength is the mean of the three normalized excesses (each saturating at 2× the threshold's bite), so deeper aggregate stress weighs more without requiring any single dimension to be extreme. `source_ref = "<chain>:<episode_start_iso>"`. Horizons follow the asset's watchlist sleeve: ethereum/solana emit at `crypto:core`, sui at `crypto:tactical`. The pre-staged `crypto_tvl_stress_combo` rule pairs `tvl_drawdown` with `relative_strength` (B-098) on the crypto:core horizon; until B-098 lands, the rule won't fire a stack but TVL events still land in `meta.signal_events` for inspection — same partial-fire pattern the equity-core rules had before B-093 / B-094. **Live homelab backfill** (2017-present): three historical episodes, all on ETH, all clustered in the 2018 ICO-bubble crash (2018-08-05, 2018-12-13, 2018-12-29) with strength 0.69 – 0.78 — exactly the "TVL collapsing while price is in a 90d drawdown and z-score is unusually low" pattern the classifier was designed for. SOL (data from 2021-03) and SUI (data from 2023-05) have not fired the classifier — both chains' TVL has mostly grown over their available history. The three-condition AND is selective enough that years can pass without a fire, which is correct for a "real stress episode" signal.

* **`relative_strength`** (B-098) — **the second crypto-side source**; the pair that finally closes the engine's crypto-side `min_distinct_sources ≥ 2` gate. Same inflection B-093 played for equity-core after B-064. For each watchlist crypto asset (ethereum / solana / chainlink / sui / pyth-network / render-token), walks daily and computes the trailing 30-day return vs BTC (the fixed crypto-market benchmark, crypto's analog of SPY). Detects state crossings using a three-state machine (laggard ≤ −15pp / neutral / leader ≥ +15pp). Emits ONE event per crossing *onset* — `laggard_crossing` (bearish) or `leader_crossing` (bullish) — and skips continued in-state days, matching the tvl_drawdown episode-onset precedent. Transitions back into neutral are silent (the "stress lifted" implicit signal — captured by the next opposite-direction crossing). Strength = `min(abs(rel_strength_pct) / 20, 1.0)`: ±15pp at threshold edge → 0.75, ±20pp at saturation → 1.0. The 20pp saturation was tuned from live data after a first pass at 30pp left a real ETH 2018 stress episode just below the rule's `min_score` of 1.5 — crypto rel-strength magnitudes during real episodes cluster in the 15-25pp range, so saturating at 20pp gives a meaningful strength to threshold-edge crossings rather than discounting them. `source_ref = "<coingecko_id>:BTC:30d:<crossing_iso>"`. Horizons follow the asset's sleeve (ethereum/solana/chainlink at `crypto:core`, sui/pyth-network/render-token at `crypto:tactical`). **Live homelab backfill** (2016-present, 487 crossings across the six crypto watchlist assets): the engine's **first real crypto stack** fires — 2018-08-04 ETH bearish `crypto_tvl_stress_combo` with score 1.65, combining a laggard_crossing at 2018-07-16 (ETH down ~17pp vs BTC over the trailing 30 days) with the 2018-08-04 tvl_drawdown_stress event. This is the canonical ETH ICO-crash signal — both on-chain demand contraction AND price losing relative leadership vs the broader crypto market at the same time. The other two ETH TVL stress episodes (2018-12-13 and 2018-12-29) don't pair because the nearest ETH laggard crossings landed 33-49 days later, just past the rule's 30-day window. That's correct behavior: the rule requires *coincident* stress, not eventual stress.

* **`macro_regime`** (B-096) — **the engine's first macro-horizon source**. Adapts B-059's regime classifier (`analytics.macro_regime_per_date`, via `load_regimes`) into one event per regime *transition* — the regime is a continuous daily state, so only the boundary day where the label flips is atomic enough to deserve a row (same "transition not state" precedent tvl_drawdown / relative_strength follow). Events use a market-wide sentinel `asset = "MACRO"` under a dedicated `asset_class = "macro"` (added in the 20260621 migration — a macro regime is not an equity/crypto/protocol, and equities/crypto are *downstream* of macro per CLAUDE.md, so it's a cross-sleeve overlay, not a per-ticker signal). `signal_kind` is the new regime label (`risk_off` / `easing` / …) so rules can target a specific entry; direction maps `risk_on`/`easing`→bullish, `risk_off`/`tightening_stress`→bearish, `mixed`→neutral; `strength` is null (a label has no natural 0-1 axis — the correlator defaults it to 1.0). `source_ref = "<ts_iso>:<new_regime>"`. Horizon `macro:cross-sleeve:primary`. Chained off the FRED daily workflow (the view is live over the FRED series). **Live homelab backfill** (2006-present): 571 transitions across 5119 regime-days — the threshold classifier oscillates on borderline days, so transitions are frequent. **Important:** with only this one source on the `MACRO` sentinel, the correlator's `min_distinct_sources ≥ 2` gate means macro events *don't form stacks on their own* — they land in `meta.signal_events` (queryable via `genkei signals --events`) but won't surface in the weekly digest until a companion rule pairs macro regime with per-asset signals as an overlay (a cross-horizon rule shape the correlator doesn't support yet) or a second macro-horizon source lands. This is the same partial-fire pattern every emitter had before its pair arrived.

Follow-up emitter (B-097):

* `watchlist_scoring_emitter` (B-097) — emits when the composite score crosses a configurable threshold band.

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

### Live benchmark-adjusted column (B-100)

`genkei signals` shows a `vs_bench` column by default that reports the asset's return minus its market benchmark's return over the stack's own window (`window_start → window_end`), in percentage points. Equity stacks compare vs SPY (from `yahoo.candles`); crypto stacks compare vs BTC (from `coinbase.candles`). Routing is automatic by `asset_class`; per-class overrides via `--equity-benchmark` / `--crypto-benchmark`. `--no-benchmark` suppresses the column entirely.

The column is the presentation-layer counterpart to the B-101/B-102 backtest's `mean_abnormal_pct`: same "stack-window abnormal return" framing, computed live for each fired stack rather than aggregated retrospectively. The correlator's `score` is left untouched — `vs_bench` is an *additional* column, not a replacement. Two reasons: (1) the B-102 backtest already proved the honest read is "show both; let the reader weigh raw and benchmark-adjusted against the rule's direction"; (2) different stacks route to different benchmarks, so collapsing into one score would lose the per-class comparator. Read sign against rule direction: bullish rule + positive `vs_bench` → market-relative confirmation; bearish rule + positive `vs_bench` → "trim toward index" rather than "go short" (the case the B-102 backtest highlighted at scale).

Live example against the homelab on 2026-06-01: of the top 10 stacks, only one (AMD `deterioration_stack`, `vs_bench` −20.02pp) shows meaningful market-relative weakness; AMZN at +29.81pp, HOOD at +14.33pp, AVGO at +14.31pp are all "asset still beat the market in the stack window" cases. The column makes that distinction visible at decision time rather than requiring a post-hoc backtest run.

## Module shape

* `src/genkei/experiments/signal_store.py` — `SignalEvent` / `CorrelationRule` / `RuleComponent` / `Stack` dataclasses; `emit_signal` / `emit_signals_bulk` / `query_events` persistence; `detect_stacks` pure correlator.
* `src/genkei/experiments/signal_rules.py` — YAML loader + validator (separate so tests can exercise the correlator on synthetic rules without pulling `yaml`).
* `src/genkei/experiments/emitters/` — one module per Phase 5 source. Today: `insider_clusters_emitter.py` (B-064) + `crowding_emitter.py` (B-093) + `eight_k_emitter.py` (B-094) + `tvl_drawdown_emitter.py` (B-095) + `relative_strength_emitter.py` (B-098) + `macro_regime_emitter.py` (B-096).
* `src/genkei/cli/signals.py` — Typer wrapper.
* `src/genkei/data/signal_rules.yml` — the declarative rule set.

## Run the emitter, query the engine

```bash
# Populate meta.signal_events from existing sec.form4_transactions data
python -m genkei.experiments.emitters.insider_clusters_emitter --since 2024-01-01

# Populate crowding events from existing sec.form13f_holdings data
python -m genkei.experiments.emitters.crowding_emitter --since 2024-01-01

# Populate 8-K events from existing sec.filings data
python -m genkei.experiments.emitters.eight_k_emitter --since 2024-01-01

# Populate TVL drawdown stress episodes (crypto-side, B-095)
python -m genkei.experiments.emitters.tvl_drawdown_emitter --since 2024-01-01

# Populate relative-strength crossings (crypto-side, B-098)
python -m genkei.experiments.emitters.relative_strength_emitter --since 2024-01-01

# Populate macro-regime transitions (macro-side, B-096)
python -m genkei.experiments.emitters.macro_regime_emitter --since 2024-01-01

# Now query
genkei signals --top 10
genkei signals --events --asset AAPL --top 50      # raw events for AAPL
genkei signals --events --asset ethereum --top 20  # TVL stress events for ETH
genkei signals --events --asset MACRO --top 20     # macro regime transitions
```

## Edge cases pinned by tests

* **`min_distinct_sources` blocks fake stacks** — two events from the *same* source within the window don't combine into a multi-source stack.
* **Cross-asset events don't combine** — a cluster on AAPL + a crowding add on MSFT never form a stack on either name.
* **Greedy window advance** — once a stack emits at window-start `t0`, scanning resumes at `t0 + window_days` so a long burst doesn't manufacture three overlapping stacks.
* **Sort order is recent-strongest-first** — the default CLI render shows what's *currently actionable*, not historical clutter.
* **`ON CONFLICT DO UPDATE`** — re-emitting the same `(asset, ts, source, signal_kind, source_ref, horizon)` updates the existing row rather than failing or duplicating. The Postgres integration test pins this explicitly, including the empty-string `source_ref` fallback for emitters without a natural source ref.

## What B-064 deliberately does NOT cover

* **The remaining emitter** (B-097) — a separate follow-up branch. The equity-side starter rules and the crypto-side `crypto_tvl_stress_combo` rule are all fully wired (B-064 / B-093 / B-094 / B-095 / B-098), and B-096 added the macro-regime source. What's still missing is (a) the watchlist-scoring band-crossing emitter (B-097) and (b) a rule shape that lets the single-source macro overlay actually *stack* with per-asset signals across horizons — until then macro events land but don't surface as digest stacks.
* **Live homelab evidence** — the insider-cluster emitter hasn't been run against the homelab yet (one command, `python -m genkei.experiments.emitters.insider_clusters_emitter --since 2024-01-01`, when you want real data). Tests prove correctness against synthetic events.
* **Decay / weighting by event age** — every event inside the window contributes equally regardless of how recent it is. A v2 could add a half-life so a 6-day-old event contributes less than a today event in a 7-day window.
* **Benchmark-adjusted display, not benchmark-gated events** — events fire on absolute thresholds, while B-100 adds the `vs_bench` presentation column so the operator can compare each fired stack against SPY/BTC at decision time.
* **Stack outcome backtesting** — the correlator surfaces what's firing *now*. Joining each historical stack to its realized forward return (`yahoo.candles` / `coinbase.candles` / `coingecko.market_data`) and asking "do stacks predict drift?" is the natural next experiment after the emitters all land.

## References

* `src/genkei/experiments/signal_store.py` — persistence + pure correlator.
* `src/genkei/experiments/signal_rules.py` — YAML loader.
* `src/genkei/experiments/emitters/insider_clusters_emitter.py` — reference emitter.
* `src/genkei/experiments/emitters/crowding_emitter.py` — 13F crowding emitter.
* `src/genkei/experiments/emitters/eight_k_emitter.py` — 8-K impact emitter.
* `src/genkei/experiments/emitters/tvl_drawdown_emitter.py` — TVL drawdown emitter (B-095, first crypto-side source).
* `src/genkei/experiments/emitters/relative_strength_emitter.py` — relative-strength crossings emitter (B-098, second crypto-side source).
* `src/genkei/experiments/signal_benchmark.py` — live benchmark-adjustment column (B-100) for `genkei signals`.
* `src/genkei/cli/signals.py` — Typer command.
* `src/genkei/data/signal_rules.yml` — rule definitions.
* `migrations/versions/20260528_create_meta_signal_events.py` — schema.
* `docs/research/decisions/2025-12-05-valueact-crm-buy-cluster.md` — the decision that prompted this design.
* `docs/experiments/13f-crowding-monitor.md`, `docs/experiments/8k-filing-impact.md` — the upstream experiments whose outputs feed this engine.
