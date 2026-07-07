# `genkei` CLI reference

One page per subcommand of the `genkei` CLI — the query layer over the
data lake. Each page carries the command's purpose, its full `--help`
flag table, and a worked example (human output + `--json`).

**Convention (B-047):** every subcommand gets a page here. When a new
subcommand lands in `src/genkei/cli/`, add a matching `docs/cli/<name>.md`
so this reference stays complete — the agent reads these when it doesn't
know how to query something.

Every command supports `--json` (machine-readable, one row per result,
never envelope-wrapped) and a human-readable table by default. All
commands read `GENKEI_DATABASE_URL` from the environment.

> Example outputs are point-in-time captures: the *shape* is the contract,
> the *values* will have moved on. Some examples are labelled
> _illustrative_ where the command runs a heavy experiment or needs args
> not available at capture time.

## Commands

| Command | Purpose |
|---|---|
| [`backtest`](backtest.md) | Stack-outcome backtest (B-101) — do historical stacks predict forward returns? |
| [`cot`](cot.md) | CFTC Commitments of Traders — weekly position breakdowns per market / trader category. |
| [`crowding`](crowding.md) | 13F crowding monitor — top crowded watchlist names per quarter + deltas. |
| [`eight-k-impact`](eight-k-impact.md) | 8-K filing impact event study (B-057) — does an 8-K predict short-run drift? |
| [`etf-flows`](etf-flows.md) | Spot crypto ETF daily activity — sum(volume x close) per asset. |
| [`filings`](filings.md) | SEC EDGAR filings (default) or XBRL facts (--concept). |
| [`holdings`](holdings.md) | SEC 13F institutional holdings (--filer / --filer-cik / --cusip). |
| [`insider-clusters`](insider-clusters.md) | Detect insider buy/sell clusters (>=N reporters within K days). |
| [`insiders`](insiders.md) | SEC Form 4 insider transactions (--ticker issuer or --reporter-cik). |
| [`macro`](macro.md) | FRED macro series observations, vintage-aware (--as-of / --all-vintages). |
| [`macro-regime`](macro-regime.md) | Macro regime label per date (risk_on / risk_off / mixed / ...). |
| [`news`](news.md) | GDELT GKG article clusters — filter by watchlist asset / theme / topic / tone. |
| [`news-sentiment`](news-sentiment.md) | News sentiment vs forward returns — Pearson/Spearman + quartiles (B-056). |
| [`prices`](prices.md) | Crypto + equity prices from the lake (CoinGecko / Coinbase / Yahoo). |
| [`query`](query.md) | Ad-hoc read-only SQL escape hatch (timeout + row cap enforced). |
| [`relative-strength`](relative-strength.md) | Crypto peer relative-strength (asset return - peer return per window). |
| [`revenue-divergence`](revenue-divergence.md) | Protocol revenue vs token price — fundamentals/valuation divergence. |
| [`signals`](signals.md) | Cross-source signal correlation engine (B-064) — multi-source agreement stacks. |
| [`stablecoin-flow`](stablecoin-flow.md) | Cross-chain stablecoin supply trajectory + rotation (--all-chains). |
| [`tvl`](tvl.md) | DeFiLlama chain / protocol TVL (default: chains overview). |
| [`tvl-drawdown`](tvl-drawdown.md) | TVL drawdown early-warning (B-058) — does TVL stress predict price drawdowns? |
| [`watchlist`](watchlist.md) | Watchlist coverage + data-lake health (list / health / drift / score / gaps). |
| [`whales`](whales.md) | ETH whale-address daily flow aggregate (--category or --address). |
| [`zcash-usage`](zcash-usage.md) | Zcash shielded-pool adoption — shielded share of supply + trend. |
