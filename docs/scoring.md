# Watchlist Scoring Rubric

**B-065.** Per-asset daily composite score that synthesizes the existing single-source signals (insider clusters, revenue-divergence, relative-strength, TVL trends, macro regime) into one number with a per-component breakdown.

The goal isn't a black-box "AAPL = 7" output. The goal is *interpretable composition* — every score is the sum of explicit components, each carrying a detail string, all queryable. Future `/research` sessions read the score at the top of any asset analysis instead of re-running the multi-signal synthesis by hand.

```bash
$ genkei watchlist score --ticker AAPL
  asset    sleeve          class     score   breakdown
  AAPL     equity-core     equity      +2    insider-flow=-2 revenue-trend=+2 filings-velocity=+1 macro-regime=+1
```

## Design principles

- **Additive, not multiplicative.** Each component contributes a small signed integer (-3..+3); the composite is the unweighted sum. Multiplicative composition implies one component can *flip the sign* of another, which is rarely what investors actually mean.
- **Interpretable, not optimal.** The rubric isn't ML-derived or backtest-optimized — it's an explicit formula. When a score surprises you, the breakdown tells you exactly which component caused it. Once we have ~6 months of persisted scores under `rubric_version = "v1"`, B-064 (cross-source correlation engine) and B-066 (regime classifier integration) can layer more sophisticated synthesis on top — but the v1 rubric stays queryable as the simple baseline.
- **Versioned, not silent.** `RUBRIC_VERSION` is part of `meta.signals`'s PK. Future rubric changes ship as `v2`, `v3`, etc.; v1 scores stay queryable forever. Backtesting a rubric change becomes a SQL JOIN across versions, not a re-computation against historical data.
- **Asymmetric by asset class.** Equities and crypto don't share signals (no equity prices in the lake yet, no Form 4 for crypto). The rubric uses different components per asset class but caps the composite range identically (-8..+8 nominal).

## Components

### Equity (4 components, range -8..+8)

| Component | Range | Inputs | Logic |
|---|---|---|---|
| `insider_flow` | -3..+3 | Form 4 transactions (B-079) | +3 if buy cluster ≥3 reporters in 30d; +1 if any open-market P buy in 14d; -2 if sell cluster ≥3 reporters in 30d; 0 otherwise. Buy cluster overrides sell cluster when both happen (rare). |
| `revenue_trend` | -2..+2 | XBRL `us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax` (B-028) | +2 if YoY >+15%; +1 if +5-15%; -1 if -10 to 0%; -2 if <-10%; 0 if 0-5% (flat). Pulls latest two quarterly values; matches ~365-day-apart period_ends. |
| `filings_velocity` | -2..+1 | `sec.filings` form_type='8-K' count over 30d | +1 if 1-2 8-Ks (normal cadence); -2 if ≥5 (restatement / executive change / material event volume); 0 otherwise. |
| `macro_regime` | -1..+1 | FRED DGS10 / BAMLH0A0HYM2 / VIXCLS / DTWEXBGS | Shared across all assets in a run — see "Macro regime" below. |

### Crypto (4 components, range -7..+6)

| Component | Range | Inputs | Logic |
|---|---|---|---|
| `relative_strength` | -2..+2 | `analytics.crypto_relative_strength` view (B-090) vs BTC at 30d | +2 if outperforming BTC by 10+pp; +1 by 3-10pp; -1 if underperforming by 3-10pp; -2 by 10+pp; 0 within ±3pp. |
| `tvl_trend` | -2..+2 | `defillama.chain_tvl` for mapped L1s; `defillama.protocol_tvl` for mapped protocol tokens | +2 if TVL +15%+ over 30d; +1 if +5-15%; -1 if -15 to -5%; -2 if <-15%; 0 otherwise or no coverage. |
| `volume_momentum` | -1..+1 | `coingecko.market_data.volume_usd` 7-day avg vs 30-day avg | +1 if 7d > 130% of 30d (surge — interest building); -1 if 7d < 70% of 30d (fade — interest waning); 0 otherwise. |
| `macro_regime` | -1..+1 | Same as equity | Shared across all assets in a run. |

### Macro regime (shared component)

Computed once per scoring run from the four FRED series the rubric reads:

| Input | Bullish (+1) | Bearish (-1) |
|---|---|---|
| DGS10 change vs 30d ago | < -0.30pp (rate ease) | > +0.30pp (rate spike) |
| BAMLH0A0HYM2 (HY OAS) | < 3.5% (tight, risk-on) | > 5.0% (wide, risk-off) |
| VIXCLS | < 18 (benign) | > 25 (elevated) |
| DTWEXBGS change vs 30d ago | < -1.0 (USD weakness, crypto-tailwind) | > +1.0 (USD strength, crypto-headwind) |

The net signal sums directional contributions; rounded to -1/0/+1 with risk-on / mixed / risk-off labels. Every asset in the same run shares this value.

## How to read a score

