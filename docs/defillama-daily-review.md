# DeFiLlama Daily Brief Review Standard

Use this checklist before treating a generated brief as decision support. The brief is a research signal only, not financial advice or a trade recommendation.

## Acceptance checks

1. **Scope is intact**
   - Focus universe is BTC, ETH + SOL, LINK, SUI in that priority order.
   - Non-target assets only appear as ecosystem context.
   - The brief remains DeFiLlama-only and does not rely on Twitter-only sentiment.

2. **Data freshness and completeness**
   - `Generated` timestamp and snapshot date are current for the intended daily review.
   - Chain TVL table has focused-chain records for Bitcoin, Ethereum, Solana, and Sui where DeFiLlama exposes them.
   - Money-flow section states stablecoin chain-data status as `available`, `partial`, or `unavailable`.
   - If stablecoin data is partial/unavailable, DCA and money-flow language stays caveated.

3. **Bitcoin ecosystem signal quality**
   - Bitcoin ecosystem section prioritizes Bitcoin-adjacent labels such as Lightning, Stacks, Rootstock/RSK, Babylon, Botanix, Merlin, Bitlayer, BOB, Citrea, and configured equivalents.
   - Generic centralized exchange or custody-like Bitcoin exposure is excluded from ecosystem signal or shown only in the caveated excluded-exposure section.

4. **DCA timing wording**
   - DCA section uses a signal label: `constructive`, `neutral`, or `caution`.
   - `constructive` never reads as an automatic buy instruction.
   - `caution` appears when momentum loss or acute outflow pressure exists.
   - The not-financial-advice line is present.

5. **Risk warnings**
   - Zombie-risk and momentum-loss rows are plausible against 7D TVL changes.
   - Acute outflow pressure is not ignored when 1D and 7D deterioration are both present.

## Triggers for tuning

- Repeated false positives from generic Bitcoin custody, CEX, or wrapped-BTC venue exposure.
- Stablecoin data is unavailable for more than three consecutive daily runs.
- DCA labels repeatedly disagree with the chain TVL table.
- DeFiLlama changes endpoint schemas or chain labels, producing empty focused sections.
- A target chain consistently appears under a new label not present in `config/defillama.sources.json`.

## When to ignore the brief

- DeFiLlama public APIs are stale, unavailable, or return partial data without clear caveats.
- Major macro, regulatory, exploit, unlock, or liquidation events dominate market structure.
- The generated brief is missing the Caveats section or the research-signal/not-financial-advice disclaimer.
- The Bitcoin ecosystem section is dominated by centralized venues or generic custody records.
- Unit validation fails in CI or local checks.

## Required validation commands

```bash
python3 -m unittest discover -s tests
python3 -m compileall scripts tests
```

For live smoke validation, run the full public-API pipeline and confirm generated artifacts remain ignored/untracked:

```bash
python3 scripts/collect_defillama.py
python3 scripts/normalize_defillama.py
python3 scripts/build_daily_report.py
git status --short --ignored data reports
```
