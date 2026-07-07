# `genkei zcash-usage`

Zcash shielded-pool adoption — the **shielded share of supply** and its trend.

The privacy-adoption signal the 2026-07-06 ZEC research decision flagged as
missing. Reads `zcash.shielded_pools` (the Zcash node's `valuePools`, landed
daily by `genkei.ingest.zcash_usage`). The default view is the per-day trend;
the *trend* is the signal — a rising shielded share means privacy is actually
being adopted, a flat one means the price move is narrative-only.

## Options

```text
--since       TEXT       Earliest snapshot_date (YYYY-MM-DD).
--until       TEXT       Latest snapshot_date (YYYY-MM-DD).
--limit       INTEGER    Max snapshots. [default: 60]
--by-pool                Latest per-pool breakdown instead of the trend.
--json                   Emit machine-readable JSON.
--help                   Show this message and exit.
```

## Example

```console
$ genkei zcash-usage
Zcash shielded-pool adoption | horizon=crypto:core:primary | 1 snapshot(s)
  date          shielded_%    shielded_M     total_M       block
  2026-07-07        26.25%         4.412      16.809   3,404,192

$ genkei zcash-usage --by-pool
Zcash value pools | 2026-07-07 | 5 pools
  pool                       ZEC  % supply  type
  transparent      12,347,955.04     73.5%  transparent
  orchard           3,766,939.29     22.4%  shielded
  sapling             620,043.04      3.7%  shielded
  lockbox              48,336.19      0.3%  non-shielded
  sprout               25,409.42      0.2%  shielded
```

> The series is **forward-only** (the source exposes only current chain state),
> so it builds from the first collection day; there's no deep history. `shielded`
> = sprout + sapling + orchard; transparent and the dev-fund lockbox are not
> private. See `docs/sources/zcash-usage.md`.

## See also

[`prices`](prices.md) · [`relative-strength`](relative-strength.md)

---

_Example output is a point-in-time capture; shape is stable, values are not._
