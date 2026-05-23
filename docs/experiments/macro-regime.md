# Macro Regime Classifier

**B-059.** Phase 5 experiment: bucket every business day into one of five regime labels — `risk_on` / `risk_off` / `easing` / `tightening_stress` / `mixed` — derived from FRED daily series. Output is queryable as the `analytics.macro_regime_per_date` Postgres view, surfaced via `genkei macro-regime`, and consumed by future versions of the watchlist scoring rubric (B-065 v2's `macro_regime` component).

```console
$ genkei macro-regime
2026-05-13 — risk_on (inputs=4/4, horizon=macro:cross-sleeve:primary)
  DGS10= 4.47 (Δ30d=  0.17), HY= 2.82 (Δ30d= -0.13), VIX=17.87, USD= 118.039 (Δ30d= -0.952)
```

## What this experiment actually answers

The acceptance criteria (B-059) is *"FRED + Treasury + market prices — bucket regimes (e.g. risk-on/risk-off)."* The narrower questions it answers concretely:

1. **What macro regime is today?** A single label that other research sessions and the scoring rubric can join against without re-deriving the synthesis by hand.
2. **What regime was the lake in on date X?** A historical view that lets event-study research (B-057, B-061) condition on macro context — "8-K filings tend to move price more in risk_off regimes" becomes a SQL JOIN against the view.
3. **How long do regimes persist?** The distribution table (`--summary`) shows the empirical base rate of each label.

The bigger question that needs to be answered eventually — *"does today's regime predict tomorrow's asset returns?"* — is a forward-looking experiment B-058 was supposed to ship. Blocked on B-035 (long-history crypto price ingester); without 5+ years of price data the OOS validation isn't credible. B-059 ships the *regime labels themselves*; pairing them with forward returns is a follow-up.

## Inputs

Four FRED daily series, all already in the lake:

| Series | Meaning | Used as | Lake coverage |
|---|---|---|---|
| **DGS10** | 10y Treasury yield | rate level + 30d change | 1962-present |
| **BAMLH0A0HYM2** | HY OAS (high-yield credit spread) | credit-stress level + 30d change | 2023-present |
| **VIXCLS** | VIX | equity-vol level | 1990-present |
| **DTWEXBGS** | Broad USD index | FX direction + 30d change | 2006-present |

**Coverage bottleneck:** the view is restricted to 2006-present (5,096 days) because pre-2006 we have at most 2 of 4 inputs (no USD index, no HY OAS). The regime label degrades gracefully to `mixed` whenever fewer than 3 of 4 inputs have coverage on a given date — better to be honest about missing context than to extrapolate.

**Pre-2023 dates have no HY OAS** (BAMLH0A0HYM2 starts 2023-05). For 2006-2023 the classifier runs on 3 inputs (DGS10, VIX, USD), which is enough to fire `risk_off` from VIX alone or detect easing from rates — the Lehman 2008 and COVID 2020 windows still classify correctly.

## Regime definitions

Priority-ordered (mutually exclusive — the first match wins):

| Priority | Label | Trigger |
|---|---|---|
| 1 | `tightening_stress` | DGS10 +0.30pp over 30d **AND** HY OAS +0.30pp over 30d **AND** VIX > 25 |
| 2 | `risk_off` | HY OAS > 5.0% **OR** VIX > 25 |
| 3 | `easing` | DGS10 -0.50pp+ over 30d |
| 4 | `risk_on` | ≥ 2 bullish inputs: HY < 3.5, VIX < 18, USD -1.0+ over 30d, DGS10 -0.30+ over 30d |
| 5 | `mixed` | None of the above (default) |

**Priority rationale.** `tightening_stress` is the worst regime — rates rising while credit widens while vol elevated is the textbook "everything goes down" environment. Even if it satisfies `risk_off`, the more-specific label is more informative. Conversely, `easing` is a meaningful directional signal, but if VIX is also elevated, the volatility risk dominates and `risk_off` wins.

**Why these thresholds.** Aligned with B-065's `score_macro_regime` (the simpler `+1/-1/0` summer the scoring rubric uses today) so consumers can swap one for the other without re-tuning. The exact numbers are informed by long-run distributions: VIX averages ~20 historically (>25 is 1+ stdev hot), HY OAS averages ~4.0 (>5.0 is ~1 stdev wide), and 30d DGS10 changes of 30bps+ are top-decile moves.

**No z-scoring.** Earlier drafts considered z-scores against a trailing-252-day baseline. The simpler absolute-threshold approach is more interpretable (a reader can verify the label from the breakdown row) and stable across regimes — a 252d-rolling-z that lived through 2022 would normalize 5% HY OAS as "average" because *that year* it was. Absolute thresholds preserve the comparison to historical norms.

## Historical sanity check

Distribution across 5,096 days (2006-01-01 → present):

```console
$ genkei macro-regime --summary --since 2006-01-01
Regime distribution across 5,096 days (horizon=macro:cross-sleeve:primary)
  regime                   days      share
  tightening_stress           3       0.1%
  risk_off                  871      17.1%
  easing                     52       1.0%
  risk_on                   467       9.2%
  mixed                    3703      72.7%
```

**Sanity.** ~17% of days fire `risk_off` over 20 years — roughly matches the intuition that bear-market and crisis periods (2008-09 H2 to mid-2009, mid-2010 sovereign-debt scare, 2011 US downgrade, 2015-16 China deval, 2018 Q4, 2020 COVID, 2022 hiking + Russia, 2024-08 yen carry unwind, 2025 tariff war) cumulatively occupy 15-20% of the period. The 73% `mixed` rate captures the boring middle that dominates calendar time. Only 3 days hit `tightening_stress` — the threshold is strict because the regime is genuinely rare; it kicks only when rates, credit, AND vol all signal simultaneously.

Targeted windows:

| Window | Days | risk_off | risk_on | easing | tightening_stress | mixed |
|---|---|---|---|---|---|---|
| 2008-09-01 to 2008-12-31 (Lehman) | 83 | 75 (90%) | 0 | 0 | 0 | 8 |
| 2020-02-15 to 2020-04-30 (COVID) | 53 | 31 (58%) | 0 | 4 (8%) | 0 | 18 |
| 2022-03-01 to 2022-10-31 (hiking) | 169 | 107 (63%) | 0 | 3 (2%) | 0 | 59 |
| 2024-08-01 to 2024-09-30 (yen unwind) | 43 | 9 (21%) | 4 (9%) | 0 | 0 | 30 |
| 2021-01-01 to 2021-12-31 (boom) | 252 | 1 (0.4%) | 53 (21%) | 0 | 0 | 198 |
| 2026-01-01 to today (recent) | ~95 | small | large | 0 | 0 | medium |

The 2008 Q4 and 2022 hiking windows light up risk_off as expected. The 2021 boom shows 53 risk_on days vs essentially zero risk_off — the bull-market backdrop the rubric should reward. The 2024-08 yen-carry-unwind window saw only ~21% risk_off because VIX spiked sharply but resolved within days — the rolling 30d windows on the other inputs hadn't caught up.

## Where the math lives

| Concern | Module |
|---|---|
| SQL view (canonical, queryable) | `analytics.macro_regime_per_date` (created by `migrations/versions/20260522_create_analytics_macro_regime.py`) |
| Python `classify(inputs) → RegimeResult` (testable equivalent) | `src/genkei/experiments/macro_regime.py` |
| Lake loader `load_regimes(since, until, limit)` | same |
| Distribution summary | `summarize(results) → {label: count}` (same module) |
| CLI surface | `src/genkei/cli/macro_regime.py::macro_regime_cmd` |
| Unit tests | `tests/experiments/test_macro_regime.py` (19 tests pinning every regime + priority collisions + degradation contract + the label set itself) |

**SQL vs Python:** the view and the Python `classify` are intentional duplicates — the SQL version is queryable in any session, the Python version is unit-testable with deterministic synthetic inputs. The thresholds are pinned identically in both. A row-by-row parity check between the two surfaces is exercised during live smoke (not in CI — would require a live DB) and any drift would be caught immediately because the view's output column shape mirrors `RegimeResult`'s fields.

## Consumers

- **B-065 v2 watchlist scoring rubric.** The current `score_macro_regime` component sums +1/-1 from four FRED inputs to produce a single regime score. v2 can join `analytics.macro_regime_per_date` to consume the named label directly, giving the rubric finer-grained input than the `risk_on / mixed / risk_off` it has today.
- **`/research` sessions.** Investment-decision write-ups can SQL-join the view to condition findings on macro context ("This 8-K was filed during risk_off — events of that type historically have N% larger same-day price moves under that regime").
- **B-066 (downstream of B-059).** Per its acceptance criteria, surfaces the regime via `genkei macro --regime`. This experiment's CLI subcommand `genkei macro-regime` already covers that surface; B-066 can either alias or merge.
- **B-064 cross-source correlation engine.** Once it ships, the regime column becomes one of the dimensions it groups by — "do insider-cluster signals fire differently across regimes?" becomes a tractable query.

## Limitations

- **HY OAS only goes back to 2023-05.** Pre-2023 historical analysis runs on 3 inputs. The classifier still fires correctly via VIX-alone for major crises (Lehman, COVID), but credit-led stress that doesn't immediately show up in VIX (like a slowly-widening HY in late 2007) won't classify. Backfilling BAMLH0A0HYM2 to 1996 would close this gap — small follow-up.
- **Absolute thresholds, not regime-relative.** A persistent low-vol regime (like 2017's "everything is 12") would have most days fire `risk_on` because VIX is structurally below 18. Conversely, a persistent high-vol regime would underweight the bearish signal. Future iteration: regime-relative scoring, or a second classifier that runs on a rolling-window-z basis.
- **Equity prices not included.** B-059's spec mentioned "market prices" but the lake doesn't have equity prices yet (B-039 is open). When equity prices land, S&P 500 / NASDAQ momentum can be added as a 5th input.
- **No forward-return validation.** A regime classifier's actual value is conditional return distribution by regime — "given today is risk_off, what's the average 30d return on each watchlist asset?" That requires longer crypto price history (B-058's blocker; depends on B-035) and equity prices (B-039). Once both land, the regime classifier's predictive power becomes an empirical question rather than a definitional one.

## Open follow-ups (filed elsewhere)

These don't ship in B-059 itself but are the natural next moves:

- **Backfill BAMLH0A0HYM2 to 1996.** Closes the pre-2023 HY blind spot.
- **B-035 + B-039.** Once long-history crypto prices and equity prices land, run the regime-conditional return study.
- **B-064 / B-066.** Both depend on this view existing.
