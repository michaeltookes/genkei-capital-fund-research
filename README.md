# Genkei Capital Fund Research

DeFiLlama-only research scaffold for a small crypto market brief focused on **BTC, ETH,
SOL, LINK, and SUI**.

The MVP is designed for:

- trend insight from TVL and stablecoin-flow proxies;
- DCA timing support;
- early zombie-chain / momentum-loss warnings;
- avoiding Twitter-only sentiment as an input source.

## Scope

Only public DeFiLlama APIs are used. There are no paid API keys, accounts, or secrets.
Non-target assets are ignored unless they provide ecosystem context for the focused assets.

Bitcoin-adjacent chains/projects exposed by DeFiLlama are grouped under **Bitcoin
ecosystem** when they match configured labels such as Lightning, Stacks, Rootstock/RSK,
Babylon, Botanix, Merlin, Bitlayer, BOB, or equivalents in
`config/defillama.sources.json`.

## Repository layout

```text
config/defillama.sources.json      DeFiLlama source and asset scope config
scripts/collect_defillama.py       Collect raw public API snapshots
scripts/normalize_defillama.py     Normalize raw snapshots into focused daily JSON
scripts/build_daily_report.py      Build analyst-style Markdown daily brief
docs/defillama-mvp.md              Design notes and operating boundaries
data/raw/defillama/.gitkeep        Raw artifact directory anchor
data/normalized/defillama/.gitkeep Normalized artifact directory anchor
reports/daily/.gitkeep             Generated daily report directory anchor
tests/                             Offline unit tests
```

Generated raw JSON, normalized JSON, JSONL, and daily Markdown reports are ignored by git.
Only directory anchors are tracked.

## Run the pipeline

From the repository root:

```bash
python3 scripts/collect_defillama.py
python3 scripts/normalize_defillama.py
python3 scripts/build_daily_report.py
```

Outputs:

- raw snapshots: `data/raw/defillama/<timestamp>/`
- normalized daily dataset: `data/normalized/defillama/daily-YYYY-MM-DD.json`
- analyst brief: `reports/daily/defillama-daily-YYYY-MM-DD.md`

## Validate locally

```bash
python3 -m unittest discover -s tests
python3 -m compileall scripts tests
```

Unit tests are deterministic and do not hit the network.
