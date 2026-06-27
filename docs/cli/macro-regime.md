# `genkei macro-regime`

Macro regime label per date (risk_on / risk_off / mixed / ...).

## Options

```text
Usage: python -m genkei.cli macro-regime [OPTIONS]                             
                                                                                
 Macro regime label per date (risk_on/risk_off/easing/tightening_stress/mixed). 
                                                                                
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --since          TEXT     Start date (YYYY-MM-DD).                           │
│ --until          TEXT     End date (YYYY-MM-DD).                             │
│ --limit          INTEGER  Max rows. Defaults to 1 if no range.               │
│ --summary                 Collapse to regime-distribution counts over the    │
│                           range.                                             │
│ --json                    Machine-readable JSON output.                      │
│ --help                    Show this message and exit.                        │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Example

**Human output**

```console
$ genkei macro-regime
2026-06-23 — mixed (inputs=4/4, horizon=macro:cross-sleeve:primary)
  DGS10= 4.41 (Δ30d= -0.15), HY= 2.71 (Δ30d= -0.03), VIX=19.49, USD= 120.396 (Δ30d=  1.109)
```

**JSON (`--json`)**

```console
$ genkei macro-regime --json
{
  "results": [
    {
      "ts": "2026-06-23",
      "regime": "mixed",
      "horizon_tag": "macro:cross-sleeve:primary",
      "available_inputs": 4,
      "dgs10": "4.41",
      "dgs10_30d_change": "-0.15",
      "hy_oas": "2.71",
      "hy_oas_30d_change": "-0.03",
      "vix": "19.49",
      "usd_index": "120.3958",
      "usd_index_30d_change": "1.1090"
... (4 more lines)
```

## See also

[`macro`](macro.md)

---

_Page generated for B-047. Example output is a point-in-time capture; shape is stable, values are not. Regenerate when the command's flags change._
