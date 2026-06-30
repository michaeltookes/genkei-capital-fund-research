# Render Network usage / fees (B-128)

The load-bearing data gap from the 2026-06-28 RENDER research decision
(`docs/research/decisions/2026-06-28-render-depin-compute-thesis.md`): the lake
had RENDER **price** but no **network-usage / fees / burn** metric, so the
DePIN compute-demand thesis was unmeasurable. This documents the source
survey (B-128 acceptance criterion #1) and the path taken.

## Survey outcome — DefiLlama BME fees (free, already-wired ingester)

Render's **Burn-and-Mint Equilibrium (BME)** fees are tracked by DefiLlama
under the slug **`render-network-bme`** (verified 2026-06-29):

| field | value |
|---|---|
| slug | `render-network-bme` |
| name | Render Network BME |
| category | **DePIN** |
| chain | **Solana** |
| endpoint | `https://api.llama.fi/summary/fees/render-network-bme?dataType=dailyFees` (and `dailyRevenue`) |
| history | ~392 daily points (≈ 2025-06-03 → present) |
| total 24h / 7d / 30d | ≈ $3.6k / $23k / $82k (at survey time) |

Both `dailyFees` and `dailyRevenue` return HTTP 200 with the standard
`totalDataChart` shape. **There is no TVL** — `/protocol/render-network-bme`
carries no TVL history (Render is a compute marketplace, not a DeFi-TVL
protocol). This is the same fees-only shape as `chainlink-requests` (B-083).

### Slug gotcha

The intuitive slugs **400**: `render`, `render-network`, `render-token`, `rndr`
all return *"Fees for X not found"*. The only working slug is
**`render-network-bme`** — found via the `/overview/fees` catalog. Pin it.

## Path taken — watchlist wiring (no new ingester)

Because DefiLlama already exposes the signal and the DefiLlama fees collector
iterates every `protocols:` slug, the entire integration is **one watchlist
entry** in `src/genkei/data/watchlists.yml` (`protocols:` → `render-network-bme`,
category DePIN, `coingecko_id: render-token`). The daily DefiLlama
collect/normalize then lands the series in `defillama.protocol_fees` keyed on
`(slug, ts)`; the `/protocol/` TVL fetch soft-fails per the existing
per-(slug, kind) soft-failure path. `coingecko_id: render-token` pairs the BME
fees with RENDER's price so `genkei revenue-divergence` joins cleanly.

Query the series:

```sql
SELECT ts::date, fees_usd, revenue_usd
FROM defillama.protocol_fees
WHERE slug = 'render-network-bme'
ORDER BY ts DESC LIMIT 30;
```

## Paths NOT taken (recorded for completeness)

- **On-chain Solana BME burn** (a direct RPC collector against the burn/mint
  program) — viable but unnecessary now that DefiLlama carries a clean,
  free, already-wired fees series. Reopen only if DefiLlama drops the slug or
  if frame-level usage (not just fee value) becomes the needed signal.
- **Render-published dashboards / RNDR explorer** — not needed for the same
  reason; would be a scraping path with more fragility than the DefiLlama API.
- **Paid DePIN data** (Messari / Token Terminal) — out of scope per the
  free-sources-first stance.

## Coverage limits

- **Fees/revenue value, not raw usage.** BME fees are a *dollar-denominated
  burn proxy*, not frames-rendered or job-count. A fee decline can reflect
  lower RENDER price as much as lower usage — read alongside `coingecko`
  price when interpreting (that's exactly what `revenue-divergence` is for).
- **History starts ≈ 2025-06**, not Render's full life — DefiLlama's BME
  tracking is the limiting window.
- The series is the **primary reassessment trigger** in the RENDER decision:
  fees/revenue *growing* → escalate the thesis; *flat/declining* → exit.
