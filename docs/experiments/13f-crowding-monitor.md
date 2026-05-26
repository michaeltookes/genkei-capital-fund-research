# 13F Crowding Monitor

**B-061.** Phase 5 experiment answering *"which watchlist names are the most-crowded by institutional positioning, and which just saw the biggest add/exit moves last quarter?"* The lake answer in one sentence: **once the 13F backfill lands data, `genkei crowding` ranks (cusip, period_of_report) by holder_count across the filers in `config/watchlists.yml::filers`, attaches the delta vs the most-recent prior period for each CUSIP, and surfaces new_entrants / exits — the activist-positioning analogue of B-060's insider buy-cluster signal.**

```text
$ genkei crowding --top 5
13F crowding (5 row(s), ≥2 holders, latest period 2025-03-31, by holder_count desc)
--------------------------------------------------------------------------------------------
  period       tkr    cusip          #  Δvs prior          $value           top holders
  2025-03-31   AAPL   037833100      4  +2 (2→4)           $42,000,000,000  Berkshire Hathaway Inc, ValueAct Capital Management LP, Pershing Square Capital, +1 more
  ...
```

```text
$ genkei crowding --by-delta --top 5      # biggest adds first
13F crowding (5 row(s), ≥2 holders, latest period 2025-03-31, by net_change desc)
```

## Backstory

B-061 was scoped to be picked up *driven by* B-080 (the 13F ingester it sits on top of), and that's exactly the order things shipped: B-080 closed the same week, and the next branch off main picks up the experiment that gave B-080's schema its concrete shape. The trigger was the ValueAct CRM cluster surfaced by `genkei insider-clusters` in 2025-12 (logged in `docs/research/decisions/2025-12-05-valueact-crm-buy-cluster.md`) — the obvious follow-up question was "and which *other* managers are alongside them?" That question wasn't answerable until 13F holdings were in the lake.

## What the experiment actually answers

Three questions, all flavors of "crowding" at the (period, CUSIP) grain:

1. **Static crowding.** *How many of our watchlist filers held this name as of quarter Q?*  
   `holder_count` per `(period_of_report, cusip)` aggregated across `sec.form13f_holdings`. Sorted desc, top-N rendered. Each row also surfaces total `value_usd` (B-080 stores dollars by convention — the ×1000 conversion is baked into the normalizer) and the top holders by position size.

2. **Delta crowding (the actionable signal).** *Did filer count jump or drop this quarter vs the prior one?*  
   For each row, we find the *positionally-prior* period for that CUSIP in the lake (not "the previous calendar quarter" — gaps happen when a filer dips below the $100M threshold and stops filing 13F for a year) and compute `net_change`, `new_entrants` (filers holding now but not last period), and `exits` (the inverse). A jump from 1 → 4 watchlist filers in one quarter is materially stronger than a stable 4 → 4, even though both have the same `holder_count`.

3. **Per-name history.** *How has crowding on CUSIP X evolved over the lake's history?*  
   `genkei crowding --cusip 037833100 --all-periods` (or `--ticker AAPL`) returns the full series. Useful for "is the crowding I see this quarter unusual, or just continued accumulation?"

## Why a CLI module, not a notebook

The backlog's literal phrasing was "notebook surfaces top crowded names per quarter." Per D-017 the project picked Claude Code over notebooks as the agent harness, so every Phase 5 experiment to date (B-058 / B-059 / B-060 / B-062 / B-065 / B-057 / B-090) has shipped as `experiments/<name>.py` + `cli/<name>.py` instead. This one follows the same shape. The CLI is the agent's query surface; a notebook would just wrap it.

## Module shape

* **Pure aggregator** (`src/genkei/experiments/crowding_monitor.py`): `compute_crowding(positions) -> list[CrowdingRow]`. Takes pre-loaded `Position` records, groups by `(period, cusip)`, computes per-period aggregates, and pairs each row with prior-period state. No DB access, no CLI knobs — all the corner cases (same filer reporting twice for one period via 13F-HR + 13F-HR/A, null `value_usd`, calendar gaps, first-observed period) live here and are pinned by unit tests.

* **Lake loader** (same module): `load_positions(*, since, until, filer_ciks, cusips)` pulls from `sec.form13f_holdings` joined to `sec.filers`. `available_periods()` returns the distinct `period_of_report` values present in the lake — used by the CLI to pick a sensible default period when the user passes no scope flag.

