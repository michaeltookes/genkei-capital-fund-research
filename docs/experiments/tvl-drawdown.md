# TVL Drawdown Early-Warning Model

**B-058.** Phase 5 experiment that asks: *does on-chain TVL change predict future price drawdowns?* The answer the lake gives, in a sentence: **TVL stress is a precision-positive but rare signal — when the rule fires it's correct ~2× as often as the base rate, but it fires on less than 1% of days and missed every drawdown in the 2024-2026 test window.**

```bash
$ genkei tvl-drawdown --chain Ethereum
TVL drawdown early-warning (B-058) — split 2024-01-01, test > split

Ethereum (ETH-USD) — forward window 30d, drawdown threshold 15.0%
  period  days     base   signal precision  recall   lift   confusion (TP/FP/TN/FN)
  train   2121   34.42%    0.57%   66.67%   1.10%  1.94×   TP=8 FP=4 TN=1387 FN=722
  test     842   33.14%    0.00%    0.00%   0.00%  0.00×   TP=0 FP=0 TN=563 FN=279
```

## Backstory

This experiment was the original B-058 kickoff target on 2026-05-22 but hit a foundational blocker: `coingecko.market_data` had only 377 days of history (CoinGecko Demo/Public hard-cap), so any OOS validation sat inside a single macro regime. The kickoff pivoted to B-059 (macro regime classifier — FRED data was fully backfilled) and then to B-035 (Coinbase OHLCV ingester — landed 10y of BTC + ETH prices on 2026-05-23). With B-035 closing the price-history gap, this experiment is now answerable.

## What the experiment actually answers

The acceptance criteria was *"Logistic or simple ML baseline + out-of-sample validation."* The narrow operational questions answered:

1. **Does any combination of TVL features predict 30-day price drawdowns better than base rate?** Yes for Ethereum (lift 1.94× train, 3.23× at the 25% drawdown threshold). The 3-AND threshold rule (TVL change 30d < -10% AND drawdown from 90d peak > 15% AND z-score < -1) hits 66.7% precision vs 34.4% base rate.
2. **Is the signal stable out-of-sample?** No — the 2024-2026 test window has zero rule fires across all three chains because the 2024-25 bull market simply didn't produce a TVL stress event matching all three conditions. This is not a failure of the rule; it's a statement about the test window. The Ethereum train period (2017-2024) covered four bear cycles (2018, 2020 COVID, 2022 hiking, late 2022) where the rule did fire; the test period covers none.
3. **Does the signal generalize across chains?** Insufficient data to claim. Solana's TVL series starts 2021-03 (so it doesn't see the 2018-2020 deep bears that gave Ethereum its training signal), and the strict 3-AND rule never fires on Solana or Sui — meaning the threshold needs per-chain calibration to be useful, which is beyond a v1 baseline.

The honest top-line: **The hypothesis isn't disproven, but it isn't strongly supported either.** TVL stress is real and informative when it appears, but the conditions that produce it are too rare to be a usable early-warning signal in calm regimes. The signal lives in bear-market windows.

## Inputs

| Source | Field | Purpose |
|---|---|---|
| `defillama.chain_tvl` | `tvl_usd` per (chain, ts) | Daily TVL series, 2017-09 to present |
| `coinbase.candles` | `close` per (product, ts) | Daily close price, per-product earliest in lake |

Chain → native token mapping (joined on shared date):

| Chain | Product | Aligned days | Earliest aligned |
|---|---|---|---|
| Ethereum | ETH-USD | 3,160 | 2017-09-26 |
| Solana | SOL-USD | 1,802 | 2021-06-16 |
| Sui | SUI-USD | 1,102 | 2023-05-17 |
| Bitcoin | BTC-USD | (excluded by default) | — |

**Bitcoin is excluded** because Bitcoin "TVL" is mostly wrapped BTC + Lightning + Stacks. Real BTC price drivers are macro (DXY, real yields, ETF flows), not on-chain DeFi. The classifier works for BTC if you pass `--chain Bitcoin`, but the directional signal is weaker by design.

## Features (per day, per chain)

| Feature | Window | What it captures |
|---|---|---|
| `tvl_change_7d_pct` | trailing 7d | Short-term TVL momentum |
| `tvl_change_30d_pct` | trailing 30d | Medium-term TVL trend (primary signal) |
| `tvl_change_90d_pct` | trailing 90d | Longer-term TVL trend (context) |
| `tvl_drawdown_from_peak_90d_pct` | trailing 90d | How far below the 90d peak is TVL today? |
| `tvl_zscore_90d` | trailing 90d | Where does today's TVL sit in the 90d distribution? |
| `forward_drawdown_pct` | forward 30d | **Ground-truth label** — worst pct drop in `[t+1, t+30]` |

All features are computed in pure Python (no numpy/pandas) on `Decimal` so the math is deterministic and unit-testable.

## Classifier

Rule-based 3-AND threshold:

