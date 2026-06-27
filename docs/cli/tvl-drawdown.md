# `genkei tvl-drawdown`

TVL drawdown early-warning (B-058) — does TVL stress predict price drawdowns?

## Options

```text
Usage: python -m genkei.cli tvl-drawdown [OPTIONS]                             
                                                                                
 TVL drawdown early-warning experiment (B-058) — does TVL stress predict price  
 drawdowns?                                                                     
                                                                                
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --chain            TEXT     Restrict to one chain (Ethereum / Solana / Sui / │
│                             Bitcoin).                                        │
│ --drawdown         FLOAT    Forward drawdown threshold (percent). Default    │
│                             15.                                              │
│                             [default: 15.0]                                  │
│ --forward          INTEGER  Forward window in days. Default 30.              │
│                             [default: 30]                                    │
│ --train-end        TEXT     Train/test split date (YYYY-MM-DD). Train = data │
│                             ≤ this; test = data > this. Default 2024-01-01.  │
│ --json                      Machine-readable JSON output.                    │
│ --help                      Show this message and exit.                      │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Example

**Human output**

```console
$ genkei tvl-drawdown
TVL drawdown early-warning (B-058) — split 2024-01-01, test > split
====================================================================================

Ethereum (ETH-USD) — forward window 30d, drawdown threshold 15.0%
  period  days     base   signal precision  recall   lift   confusion (TP/FP/TN/FN)
  train   2091   34.91%    0.53%   72.73%   1.10%  2.08x   TP=8 FP=3 TN=1358 FN=722
  test     876   34.70%    0.00%    0.00%   0.00%  0.00x   TP=0 FP=0 TN=572 FN=304

Solana (SOL-USD) — forward window 30d, drawdown threshold 15.0%
  period  days     base   signal precision  recall   lift   confusion (TP/FP/TN/FN)
  train    810   51.98%    0.00%    0.00%   0.00%  0.00x   TP=0 FP=0 TN=389 FN=421
  test     876   39.84%    0.00%    0.00%   0.00%  0.00x   TP=0 FP=0 TN=527 FN=349

Sui (SUI-USD) — forward window 30d, drawdown threshold 15.0%
  period  days     base   signal precision  recall   lift   confusion (TP/FP/TN/FN)
  train    110   33.64%    0.00%    0.00%   0.00%  0.00x   TP=0 FP=0 TN=73 FN=37
  test     876   52.28%    0.00%    0.00%   0.00%  0.00x   TP=0 FP=0 TN=418 FN=458
```

**JSON (`--json`)**

```console
$ genkei tvl-drawdown --json
[
  {
    "period": "train",
    "chain": "Ethereum",
    "product": "ETH-USD",
    "period_start": "2017-09-26",
    "period_end": "2023-12-02",
    "days_evaluated": 2091,
    "base_rate_pct": "34.91152558584409373505499761",
    "signal_rate_pct": "0.5260640841702534672405547585",
    "precision_pct": "72.72727272727272727272727273",
    "recall_pct": "1.095890410958904109589041096",
    "lift": "2.083188044831880448318804483",
    "true_positives": 8,
... (90 more lines)
```

## See also

[`tvl`](tvl.md)

---

_Page generated for B-047. Example output is a point-in-time capture; shape is stable, values are not. Regenerate when the command's flags change._
