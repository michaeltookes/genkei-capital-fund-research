# DeFiLlama MVP Design

## Objective

Build a lightweight, repeatable daily research flow using only public DeFiLlama data. The
brief should read like an analyst market note, not a founder memo.

Primary use cases:

1. identify TVL and stablecoin-flow trends;
2. support DCA timing decisions;
3. flag zombie-chain or momentum-loss risk;
4. reduce dependence on Twitter-only sentiment.

## Asset scope

Focused assets:

- BTC
- ETH
- SOL
- LINK
- SUI

Everything else is ignored unless needed as ecosystem context. The initial context exception
is the Bitcoin ecosystem bucket, which captures Bitcoin-adjacent DeFiLlama chain/project
labels including Lightning, Stacks, Rootstock/RSK, Babylon, Botanix, Merlin, Bitlayer, BOB,
and configured equivalents.

## Data sources

Configured in `config/defillama.sources.json`:

- `coins.llama.fi/prices/current/...` for current target asset prices;
- `api.llama.fi/v2/chains` for chain-level current TVL;
- `api.llama.fi/v2/historicalChainTvl/{chain}` for 1D, 7D, and 30D TVL changes;
- `api.llama.fi/protocols` for protocol-level ecosystem exposure;
- `stablecoins.llama.fi/stablecoins?includePrices=true` for stablecoin supply context.

No account, paid API key, private credential, or browser session is required.

## Pipeline

1. `scripts/collect_defillama.py`
   - Pulls configured public endpoints.
   - Writes raw API responses under `data/raw/defillama/<timestamp>/`.
   - Writes a manifest containing endpoint names, URLs, and local artifact paths.

2. `scripts/normalize_defillama.py`
   - Loads the latest raw manifest directory.
   - Keeps only BTC, ETH, SOL, LINK, and SUI price records.
   - Keeps focused chain TVL records and derives 1D, 7D, and 30D trend changes.
   - Extracts stablecoin supply context for focused chains when exposed.
   - Labels Bitcoin-adjacent protocol exposure under `Bitcoin ecosystem`.
   - Separates generic Bitcoin CEX/custody exposure so it does not pollute Bitcoin-native ecosystem signal.
   - Emits `data/normalized/defillama/daily-YYYY-MM-DD.json`.

3. `scripts/build_daily_report.py`
   - Builds `reports/daily/defillama-daily-YYYY-MM-DD.md`.
   - Uses analyst sections: scope, prices, chain liquidity, money-flow context, DCA timing,
     zombie risk, target protocol exposure, Bitcoin ecosystem, excluded CEX/custody exposure, and caveats.

4. `.github/workflows/defillama-daily.yml`
   - Runs validation and the live public-API pipeline on a daily schedule or manual dispatch.
   - Uploads generated daily brief artifacts to GitHub Actions.
   - Does not commit generated raw, normalized, or report artifacts.

## Signal definitions

- `momentum loss`: 7-day TVL change is at or below -5%.
- `softening`: 7-day TVL change is negative but above -5%.
- `expanding`: 7-day TVL change is zero or positive.
- `zombie risk: elevated`: TVL below $10M and 7-day TVL change at or below -10%.
- `zombie risk: watch`: 7-day TVL change at or below -10%, regardless of TVL.
- `zombie risk: normal`: above the current risk thresholds.
- `DCA signal: constructive`: expanding TVL with available stablecoin-chain context.
- `DCA signal: neutral`: no decisive TVL edge, or data completeness limits conviction.
- `DCA signal: caution`: momentum loss or acute outflow pressure is present.

These are heuristic defaults for triage, not investment rules.

## Boundaries and caveats

- DeFiLlama TVL and stablecoin supply are flow proxies, not complete order-flow or liquidity
  depth data.
- The scaffold does not collect social sentiment.
- It does not call centralized exchange APIs.
- It does not make trade recommendations.
- It should be paired with price structure, catalyst, unlock, macro, and risk-management
  checks before capital allocation decisions.