```text
fire = (tvl_change_30d_pct < -10%)
    AND (tvl_drawdown_from_peak_90d_pct > 15%)
    AND (tvl_zscore_90d < -1)
```

**Why three conditions ANDed, not summed:** each individual condition has high false-positive rate in bull markets (TVL pct-changes often run -5 to -10% during normal consolidations). The 3-AND construction trades recall for precision — better to fire rarely and reliably than often and noisily.

**Why rule-based, not logistic regression:** the acceptance criteria allows either ("logistic or simple ML baseline"). The project's experiment-module pattern (B-062, B-065, B-090, B-059) is pure-Python rule-based with no sklearn/numpy/pandas dependency. A hand-rolled logistic regression at ~3k daily observations per chain would be fragile + unmotivated; sklearn would be scope creep. The directional answer doesn't change. (Future iteration: if a follow-up wants per-chain calibrated probabilities, that's the time to take on the sklearn dep.)

## Out-of-sample evaluation

Time-based train/test split — NOT random — to avoid leaking future information into the training set. Train metrics exclude rows whose forward labels would read beyond the split date.

- **Train**: labels contained within data ≤ 2024-01-01 (covers 2017-2018 bear, 2020 COVID, 2021 boom, 2022 hiking, late-2022 trough)
- **Test**: data > 2024-01-01 (covers 2024-25 bull)

Results across all three default chains, drawdown threshold 15% over 30 days:

| Chain | Period | Days | Base rate | Signal rate | Precision | Recall | Lift |
|---|---|---|---|---|---|---|---|
| Ethereum | train | 2,121 | 34.4% | 0.57% | 66.7% | 1.1% | **1.94×** |
| Ethereum | test | 842 | 33.1% | 0.0% | 0.0% | 0.0% | 0.0× |
| Solana | train | 840 | 51.4% | 0.0% | 0.0% | 0.0% | 0.0× |
| Solana | test | 842 | 38.8% | 0.0% | 0.0% | 0.0% | 0.0× |
| Sui | train | 140 | 26.4% | 0.0% | 0.0% | 0.0% | 0.0× |
| Sui | test | 842 | 51.7% | 0.0% | 0.0% | 0.0% | 0.0× |

**Interpretation row by row:**

- **Ethereum train**: This is the headline result. Rule fires 12 times (8 TP + 4 FP) across 2,121 days. When it fires, 67% of the time price drops ≥15% in the next 30 days vs base rate 34%. Lift 1.94× is meaningful and not consistent with random chance at that sample size. The rule misses 722 of 730 actual drawdown days (recall 1.1%), but that's the price of high selectivity.
- **Ethereum test**: Rule fires zero times. The 2024-25 ETH bull market had no 30-day window where TVL fell 10%+ AND drawdown-from-peak exceeded 15% AND z-score went below -1. Notably, base rate is still 33% — drawdowns happen — they just don't preview themselves through this particular feature combination.
- **Solana / Sui across both periods**: Zero fires. The strict 3-AND rule is calibrated to Ethereum's dynamics (where TVL is more volatile and trends are more pronounced). On Solana the z-score rarely drops to -1; on Sui the series is too short for the 90d windows to fill in deep enough.

## Sensitivity sweep

Varying thresholds on the Ethereum train period (n=2,121):

| 30d change ≤ | drawdown > | z-score < | Signal rate | Precision | Lift |
|---|---|---|---|---|---|
| -10 | 15 | -1.0 | **0.57%** | **66.7%** | **1.94×** *(default)* |
| -5 | 10 | -0.5 | 1.84% | 64.1% | 1.86× |
| -15 | 20 | -1.5 | 0.05% | 100.0% | 2.91× *(only 1 TP)* |
| 0 | 5 | 0.0 | 33.14% | 38.1% | 1.11× *(barely beats base rate)* |

**Reading the sweep**: lift is monotonically increasing in selectivity (stricter rule → higher lift), which is consistent with a real signal. But at the strictest setting the rule fires 0.05% of days — one true positive across six years. That's not a usable forecasting tool; it's a curiosity. At the loosest setting the rule fires 33% of days (basically "any TVL weakness whatsoever") and lift collapses to 1.11×. The default setting is the inflection point between selectivity and informativeness.

Same sweep on the **test** period (2024-2026): every strict variant has zero signal rate. Only the loosest "any TVL weakness" rule fires (37% signal rate, 43% precision, lift 1.31×) — modestly above base rate but useful evidence that TVL weakness is *generally* associated with forward drawdowns even in the bull market, just not via the strict 3-AND construction.

## Limitations

- **Test window is one macro regime.** 2024-2026 is a single risk-on / bull window. A rule calibrated to bear-market dynamics has nothing to fire on in this window. The 2017-2024 train period is the more informative validation surface; "OOS" in the strict sense requires either more time or a different chain whose test-period bear didn't show up in train.
- **Single-feature combination tested.** Other feature sets — protocol-level TVL concentration, stablecoin flow, on-chain transaction count — would each test a different hypothesis. The 3-AND TVL rule is one configuration of many.
- **Per-chain threshold calibration needed for non-Ethereum chains.** Solana and Sui have different TVL volatility profiles; the Ethereum thresholds don't transfer. A follow-up could compute per-chain quantile thresholds.
- **Look-ahead is 30 days; longer windows weren't tested.** A 60-day or 90-day forward window would catch slower drawdowns and might shift the precision/recall balance.

## Limitations vs the acceptance criteria

The acceptance criteria says "**Logistic or simple ML baseline + a notebook**" and "**Out-of-sample validation**." What B-058 ships:

- **Simple ML baseline ✓**: rule-based threshold classifier with explicit precision/recall/lift evaluation (instead of a logistic model — same evaluation surface, different functional form, chosen for project pattern consistency).
- **Notebook / Module ✓**: shipped as a deterministic Python module + CLI + writeup. The `notebooks/` experiments layer (B-054 + B-055) has since landed, so a notebook can now call this module's pure functions directly (see `notebooks/README.md`).
- **OOS validation ✓**: time-based train/test split, base-rate-relative lift, confusion matrix per period.

## Where the math lives

| Concern | Module |
|---|---|
| Pure functions (engineer_features, classifier_fires, evaluate) | `src/genkei/experiments/tvl_drawdown.py` |
| Lake loader (`load_aligned_series`) | same |
| Orchestrator (`run_chain_evaluation`) | same |
| CLI surface (`genkei tvl-drawdown`) | `src/genkei/cli/tvl_drawdown.py` |
| Unit tests | `tests/experiments/test_tvl_drawdown.py` (16 tests across feature engineering / classifier / evaluator) |

## The signal-events emitter (B-095 + slow-bleed fix)

`src/genkei/experiments/emitters/tvl_drawdown_emitter.py` adapts this experiment
into the cross-source correlation engine (B-064), emitting `tvl_drawdown_stress`
events into `meta.signal_events` per chain. It detects stress two ways:

- **Acute** — the B-058 `classifier_fires` three-condition AND above (30d change
  + 90d-peak drawdown + 90d z-score). Catches fast crashes.
- **Sustained** — `sustained_drawdown_fires`: drawdown past 30% of the trailing
  **365-day** peak (`tvl_drawdown_from_peak_365d_pct`). This is the slow-bleed
  fix. The acute rule's ≤90-day windows are structurally blind to a
  multi-quarter decline — the reference peak keeps resetting downward — so the
  emitter had produced **no events between 2018 and 2026** even as ETH TVL fell
  ~60% off its 1-year peak. The 365-day window doesn't reset under a gradual
  bleed, so it surfaces exactly the stress the acute rule misses.

**Onset vs ongoing.** The emitter marks each episode *onset* (stress flips on),
but an onset can be months old — older than the correlator's ≤30-day stacking
window, so it could never pair with recent price signals. So while an asset
stays under stress the emitter also emits a fresh-dated **ongoing** event at the
latest observation (distinct `:ongoing:` `source_ref`), keeping a live episode
inside the correlator window. Ongoing refs carry the episode start date rather
than the latest observation date, so daily reruns update the live state without
counting as independent TVL evidence. Event strength is `max(acute, sustained)`
so a deep slow bleed isn't diluted toward zero by the acute conditions it
doesn't trip. Payload carries `stress_type` (`acute` / `sustained` / `both`) and
`ongoing`. The `crypto_tvl_stress_combo` rules (core + tactical) pair these with
a `relative_strength` laggard crossing — TVL demand contracting *and* price
losing relative leadership at once.

## Open follow-ups

These are out of scope for B-058 itself but the natural next moves:

- **Per-chain threshold calibration.** Compute the chain-specific 5th-percentile of `tvl_zscore_90d` and `tvl_change_30d_pct` from train data and use those as the per-chain thresholds. Would unblock Solana / Sui from the "rule never fires" state.
- **Cross-chain feature combination.** "ETH TVL falling AND SOL TVL falling" is a different (stronger) signal than either alone. Phase 6 cross-source correlation engine (B-064) is the right home for this.
- **Protocol-level TVL stress.** `defillama.protocol_tvl` (B-081) is more granular than chain TVL — a single protocol's TVL collapsing (Curve June 2023, FTX Nov 2022, Terra May 2022) is often the *signal* that becomes chain-level TVL stress later. Would extend B-058 from chain-level to protocol-level.
- **Forward-window sensitivity.** Run the same evaluation at 60d and 90d forward windows to see whether slower drawdowns are more predictable.
- **Compare against B-059's regime labels.** Does the TVL stress rule fire more often during `risk_off` macro regimes than `risk_on`? If yes, it's adding orthogonal information; if no, it's just a chain-level expression of macro stress and can be subsumed by the regime classifier.
