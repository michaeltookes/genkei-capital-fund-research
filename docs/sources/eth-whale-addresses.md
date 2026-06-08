# ETH whale address curation methodology (B-106)

**Source of truth:** `src/genkei/data/watchlists.yml` → `eth_whale_addresses:` section. Edit there; this doc explains *how* to choose what goes in.

**Context:** B-106 ships a daily-snapshot whale-flow tracker that pulls per-address ETH balance + 24h net flow from Etherscan v2 for a curated list of addresses. The list is the load-bearing decision — get it wrong and the resulting `onchain.eth_whale_flows` table either misses the real signal (whale we should track isn't in the list) or fakes one (random address mislabeled as a whale). This doc records the curation principles so the list stays honest as it grows.

## What counts as a "whale address" for this tracker

The B-106 spec frames the signal as "are large long-term ETH wallets net-selling?" — the user's "OG sellers" framing on ETH. A useful whale address for this tracker satisfies all four:

1. **Public provenance.** The address must be identifiable from at least one of: Etherscan's canonical "Name Tag" page, the entity's own public disclosure (e.g. Ethereum Foundation Treasury Report, Lido governance docs), or a publicly-published community analysis (Lookonchain, Nansen Public, similar). Privately-sourced "this is whale X" claims don't go in v1.
2. **Stable identity.** The address shouldn't be a one-off router that the entity rotates away from. Cold-storage and contract addresses qualify; hot wallets that change weekly don't.
3. **Material ETH balance.** Loose floor: ≥1,000 ETH at the time of addition. Smaller balances clutter the signal without adding it. Document the balance at add-time in the watchlist `notes` field so a future audit can see whether the address has materially drained / grown.
4. **Behavior that maps to the signal.** A wallet that only ever receives airdrops isn't a "seller" or a "holder" — it's just a destination. A wallet that ETH-trades regularly is the useful signal even if it isn't the largest.

## Category labels

Every address gets exactly one `category` value. The four are tuned for the headline aggregate view (`onchain.eth_whale_flows_aggregate`) which sums per-category per day:

- **`exchange`** — Centralized exchange cold wallets (Binance, Coinbase, Kraken, OKX, Bybit, Bitfinex, Gemini, HTX, Crypto.com). The headline interpretation is opposite the intuitive one: a CEX cold wallet receiving ETH means *users are sending TO the exchange* (i.e. preparing to sell), NOT that the exchange itself is buying. Aggregated CEX inflow over time is a meaningful sell-pressure signal — but read the SIGN carefully.
- **`custodian`** — Non-CEX staking / custody infrastructure: Lido stETH contract, Beacon Deposit Contract (the Eth2 staking entry point), Rocketpool storage, EigenLayer strategy managers. Inflow here is the OPPOSITE of `exchange` — users staking long-term is bullish, withdrawals (in the post-Shapella era) are bearish.
- **`foundation`** — Protocol-treasury addresses: Ethereum Foundation hot/cold, Vitalik's publicly-self-identified primary wallet. Outflow here is a meaningful signal (the EF historically sells tranches during bull runs); inflow is rare and usually grant-recoupment.
- **`whale`** — Individual large holders identified via public community analysis. Smallest, noisiest category in v1 because address-identification requires trusting Lookonchain-style attributions. Default attitude: include only when the provenance trail is documented in the `notes` field.

## v1 seed list — provenance for every entry

The watchlist seed pulls from Etherscan-canonical name tags + entity-published disclosures only. Every entry has `notes` recording where the label came from and the ETH balance at addition time (so future reconciliations can spot a drained address). v1 covers ~25 addresses across all four categories — small enough to manually curate but large enough that the aggregate view has signal beyond any single entity's idiosyncrasy.

**foundation (2):**

- `0xde0B295669a9FD93d5F28D9Ec85E40f4cb697BAe` — Ethereum Foundation hot wallet (Etherscan canonical label). Historical role: the EF has sold tranches of ETH from this address during bull runs (notably 2021, 2024); outflow is a meaningful supply-side signal.
- `0xab5801a7d398351b8be11c439e05c5b3259aec9b` — Vitalik Buterin primary (self-identified on Twitter many times). Smaller than EF treasury but symbolically watched.

**custodian (3):**

- `0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84` — Lido stETH token contract (Etherscan label). Holds the modest "buffer" ETH; the bulk of staked ETH sits in the Beacon Deposit Contract.
- `0x00000000219ab540356cBB839Cbe05303d7705Fa` — Eth2 Beacon Deposit Contract (Etherscan label). The single largest ETH holder on the network — all newly-staked ETH flows here.
- `0xDc24316b9AE028F1497c275EB9192a3Ea0f67022` — Lido stETH/ETH Curve Pool (Etherscan label). Material liquidity for the un-stake exit path.

**exchange (~15-18):**

All Etherscan-canonical exchange labels at addition time. Inflow is sell-pressure direction.

- `0xF977814e90dA44bFA03b6295A0616a897441aceC` — Binance cold wallet 1 (~556K ETH)
- `0x28C6c06298d514Db089934071355E5743bf21d60` — Binance 14
- `0x21a31Ee1afC51d94C2eFcCAa2092aD1028285549` — Binance 7
- `0xDFd5293D8e347dFe59E90eFd55b2956a1343963d` — Binance 16
- `0x71660c4005BA85c37ccec55d0C4493E66Fe775d3` — Coinbase 1
- `0x503828976D22510aad0201ac7EC88293211D23Da` — Coinbase 2
- `0xddfAbCdc4D8FfC6d5beaf154f18B778f892A0740` — Coinbase 4
- `0xA9D1e08C7793af67e9d92fe308d5697FB81d3E43` — Coinbase 10
- `0x2910543Af39abA0Cd09dBb2D50200b3E800A63D2` — Kraken 1
- `0xE853c56864A2ebe4576a807D26Fdc4A0adA51919` — Kraken 4
- `0x0A869d79a7052C7f1b55a8EbAbbEa3420F0D1E13` — Kraken 5
- `0x6cC5F688a315f3dC28A7781717a9A798a59fDA7b` — OKX cold
- `0xf89d7b9c864f589bbF53a82105107622B35EaA40` — Bybit cold
- `0x1151314c646Ce4E0eFD76d1aF4760aE66a9Fe30F` — Bitfinex
- `0x07Ee55aA48Bb72DcC6E9D78256648910De513eca` — Gemini 1

**whale (0 in v1):**

v1 ships with zero direct-whale entries because the provenance bar (publicly-documented attribution beyond "Lookonchain said so") is high. The aggregate signal from the 20 exchange / custodian / foundation entries is strong enough to ship; adding whale-category addresses is the natural v2 expansion if a research session names a specific holder we want to track.

## Hard limits the data exposes (call them out loudly in any reading)

These limits are load-bearing because callers will misread the data without them:

1. **The address list is necessarily incomplete.** True whales obfuscate via fresh wallets, multi-sig rotations, and chain-bridging. A whale that doesn't appear in our list isn't visible — and the largest whales are typically the ones working hardest to stay hidden. Treat the aggregate as "lower bound on the directional signal", not "this is the full picture".
2. **Exchange cold-wallet flow reverses the intuitive sign.** CEX cold wallets aggregate many users' deposits — when ETH flows IN, that's users preparing to sell, not the exchange buying. The headline aggregate query should report category-net flow per day, not bundle exchange + foundation into one "whales" number.
3. **Etherscan rate limits cap address-list size.** Free tier is 3 req/s — at one balance + one txlist call per address per day, we have headroom up to ~500 addresses per daily run. Comfortable for v1's 20 but will need pagination + throttling if the list grows beyond a few hundred.
4. **Single-day reads are noisy.** A transfer at 23:59:59 UTC vs 00:00:01 UTC lands in different daily rows. The collector filters by exact transaction timestamp after resolving boundary blocks, but the signal still stabilizes when you aggregate over 7d+ windows.
5. **Historical balances are intentionally NULL.** Etherscan's free balance endpoint returns the current balance, not point-in-time historical balances. Backfills therefore populate `net_flow_eth_24h` / `net_flow_usd_24h` from historical txlist windows but leave `balance_eth` / `balance_usd_at_snapshot` empty for historical dates rather than writing today's balance into old rows.

## How to add an address

1. Confirm it satisfies all four criteria above.
2. Add an entry under `eth_whale_addresses:` in `src/genkei/data/watchlists.yml`:
   ```yaml
   - address: 0x...
     label: Human-readable name
     category: exchange | custodian | foundation | whale
     notes: |
       Provenance: source URL or disclosure citation.
       Balance at add-time: N,NNN ETH.
       (Anything else load-bearing about how to read this address.)
   ```
3. Push the change in a small dedicated commit so future audits can see when each entry landed.
4. The next daily collector run picks it up automatically; the schema's `(address, ts)` PK means a re-add is idempotent.

## How to remove an address

Hard removal (delete from the watchlist) is OK when the address has drained materially OR the provenance turns out to be wrong. Soft removal — "leave it in but don't include in the aggregate" — isn't supported in the v1 schema. If we end up needing that, the right v2 move is a `disabled: true` flag on the watchlist entry, not a complex `ignore_after_date` mechanism.
