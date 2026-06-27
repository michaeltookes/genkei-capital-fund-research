# `genkei revenue-divergence`

Protocol revenue vs token price — fundamentals/valuation divergence.

## Options

```text
Usage: python -m genkei.cli revenue-divergence [OPTIONS]                       
                                                                                
 Protocol revenue vs token price — flag fundamentals/valuation divergence.      
                                                                                
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --slug                    TEXT                  DefiLlama protocol slug. If  │
│                                                 omitted, summarize every     │
│                                                 mapped protocol.             │
│ --since                   TEXT                  Start date (YYYY-MM-DD).     │
│                                                 Emits time series in --slug  │
│                                                 mode.                        │
│ --until                   TEXT                  End date (YYYY-MM-DD).       │
│ --window-days             INTEGER RANGE [x>=1]  Trailing window for          │
│                                                 fees/revenue (days).         │
│                                                 [default: 30]                │
│ --lookback-days           INTEGER RANGE [x>=1]  Lookback span for trend      │
│                                                 comparison (days).           │
│                                                 [default: 90]                │
│ --significance-pct        FLOAT RANGE [x>=0.0]  Trend deltas smaller than    │
│                                                 this are treated as flat     │
│                                                 (percent).                   │
│                                                 [default: 10.0]              │
│ --json                                          Emit machine-readable JSON   │
│                                                 instead of human table.      │
│ --config                  PATH                  Watchlist path.              │
│                                                 [default:                    │
│                                                 /Users/michaeltookes/Deskto… │
│                                                 Projects/genkei-capital-fun… │
│ --help                                          Show this message and exit.  │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Example

**Human output**

```console
$ genkei revenue-divergence
slug                   horizon                  token                  kind                     price%       rev%      P/F now
------------------------------------------------------------------------------------------------------------------------------
  suilend              crypto:core:lending      suilend                price-leads-down         -46.0%     +45.2%        0.85x
  jupiter-staked-sol   crypto:core:liquid-staking jupiter-exchange-solana price-leads-up           +50.7%     -32.6%       35.72x
  chainlink-staking    crypto:core:oracle       chainlink              price-leads-down         -16.8%     +55.3%       62.74x
  curve-dex            crypto:core:dex          curve-dao-token        price-leads-down          -8.7%     +59.8%        4.23x
  uniswap-v3           crypto:core:dex          uniswap                price-leads-down         -16.1%     +16.6%        6.69x
  aave-v3              crypto:core:lending      aave                   aligned                  -12.0%     -16.3%        2.65x
  bluefin-pro          crypto:core:derivatives  bluefin                aligned                  -30.5%     -48.9%        8.76x
  bluefin-spot         crypto:core:dex          bluefin                aligned                  -30.5%     -12.3%        1.83x
  cetus-clmm           crypto:core:dex          cetus-protocol         aligned                  -32.5%     -12.8%        3.07x
  chainlink-requests   crypto:core:oracle       chainlink              aligned                  -16.8%     -34.7%     2437.13x
  compound-v3          crypto:core:lending      compound-governance-token insufficient-data        -17.7%        n/a        7.50x
  deepbook-v3          crypto:core:dex          deep                   insufficient-data        -41.3%        n/a       36.93x
  jupiter-lend         crypto:core:lending      jupiter-exchange-solana aligned                  +50.7%     +14.0%       21.41x
  jupiter-perpetual-exchange crypto:core:derivatives  jupiter-exchange-solana aligned                  +50.7%     +15.5%        5.38x
  lido                 crypto:core:liquid-staking lido-dao               aligned                  -19.4%     -52.6%        0.50x
  navi-lending         crypto:core:lending      navi                   aligned                  -16.3%     -60.9%        1.14x
... (2 more lines)
```

**JSON (`--json`)**

```console
$ genkei revenue-divergence --json
[
  {
    "slug": "chainlink-staking",
    "name": "Chainlink Staking",
    "category": "Oracle",
    "coingecko_id": "chainlink",
    "horizon_tag": "crypto:core:oracle",
    "as_of": "2026-06-26",
    "window_days": 30,
    "lookback_days": 90,
    "price_change_pct": "-16.83644941086250982042896153",
    "revenue_change_pct": "55.28389484615023179700513009",
    "pf_ratio_now": "62.73601656783766458626833492",
    "pf_ratio_lookback": "109.7390009091432265539725260",
... (258 more lines)
```

## See also

[`tvl`](tvl.md)

---

_Page generated for B-047. Example output is a point-in-time capture; shape is stable, values are not. Regenerate when the command's flags change._
