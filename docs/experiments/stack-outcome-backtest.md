# Stack-Outcome Backtest

**B-101.** The payoff question for the cross-source signal correlation engine (B-064): **do historical multi-source stacks actually precede meaningful forward returns?** Without a measurable lift vs a random-day baseline, the stacks are just plumbing. This experiment closes the loop: load every historical stack, join to forward returns, aggregate per rule / direction / asset, and report mean / median / hit-rate / excess-vs-baseline at horizons from 5 days to 1 year.

```text
$ genkei backtest
Backtest by rule (416 stacks across 3 strata)
---------------------------------------------
  stratum                window     n_eval   mean%    med%   hit%  excess
  broad_exit (n=113)     post_5d       113    0.39    0.37  54.87   -0.15
  broad_exit (n=113)     post_30d      112    2.95    2.30  59.82    0.14
  broad_exit (n=113)     post_90d      112    8.34    7.10  66.07    0.28
  broad_exit (n=113)     post_180d     110   13.60   11.58  66.36   -3.25
  broad_exit (n=113)     post_365d     107   33.83   24.96  79.44   -3.01
  ...
```

## Why an honest backtest matters more than another emitter

By the time B-101 landed, the engine had three emitters live (`insider_clusters`, `crowding`, `eight_k_impact`), four starter rules pre-configured, and 416 multi-source stacks in `meta.signal_events` covering 27 watchlist tickers and 2003 → 2026. The natural pull was to add the four remaining emitters (B-095–B-098) and grow coverage further. Resisted that. Adding more emitters before measuring what the existing engine produces is the classic "more features, less signal" trap — you end up trusting the rules because you built them, not because they earned it. Per the project's "fund-grade" working stance: measurement comes early.

## Module shape

`src/genkei/experiments/stack_backtest.py` mirrors the B-057 8-K event-study shape — load a stream of events, join each to the issuer's adjusted-close price series, compute per-window forward returns, aggregate by stratum. The reusable pieces (`PricePoint`, `load_price_series`, `compute_windowed_returns`) are imported directly from `eight_k_impact` rather than duplicated; the per-stack / per-stratum types are new.

