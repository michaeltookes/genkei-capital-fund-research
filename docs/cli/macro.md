# `genkei macro`

FRED macro series observations, vintage-aware (--as-of / --all-vintages).

## Options

```text
Usage: python -m genkei.cli macro [OPTIONS]                                    
                                                                                
 FRED macro series observations (vintage-aware).                                
                                                                                
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ *  --series              -s      TEXT                  FRED series id, e.g.  │
│                                                        DGS10.                │
│                                                        [required]            │
│    --since                       TEXT                  Start observation     │
│                                                        date (YYYY-MM-DD).    │
│    --until                       TEXT                  End observation date  │
│                                                        (YYYY-MM-DD).         │
│    --as-of                       TEXT                  Pin to vintages known │
│                                                        on this date          │
│                                                        (YYYY-MM-DD).         │
│                                                        Defaults to latest    │
│                                                        known vintage.        │
│    --all-vintages                                      Return every revision │
│                                                        row (no per-ts        │
│                                                        dedupe).              │
│    --regime                                            Annotate each         │
│                                                        observation with the  │
│                                                        prevailing macro      │
│                                                        regime as-of that     │
│                                                        date (B-066).         │
│    --limit                       INTEGER RANGE [x>=1]  Max rows.             │
│                                                        [default: 30]         │
│    --max-snapshot-age-…          FLOAT RANGE [x>=1]    Warn on stderr when   │
│                                                        the FRED pipeline's   │
│                                                        last successful run   │
│                                                        is older than this    │
│                                                        many hours (default   │
│                                                        36h). Judged on the   │
│                                                        ingest run, not the   │
│                                                        observation date —    │
│                                                        FRED series have      │
│                                                        mixed cadence         │
│                                                        (daily/monthly/quart… │
│                                                        so a weeks-old        │
│                                                        monthly observation   │
│                                                        is not stale. The     │
│                                                        --json stdout is      │
│                                                        never altered.        │
│                                                        [default: 36.0]       │
│    --json                                              Emit machine-readable │
│                                                        JSON instead of human │
│                                                        table.                │
│    --config                      PATH                  Watchlist path.       │
│                                                        [default:             │
│                                                        /Users/michaeltookes… │
│                                                        Projects/genkei-capi… │
│    --help                                              Show this message and │
│                                                        exit.                 │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Example

**Human output**

```console
$ genkei macro --series DGS10 --limit 3
DGS10 (3 rows, latest-vintage, horizon=macro:cross-sleeve:primary)
------------------------------------------------------------------
  ts           realtime_start   realtime_end                value
  2026-06-23   2026-06-25       9999-12-31                 4.4100
  2026-06-22   2026-06-24       9999-12-31                 4.5000
  2026-06-21   2026-06-23       9999-12-31                 4.5100
```

**Regime context (`--regime`)**

Annotates each observation with the prevailing macro regime *as-of* that date,
resolved from `analytics.macro_regime_per_date` (the B-059/B-096 view). The
as-of match carries the last in-force regime forward, so it works for
mixed-cadence series (a monthly print or a market-holiday day still resolves).

```console
$ genkei macro --series DGS10 --since 2026-06-28 --limit 4 --regime
DGS10 (4 rows, latest-vintage, horizon=macro:cross-sleeve:primary)
  ts           realtime_start   realtime_end                value  regime
  2026-07-01   2026-07-06       9999-12-31                 4.4900  risk_on
  2026-06-30   2026-07-02       9999-12-31                 4.4800  risk_on
  2026-06-29   2026-07-01       9999-12-31                 4.4400  risk_on
  2026-06-28   2026-06-30       9999-12-31                 4.3800  mixed
```

A carried-forward label is flagged inline as `risk_on (as of 2026-07-01)`; the
`--json` output adds `regime` and `regime_as_of` fields per row. Because the
regime is a plain view, `genkei query` can also join it onto any series
directly — `--regime` is the ergonomic shortcut for the common case.

**JSON (`--json`)**

```console
$ genkei macro --series DGS10 --limit 3 --json
[
  {
    "ts": "2026-06-23T19:00:00-05:00",
    "realtime_start": "2026-06-25",
    "realtime_end": "9999-12-31",
    "value": 4.41,
    "horizon_tag": "macro:cross-sleeve:primary"
  },
  {
    "ts": "2026-06-22T19:00:00-05:00",
    "realtime_start": "2026-06-24",
    "realtime_end": "9999-12-31",
    "value": 4.5,
    "horizon_tag": "macro:cross-sleeve:primary"
... (9 more lines)
```

## See also

[`macro-regime`](macro-regime.md)

---

_Page generated for B-047. Example output is a point-in-time capture; shape is stable, values are not. Regenerate when the command's flags change._
