# `genkei watchlist`

Watchlist coverage + data-lake health (list / health / drift / score / gaps).

## Options

```text
Usage: python -m genkei.cli watchlist [OPTIONS] COMMAND [ARGS]...              
                                                                                
 Watchlist coverage + data-lake health.                                         
                                                                                
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --help          Show this message and exit.                                  │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ list    Show watchlist assets by sleeve.                                     │
│ health  Show per-source ingest health + primary-table liveness + schema      │
│         drift.                                                               │
│ drift   B-072 schema-drift check — load-bearing keys per (source,            │
│         endpoint_kind).                                                      │
│ score   Compute or read per-asset composite scores (B-065 rubric).           │
│ gaps    Show per-asset freshness across all sleeves.                         │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Example

**Human output**

```console
$ genkei watchlist list --sleeve crypto
crypto (8)
----------
  BTC      primary    bitcoin              Bitcoin
  ETH      primary    ethereum             Ethereum
  SOL      primary    solana               Solana
  LINK     primary    chainlink            Chainlink
  JUP      primary    jupiter-exchange-solana Jupiter
  SUI      primary    sui                  Sui
  PYTH     secondary  pyth-network         Pyth Network
  RENDER   secondary  render-token         Render
```

**JSON (`--json`)**

```console
$ genkei watchlist list --sleeve crypto --json
{
  "crypto": [
    {
      "symbol": "BTC",
      "name": "Bitcoin",
      "coingecko_id": "bitcoin",
      "tier": "primary"
    },
    {
      "symbol": "ETH",
      "name": "Ethereum",
      "coingecko_id": "ethereum",
      "tier": "primary"
    },
... (38 more lines)
```

## Subcommands

`watchlist` is a command group. Run `genkei watchlist <sub> --help`:

- `list` — watchlist assets by sleeve
- `health` — per-source ingest health + primary-table liveness + schema drift
- `drift` — focused schema-drift view (B-072)
- `score` — per-asset composite signal score (B-065)
- `gaps` — per-asset freshness (last data point + age)

`watchlist list --sleeve prices` includes price-only `crypto_price_targets`
(CoinGecko) and `yahoo_price_targets` entries used for reflection/outcome
tracking without enrolling those tickers in research signal pipelines.

## See also

[`prices`](prices.md) · [`macro`](macro.md)

---

_Page generated for B-047. Example output is a point-in-time capture; shape is stable, values are not. Regenerate when the command's flags change._