| Composite | Interpretation |
|---|---|
| **+5 or higher** | Multiple signals aligned bullish. For equity-core / crypto-core (Buffett-style holds), this is an "add-candidate" tier. For crypto-tactical, "consider weighting up." |
| **+2 to +4** | Modest bullish lean. Worth investigating but not by itself an action signal. |
| **-1 to +1** | Neutral. No strong evidence either way. Most assets sit here most days. |
| **-2 to -4** | Modest bearish lean. For equity-core, "wait/observe." For crypto-tactical, "consider trimming or hold." |
| **-5 or lower** | Multiple signals aligned bearish. For crypto-tactical specifically, this is the "trim/exit" tier. For equity-core (no-sell sleeve), it's a "do not add" signal — the existing position stays, but new capital goes elsewhere. |

**Per-sleeve interpretation matters.** Per `CLAUDE.md`:
- **Equity-core** is no-sell Buffett-style; scores inform *add* decisions, not exit decisions.
- **Crypto-core** (BTC/ETH/SOL/LINK) is multi-year hold; same interpretation.
- **Crypto-tactical** (SUI, PYTH, RENDER) is turnover-eligible; scores read symmetrically.

A negative score on an equity-core position doesn't mean "sell" — it means "don't add right now." The user's sleeve discipline still trumps the rubric.

## Storage

```sql
CREATE TABLE meta.signals (
    asset           TEXT        NOT NULL,    -- ticker (AAPL) or coingecko_id (bitcoin)
    ts              TIMESTAMPTZ NOT NULL,    -- UTC daily score timestamp (00:00)
    rubric_version  TEXT        NOT NULL,    -- "v1" today; future rubrics ship as v2/v3
    asset_class     TEXT        NOT NULL,    -- 'equity' | 'crypto'
    sleeve          TEXT        NOT NULL,    -- equity-core | crypto-core | crypto-tactical
    composite_score NUMERIC     NOT NULL,
    components      JSONB       NOT NULL,    -- {name: {score, detail}}
    ingest_run_id   BIGINT      NOT NULL REFERENCES meta.ingest_runs(id),
    PRIMARY KEY (asset, ts, rubric_version)
);
```

Three indexes: `ts DESC` (today's snapshot reads), `(rubric_version, ts DESC)` (version-scoped backtests), `(sleeve, ts DESC)` (research-session sleeve filtering).

Plain table — not hypertable. Volume is ~35 watchlist assets × 1 score/day = ~13k rows/year.

## CLI

```bash
# Compute today's scores in-memory and print sorted by composite DESC.
genkei watchlist score

# Same + write to meta.signals (idempotent on (asset, UTC-day ts, rubric_version)).
genkei watchlist score --persist

# Drill into one asset.
genkei watchlist score --ticker AAPL

# Filter to one sleeve.
genkei watchlist score --sleeve crypto-tactical

# Read previously-persisted history.
genkei watchlist score --since 2026-05-01

# Machine-readable (every mode).
genkei watchlist score --json
```

`--persist` writes one row per asset under a fresh `meta.ingest_runs` row of source `watchlist_scoring`, endpoint `score`. The recurring-cron flow is the user's responsibility today — wire `genkei watchlist score --persist` into a daily GitHub Actions workflow on the self-hosted runner if you want automatic daily persistence. (A `watchlist-scoring-daily.yml` workflow shipping this is a small follow-up.)

## Rubric evolution

The hard rule: **never modify v1 in place.** If a component's logic needs to change, ship a `v2` rubric. The `meta.signals` PK includes `rubric_version` so v1 and v2 scores coexist. To backtest a rubric change: SQL JOIN `v1` vs `v2` rows on the same (asset, ts) and compare composite_score distributions / decision outcomes.

Likely v2 candidates as the lake grows:
- Add a `protocol_revenue_divergence` component for crypto tokens whose coingecko_id maps to a watchlist protocol (only LINK qualifies under v1 since chainlink-* are the only protocol→token mappings whose token is in `crypto:` today; this expands as more protocol tokens are added).
- Weight `insider_flow` by *dollar value* of the cluster, not just reporter count (a 4-reporter $200M cluster is materially different from a 4-reporter $5K cluster — both score +3 in v1).
- Add a stablecoin-supply flow component for crypto-core (chain stables growing = capital arriving = bullish for chain L1 token; chain stables shrinking = bearish).

## Where the math lives

| Concern | Module |
|---|---|
| Pure scoring functions (`score_insider_flow` etc.) | `src/genkei/experiments/watchlist_scoring.py` |
| Composers (`compose_equity_score`, `compose_crypto_score`) | same |
| Lake loaders (`_load_macro_inputs`, `_load_equity_signals`, `_load_crypto_signals`) | same |
| Orchestration (`compute_today`, `persist_scores`, `load_latest_scores`) | same |
| CLI surface (`genkei watchlist score`) | `src/genkei/cli/watchlist.py::score_cmd` |
| Schema migration | `migrations/versions/20260522_create_meta_signals.py` |
| Pure-function unit tests | `tests/experiments/test_watchlist_scoring.py` |