* **CLI** (`src/genkei/cli/crowding.py`): scope (`--ticker` / `--cusip`), period framing (`--period` / `--since`/`--until` / `--all-periods`, default = latest available), output knobs (`--min-holders`, `--top`, `--by-delta`, `--json`). The CLI is the only layer that knows about ticker→CUSIP resolution via the watchlist; the experiments module stays watchlist-agnostic so it stays testable on synthetic data.

## Edge cases pinned by tests

* **A filer who restates a quarter** (Form 13F-HR followed by 13F-HR/A for the same `period_of_report`) gets counted once — the highest accession_number wins, which sorts as the most-recent filing.
* **Positions with `value_usd = NULL`** still bump `holder_count` (the manager filed *something*, that's the signal) but contribute 0 to dollar-weighted aggregates.
* **First-observed period** for a CUSIP has `prior_holder_count = None`, `net_change = None`, empty `new_entrants` / `exits`. The CLI renders this as `new` instead of a +/- delta.
* **Calendar gaps** — if a filer skipped 2024-Q2 and Q3 and returned in Q4, the Q4 delta compares against Q1 (the positional-prior), not against an imagined Q3 with 0 holders. Otherwise every gap would manifest as a fake exit-then-re-add.
* **Exits-only periods** — the detector returns every (period, cusip) aggregate regardless of `holder_count`, so a quarter where AAPL dropped from 4 filers to 1 still surfaces (with `net_change = -3`) when the user runs `--min-holders 1`. `--min-holders` is a presentation filter, not an aggregator gate.

## Why `--by-delta` matters

The default sort (latest period, most-crowded first) surfaces the *consensus* names — useful context, but mostly information you already had ("Berkshire holds Apple"). The actionable signal lives in the *change*: a name going from 1 → 4 watchlist filers in one quarter has multiple high-conviction funds simultaneously building positions. `--by-delta` sorts by `net_change` desc to surface that pattern directly. Pairs cleanly with `genkei insider-clusters` — if a name shows up on both (insiders adding + 13F managers adding), that's the highest-confidence stack the lake can produce today.

## What B-061 deliberately does NOT cover

* **Position-size deltas** — `net_change` is about *who* holds; this experiment doesn't measure whether existing holders increased or trimmed positions inside a window. Adding a `shares_delta` / `value_delta` view per (filer, cusip, period) is a small v2 extension.
* **Concentration / Herfindahl** — 10 filers each at 1% allocation vs 1 filer at 10% is a different signal than raw holder_count. A `concentration_score` column is a one-PR follow-up if/when it becomes useful.
* **Sector / theme rollups** — "are mega-cap tech names becoming more or less crowded as a group?" requires sector tagging on CUSIP. Out of scope until the watchlist gains a `sector` taxonomy that 13F filers can be joined against.
* **Live homelab evidence** — the 13F backfill hasn't been run on the homelab yet (one command — `python -m genkei.ingest.sec_form13f --backfill` from the homelab — but separate session). Tests prove correctness against synthetic data; first real-data run is the user's call.

## Backlog implications

* **B-061 closes.** The experiment ships.
* **B-080 unblocks B-061 ✓** — already noted in `docs/resolved.md` B-080 entry.
* **CUSIP coverage** — 19 of 28 watchlist equities now carry `cusip:` fields; the remaining 9 (SMCI, PLTR, DOCN, SOFI, HOOD, MSTR, BMNR, CLSK, SUIG) can be filled in as the user needs them. The CLI surfaces a friendly error pointing at the watchlist when an unmapped ticker is requested.

## References

* `src/genkei/experiments/crowding_monitor.py` — pure aggregator + lake loader.
* `src/genkei/cli/crowding.py` — Typer command.
* `tests/experiments/test_crowding_monitor.py` — 13 unit tests on the pure layer.
* `tests/cli/test_crowding.py` — 18 CLI tests covering arg validation, format helpers, mocked-DB end-to-end runs.
* `docs/resolved.md` R-080 — the 13F ingest this experiment sits on.
* `docs/research/decisions/2025-12-05-valueact-crm-buy-cluster.md` — the decision that motivated this work.
