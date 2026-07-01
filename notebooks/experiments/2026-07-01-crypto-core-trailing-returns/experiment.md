# Crypto-core trailing returns (BTC / ETH / SOL)

**Date:** 2026-07-01
**Author:** Genkei research desk
**Status:** done
**Horizon tag:** crypto:core:years

## Hypothesis

This is the **reference experiment** for the B-054/B-055 framework, not a
directional bet. It demonstrates the reproducible-experiment shape end to end:
seed → pooled session → `read_sql_df` → a small transform → `write_manifest`.
The stand-in question: over the trailing 30 days, how do the three liquid
crypto-core majors (BTC, ETH, SOL) rank by return? Expectation (from the
2026-06 research sessions): BTC, the lowest-beta anchor, holds up best.

## Data

- **Sources / tables:** `coinbase.candles` (daily close per `product`).
- **Snapshot:** see `manifest.json` — pins the `coinbase` fact-row
  `ingest_run_id`s this run read.
- **Window / universe:** trailing ~30 calendar days; products `BTC-USD`,
  `ETH-USD`, `SOL-USD`.

## Method

For each product, take the latest close and the last close on or before 30
days earlier, and compute the simple return `latest / prior - 1`. The SQL
selects both points' `ingest_run_id`s so the manifest records the exact
normalizer runs behind the fact rows; the notebook renders the ranked result as
a DataFrame.

## Results

Run the notebook against the live lake to repopulate — values move daily. The
*shape* is the contract: three rows (BTC/ETH/SOL) with a `return_30d` column,
ranked. As of the checked-in capture, the trailing-30d ranking was **SOL -5.5%
> BTC -16.1% > ETH -19.8%** — so the "BTC holds up best" hypothesis was
*disproven* this window: SOL had the shallowest 30d drawdown, BTC sat in the
middle. A useful reminder that the lowest-beta-anchor read is a multi-quarter
tendency, not a guarantee in any single 30-day window (and SOL had already
fallen furthest over the longer horizon, so a shallower recent slice is partly
mean-reversion).

## Next steps

- Generalize to the full crypto watchlist (add LINK/JUP and the tactical
  sleeve) and parameterize the window — a natural second experiment.
- If a persistent ranking signal emerges, wire it as a signal emitter
  (`src/genkei/experiments/emitters/`) rather than leaving it in a notebook.
