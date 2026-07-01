# <Experiment title>

**Date:** YYYY-MM-DD
**Author:** <name>
**Status:** in-progress | done | abandoned
**Horizon tag:** <e.g. crypto:core:years — which sleeve/horizon the result informs>

## Hypothesis

One or two sentences. What do you expect to find, and why? State it so the
result can prove you wrong.

## Data

- **Sources / tables:** which lake tables this reads (e.g. `coinbase.candles`,
  `defillama.protocol_fees`).
- **Snapshot:** see `manifest.json` — the pinned `meta.ingest_runs` ids this
  run consumed. (Written by `write_manifest` in the notebook.)
- **Window / universe:** date range, assets, filters.

## Method

How the analysis works — the transform, the metric, any model. Enough that
someone could reproduce it from this description plus the notebook.

## Results

What the data actually showed. Numbers, a chart reference, the headline. Note
whether the hypothesis held.

## Next steps

- What follow-up this opens (a new experiment, a signal emitter, a research
  decision, a backlog item).
- Any caveats / data-quality issues to resolve before trusting this further.
