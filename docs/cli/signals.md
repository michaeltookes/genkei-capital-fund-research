# `genkei signals`

Cross-source signal correlation engine (B-064) — multi-source agreement stacks.

## Options

```text
Usage: python -m genkei.cli signals [OPTIONS]                                  
                                                                                
 Cross-source signal correlation engine (B-064) — multi-source agreement        
 stacks.                                                                        
                                                                                
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --asset            -a                    TEXT              Limit to one      │
│                                                            asset (equity     │
│                                                            ticker or         │
│                                                            coingecko id).    │
│ --rule                                   TEXT              Run only the      │
│                                                            named correlation │
│                                                            rule.             │
│ --direction                              TEXT              Filter to bullish │
│                                                            / bearish /       │
│                                                            neutral.          │
│ --asset-class                            TEXT              Limit to one      │
│                                                            asset class       │
│                                                            (crypto / equity  │
│                                                            / protocol /      │
│                                                            macro). The       │
│                                                            reliable way to   │
│                                                            focus on crypto   │
│                                                            stacks (B-130).   │
│ --horizon                                TEXT              Limit to one      │
│                                                            exact horizon tag │
│                                                            e.g.              │
│                                                            crypto:tactical.  │
│ --since                                  TEXT              Earliest event    │
│                                                            date              │
│                                                            (YYYY-MM-DD).     │
│ --until                                  TEXT              Latest event date │
│                                                            (YYYY-MM-DD).     │
│ --events                                                   Dump raw signal   │
│                                                            events instead of │
│                                                            running the       │
│                                                            correlator.       │
│ --top                                    INTEGER RANGE     Max rows.         │
│                                          [x>=1]            [default: 30]     │
│ --json                                                     Emit              │
│                                                            machine-readable  │
│                                                            JSON.             │
│ --rules-path                             PATH              Override the      │
│                                                            signal-rules YAML │
│                                                            location.         │
│                                                            [default:         │
│                                                            /Users/michaelto… │
│                                                            Projects/genkei-… │
│ --benchmark            --no-benchmark                      Show              │
│                                                            benchmark-adjust… │
│                                                            column (B-100).   │
│                                                            Equity stacks     │
│                                                            compare vs SPY    │
│                                                            from              │
│                                                            yahoo.candles,    │
│                                                            crypto stacks vs  │
│                                                            BTC from          │
│                                                            coinbase.candles. │
│                                                            Default on;       │
│                                                            --no-benchmark    │
│                                                            disables.         │
│                                                            [default:         │
│                                                            benchmark]        │
│ --equity-benchma…                        TEXT              Equity-side       │
│                                                            benchmark ticker  │
│                                                            (from             │
│                                                            yahoo.candles).   │
│                                                            [default: SPY]    │
│ --crypto-benchma…                        TEXT              Crypto-side       │
│                                                            benchmark symbol  │
│                                                            (from             │
│                                                            coinbase.candles  │
│                                                            as <SYMBOL>-USD). │
│                                                            [default: BTC]    │
│ --help                                                     Show this message │
│                                                            and exit.         │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Example

> Equity signals dominate the correlator output; to reliably focus on the
> (sparser) crypto sleeve, filter by asset class:
>
> ```console
> $ genkei signals --asset-class crypto        # only crypto stacks
> $ genkei signals --horizon crypto:tactical   # finer: one exact horizon
> ```

**Human output**

```console
$ genkei signals
Cross-source signal stacks (30 found)
-------------------------------------
  window_end   asset    dir      rule                     horizon             score sources  vs_bench  events
  2026-06-21   SOFI     bearish  equity_rel_strength_exit equity:core          2.00       2      5.53  equity_relative_strength/laggard_crossing, insider_clusters/sell_cluster, insider_clusters/sell_cluster
  2026-06-21   MSTR     bullish  equity_rel_strength_entry equity:core          1.40       2    -39.39  equity_relative_strength/leader_crossing, insider_clusters/buy_cluster
  2026-06-17   TSM      bullish  equity_rel_strength_entry equity:core          2.82       2     11.02  insider_clusters/buy_cluster, insider_clusters/buy_cluster, equity_relative_strength/leader_crossing
  2026-06-16   MSFT     bearish  equity_rel_strength_exit equity:core          4.09       2     -5.85  equity_relative_strength/laggard_crossing, equity_relative_strength/laggard_crossing, insider_clusters/sell_cluster, equity_relative_strength/laggard_crossing, +2 more
  2026-06-14   MSTR     bearish  equity_rel_strength_exit equity:core          3.28       2    -22.48  equity_relative_strength/laggard_crossing, insider_clusters/sell_cluster, insider_clusters/sell_cluster, insider_clusters/sell_cluster, +1 more
  2026-06-14   NVDA     bearish  equity_rel_strength_exit equity:core          2.07       2     -0.28  equity_relative_strength/laggard_crossing, insider_clusters/sell_cluster, equity_relative_strength/laggard_crossing
  2026-06-10   AMZN     bearish  equity_rel_strength_exit equity:core          4.35       2     -7.23  insider_clusters/sell_cluster, insider_clusters/sell_cluster, insider_clusters/sell_cluster, insider_clusters/sell_cluster, +3 more
  2026-06-08   AVGO     bearish  equity_rel_strength_exit equity:core          2.57       2     -2.82  insider_clusters/sell_cluster, equity_relative_strength/laggard_crossing, equity_relative_strength/laggard_crossing, equity_relative_strength/laggard_crossing
  2026-06-02   HOOD     bearish  equity_rel_strength_exit equity:core          5.35       2      4.27  insider_clusters/sell_cluster, equity_relative_strength/laggard_crossing, insider_clusters/sell_cluster, equity_relative_strength/laggard_crossing, +5 more
  2026-06-02   PLTR     bearish  equity_rel_strength_exit equity:core          3.83       2     -1.96  equity_relative_strength/laggard_crossing, equity_relative_strength/laggard_crossing, insider_clusters/sell_cluster, equity_relative_strength/laggard_crossing
  2026-06-02   HOOD     bearish  deterioration_stack      equity:core          1.62       2      3.18  insider_clusters/sell_cluster, eight_k_impact/item_5_02, insider_clusters/sell_cluster, insider_clusters/sell_cluster
  2026-05-31   JPM      bearish  equity_rel_strength_exit equity:core          5.54       2    -11.43  insider_clusters/sell_cluster, insider_clusters/sell_cluster, equity_relative_strength/laggard_crossing, insider_clusters/sell_cluster, +3 more
  2026-05-31   COIN     bearish  equity_rel_strength_exit equity:core          4.88       2     -7.91  equity_relative_strength/laggard_crossing, equity_relative_strength/laggard_crossing, equity_relative_strength/laggard_crossing, equity_relative_strength/laggard_crossing, +3 more
  2026-05-31   META     bearish  equity_rel_strength_exit equity:core          3.43       2    -17.53  insider_clusters/sell_cluster, equity_relative_strength/laggard_crossing, insider_clusters/sell_cluster, equity_relative_strength/laggard_crossing, +2 more
  2026-05-31   SNOW     bearish  equity_rel_strength_exit equity:core          1.60       2     72.43  equity_relative_strength/laggard_crossing, insider_clusters/sell_cluster
... (19 more lines)
```

**JSON (`--json`)**

```console
$ genkei signals --json
[
  {
    "rule": "equity_rel_strength_exit",
    "asset": "SOFI",
    "asset_class": "equity",
    "horizon_tag": "equity:core",
    "direction": "bearish",
    "window_start": "2026-04-28T19:00:00-05:00",
    "window_end": "2026-06-21T19:00:00-05:00",
    "span_days": 54,
    "score": "2.00",
    "distinct_sources": 2,
    "event_count": 3,
    "events": [
... (1722 more lines)
```

## See also

[`backtest`](backtest.md) · [`insider-clusters`](insider-clusters.md)

---

_Page generated for B-047. Example output is a point-in-time capture; shape is stable, values are not. Regenerate when the command's flags change._
