# `genkei top-ranks`

Daily **top-N market-cap rank snapshot** and a **diff between two dates** (B-149).

Reads `coingecko.market_rank`, filled by the CoinGecko daily run from one
`/coins/markets?order=market_cap_desc&per_page=50` call (soft-failing: if it
fails, watchlist price ingest is unaffected and the day simply has no snapshot).
It exists to grade the "the top 15 is essentially chosen" thesis: with a rank
series kept forever, `--diff` shows which coins entered or left the top N.

The series is **forward-only**. The free CoinGecko tier has no historical rank
endpoint, so there is no snapshot before the first run after the migration. A
date with no snapshot is reported as missing, never guessed.

`--exclude-stables` drops the watchlist's `stablecoin_ids` and re-ranks the
remainder (`adj_rank`); the original CoinGecko rank stays in `rank`.

## Options

```text
--n               INTEGER   Top N coins to show / compare. [default: 15]
--date            TEXT      Snapshot date YYYY-MM-DD. [default: latest]
--exclude-stables           Drop watchlist stablecoin_ids and re-rank the rest.
--diff            TEXT TEXT Compare two snapshots (entries, exits, rank moves).
--json                      Emit machine-readable JSON.
--config          PATH      Watchlist path.
--help                      Show this message and exit.
```

`--date` and `--diff` are mutually exclusive. A hidden `--diff-from A --diff-to B`
pair is equivalent to `--diff A B` (the MCP tool uses it, since MCP passes one
value per flag).

## Examples

```console
$ genkei top-ranks --n 5 --exclude-stables
$ genkei top-ranks --diff 2026-10-29 2027-01-08 --n 15 --json
```

Diff output lists **Entered** (in the later top N, not the earlier), **Exited**
(the reverse, with the new rank, or `-` when outside the stored top 50) and
**Moved** (same membership, different place; positive change = moved up).

Also exposed as the `top_ranks` tool in `genkei-mcp`.