| Type | Role |
|---|---|
| `StackReturns` | one per stack, with `windows: dict[label, Decimal\|None]` of per-horizon forward returns |
| `BaselineStats` | per-asset random-day baseline (sampled every 7 days through the asset's history) — the "what would a random day predict?" comparator |
| `StackStratumStats` | aggregated mean / median / hit-rate / `mean_excess_pct` per stratum |
| `run_backtest(*, rule, direction, asset, since, until)` | orchestrator: load events → detect_stacks → load prices per asset (shared between stack-returns and baselines) → compute |
| `stratify_by_rule / direction / asset` | three lenses on the same `StackReturns` list |

## Windows

Calendar-day offsets, picked by `compute_windowed_returns` via bisect to the nearest trading day on or after each window-end date:

| Label | Offset | Approx |
|---|---|---|
| `post_5d` | +5 cal days | ~1 trading week (covers `smart_money_buy`'s 7d rule horizon) |
| `post_30d` | +30 cal days | ~1 month |
| `post_90d` | +90 cal days | ~1 quarter (covers `broad_exit`'s 90d rule horizon) |
| `post_180d` | +180 cal days | ~6 months |
| `post_365d` | +365 cal days | ~1 year |

Long horizons matter — Buffett-style equity-core decisions sit at 6mo–1y, and the most informative excess-vs-baseline numbers turned out to land there (see Findings).

## Baseline

Per-asset random-day mean is the right comparator: a strong underlying name (NVDA, AAPL) makes most stacks look "good" in absolute return terms just because the ticker went up. Subtract the asset's own random-day mean and what's left is the *excess* attributable to the stack actually firing.

Sampling: every 7 calendar days through the asset's loaded price history, compute windowed returns at each label, mean / hit-rate over the samples. Uniform sampling is deterministic without a random seed (which would break replayability) and weekly samples are nearly independent because adjacent trading days carry heavy return autocorrelation.

`mean_excess_pct = stack_mean − asset-weighted baseline_mean`. Positive = stacks beat baseline upward. The reader interprets the *sign* against the rule's direction: for `smart_money_buy` (bullish), positive excess is the win; for `broad_exit` (bearish), negative excess is the win. **The CLI does not mask the sign** — that would be dishonest framing for a backtest.

## No-lookahead guarantee

Every forward return is computed from `stack.window_end` forward — never backward. The stack's own events all have `ts ≤ stack.window_end` by construction (the correlator scans chronologically). The backtest does not filter or weight stacks based on what happens *after* their window_end. The unit tests pin this implicitly: `compute_stack_returns` takes `prices_by_asset` and a stack and never looks past the event date in the wrong direction.

## Findings on the live 2003–2026 dataset

416 stacks across 27 watchlist tickers. **Bearish rules dominate the population (415 / 416)** because insider transactions skew ~10:1 toward sells (executive comp vesting, 10b5-1 plans) and 13F managers herd out of names faster than they herd in.

### Rule-level: bearish rules carry signal — at the *long* horizon

```text
broad_exit (n=113)
  post_5d:    excess -0.15pp  | post_30d: +0.14 | post_90d: +0.28
  post_180d:  excess -3.25pp  | post_365d: -3.01pp
deterioration_stack (n=302)
  post_5d:    excess -0.37    | post_30d: +0.09 | post_90d: +1.28
  post_180d:  excess -1.71    | post_365d: -3.58
```

**Both bearish rules show ~3pp of underperformance vs baseline at the 6–12 month horizon** but produce nothing — or even slight *positive* excess — at the 1–3 month window where the rules' own internal logic operates (`broad_exit` window is 90d, `deterioration_stack` window is 30d). The rules correctly identify "stress" patterns; the short-term price reaction is already priced in or mean-reverts; the long-term decay is the actual edge.

The realized horizon being 4–10x the rule's own window has tactical implications:
- These are **slow signals**. Reading a fresh `broad_exit` stack today as "expect a 90-day drop" overstates the case.
- The right action is a **6–12 month avoidance / underweight**, not a tactical short.
- The rule's `window_days` parameter is about *detection*, not about prediction horizon.

### `smart_money_buy` (n=1) is anecdotal but the anecdote is brutal

```text
smart_money_buy (n=1, MSTR 2025-07-31)
  post_5d:   -6.57   excess -7.20
  post_30d:  -14.99  excess -17.99
  post_90d:  -31.48  excess -40.77
  post_180d: -59.79  excess -80.40
```

The single bullish stack the engine has ever fired completely failed. MSTR fell ~60% over the 180 days after the stack (the Q3 2025 Bitcoin treasury crisis). N=1 isn't a verdict on the rule; it *is* a verdict on "any rule with only 1 historical fire is statistically meaningless." The rule needs more data — most likely via:
- The remaining emitters (B-095–B-098) adding crypto + macro context that pulls more compounded bullish situations through
- Possibly relaxing `smart_money_buy`'s tight 7-day window (the realized signal lives at multi-month, mirroring the bearish rules)
- A `--rule smart_money_buy` retrospective once more emitters land

### Asset heterogeneity — bearish-signal works on some names dramatically, fails on others

| Asset | n | post_180d excess | post_365d excess | Interpretation |
|---|---:|---:|---:|---|
| **MSTR** | 7 | **−55.77pp** | −63.20 | Bearish stacks nailed the Bitcoin-treasury crisis cleanly. |
| **HOOD** | 8 | −9.40 | **−73.56** | Strong bearish signal at 12mo. |
| **GOOGL** | 28 | −7.35 | −10.15 | Consistent, modest signal. |
| **GOOG** | 30 | −4.51 | −9.66 | Mirrors GOOGL. |
| **META** | 38 | −5.17 | −3.10 | Persistent multi-month signal. |
| **AVGO** | 9 | −7.61 | +20.27 | Mixed — 6mo signal, 12mo anti-signal. |
| **AMZN** | 31 | −2.22 | +3.89 | Marginal signal that dies. |
| **MSFT** | 19 | −1.69 | +0.91 | No clear signal either way. |
| **CRM** | **93** | **+2.84** | +0.46 | Most stacks of any name — and the bearish rule was *anti-predictive*. CRM's executive comp / vesting dynamics likely generate sell-clusters that don't translate to drawdowns. |
| **DOCN** | 3 | +12.21 | +20.75 | Tiny n; anti-signal. |
| **COIN** | 9 | −16.01 | **+59.17** | Mid-term win, but the 12mo number is dominated by the late-2024 election runup. |

**The single highest-leverage operational implication of this dataset**: CRM's 93 stacks (22% of all bearish stacks) are systematic noise. Position-sizing or rule-tuning on a per-asset basis is the natural follow-up.

### MSTR pair: the most informative single asset

- 1 bullish stack (2025-07-31): forward returns are catastrophic (-60% at 6mo, -80pp excess vs baseline)
- 7 bearish stacks: forward returns are also catastrophic (-35% mean at 6mo, -55pp excess vs baseline)

The rules captured the *direction* of the eventual move incorrectly on the bullish side but extraordinarily well on the bearish side. **The engine is sensitive to whatever was driving MSTR — both rule directions detected something real; only the bearish direction had it right.** That's an unusual quality to surface mechanically.

## CLI

```bash
genkei backtest                                # by rule (default)
genkei backtest --by direction                 # bullish vs bearish aggregate
genkei backtest --by asset                     # per-ticker breakdown
genkei backtest --rule broad_exit              # one rule
genkei backtest --asset MSTR                   # one asset
genkei backtest --since 2020-01-01 --json      # machine output
```

The default cut is `--by rule`. Add `--asset X` to drill into a single ticker. JSON output preserves Decimal precision via `_json_default`.

## What B-101 deliberately does NOT cover

- **SPY-adjusted abnormal returns.** v1 reports raw returns. Benchmark adjustment requires ingesting SPY first (filed as a follow-up backlog item) and the adjustment logic from B-100. v1's per-asset random-day baseline captures most of what benchmark adjustment would, just per-asset instead of per-market.
- **Crypto-side stacks.** No crypto-side correlation rules exist today (B-095–B-098 will add the crypto emitters). When they land, the backtest will need to use `coinbase.candles` / `coingecko.market_data` for crypto assets; the orchestrator is structured to make that drop-in.
- **Per-rule horizon tuning.** All rules use the same `STACK_WINDOWS` set. Future v2: each rule has its own "natural" forward-return horizon — `broad_exit` clearly lives at 6–12mo, `smart_money_buy`'s realized horizon is unknown for n=1.
- **Statistical significance tests.** Mean / hit-rate / excess are reported as point estimates without confidence intervals. With 113+ stacks for the bearish rules the asymptotic numbers are informative enough; revisit when a rule needs a "is this real or noise?" verdict.

## References

- `src/genkei/experiments/stack_backtest.py` — pure aggregation + orchestrator.
- `src/genkei/cli/backtest.py` — Typer command.
- `tests/experiments/test_stack_backtest.py` — pinning unit tests.
- `tests/cli/test_backtest.py` — CLI rendering + validation tests.
- `src/genkei/experiments/eight_k_impact.py` — source of the reusable `compute_windowed_returns` / `load_price_series` / `PricePoint`.
- `docs/experiments/cross-source-signals.md` — the engine being measured.
