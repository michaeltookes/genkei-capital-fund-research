"""On-chain staking-pool event ingester (B-082 + B-086).

Reads Staked / Unstaked / UnbondingPeriodStarted log events from a
configured staking-pool contract via Etherscan's V2 logs API and lands
one row per event in ``onchain.staking_events``. Currently covers the
Chainlink v0.2 staking system on Ethereum mainnet — both halves of it:

  - ``chainlink-v02``           — CommunityStakingPool
                                  (``0xBc10f2E862ED4502144c7d632a3459F49DFCDB5e``)
  - ``chainlink-v02-operator``  — OperatorStakingPool
                                  (``0xa1d76a7ca72128541e9fcacafbda3a92ef94fdc5``)

Both contracts share the v0.2 codebase and emit the same Staked /
Unstaked / UnbondingPeriodStarted event signatures (verified via the
Etherscan topic probe in B-086), so DEFAULT_POOLS reuses the existing
EVENT_TOPIC_* constants for both — no per-pool topic overrides needed.

The schema (migration ``5d3e8b9c1a02``) is deliberately generic so
adding Lido / RocketPool / EigenLayer in the future is a config
change plus a contract-address constant, not a new schema.

Not covered (filed as B-116 follow-up): the v0.1 legacy ``Staking``
contract (``0x3feB1e09b4bb0E7f0387CeE092a52e85797ab889``). It still
holds 0.46M LINK during unwind as of 2026-06-07 but emits DIFFERENT
event signatures than v0.2 — wiring it up requires extending
PoolConfig with per-pool event-topic overrides and a separate parse
path. The two v0.2 pools together reconcile within ~3% of DefiLlama's
chainlink-staking TVL so v0.1 is not load-bearing for v1.

Signal-interpretation notes (learned from the B-082 + B-086 backfills):

  **Cap-and-intent dynamics.** The v0.2 community pool runs at a
  **capped capacity** (~40.9M LINK as of 2026-06-07, not the ~6.5M
  observed in the early ramp during the B-082 design window).
  Once the cap fills, every Unstaked event is immediately matched by
  a queued Staked event of the same amount — monthly net flow is
  structurally **zero** in steady state. So "net flow per month" is
  NOT a useful demand signal for the capped pool; the framing in
  B-082's original acceptance criteria was wrong on that front.

  The actual demand signal is the **UnbondingPeriodStarted** count
  (stakers signaling intent to exit, waiting ~28d before the actual
  Unstaked event lands). That count has trended from ~150/month in
  2024 to ~400/month in 2025-2026 — a ~2.5x increase in stakers
  losing patience, which is real on-chain conviction data of the
  kind the LINK research session was looking for.

  **Operator pool runs differently.** The OperatorStakingPool has
  much lower turnover (104 Staked + 16 Unstaked events vs 17,488 +
  3,141 on the community pool) because it tracks bonded node-
  operator stake rather than retail flow. Net-stake-delta is more
  meaningful there — operator bonding/unbonding is an institutional
  signal in its own right.

  **TVL reconciliation methodology.** SUM(staked - unstaked) per
  protocol_slug × current LINK price should land within ~10% of
  DefiLlama's chainlink-staking TVL. Verified at 2026-06-07:
  community 40,875,000 LINK + operator 1,731,903 LINK = 42,606,903
  LINK; at $7.68 = $327M vs DefiLlama's $338M (~3% gap, entirely
  explained by LINK price drift across DB snapshots + the v0.1
  contract's 457K LINK that B-116 will add). Run the reconciliation
  any time a new pool ships:

      SELECT protocol_slug,
             SUM(CASE WHEN event_type='staked'   THEN amount_token
                      WHEN event_type='unstaked' THEN -amount_token END) AS net_link
      FROM onchain.staking_events
      WHERE protocol_slug LIKE 'chainlink-%'
        AND event_type IN ('staked','unstaked')
      GROUP BY protocol_slug;

  Queries computing demand signal should aggregate by event_type +
  protocol_slug; aggregating the community pool's staked-minus-
  unstaked alone is structurally meaningless on capped pools.

Configuration:
  - ``ETHERSCAN_API_KEY`` — Etherscan V2 strictly requires a key (no
    keyless mode). When unset, the collector gracefully **skips**: it
    logs a warning, records a run with status=success + 0 rows, and
    returns. Same shape as the CoinGecko keyless-fallback (D-020) —
    code ships, but data only flows once the key is configured.
    Register a free key at https://etherscan.io/myapikey.
  - Rate limit: free Etherscan tier allows 5 req/s. We use 4/s to
    leave headroom for retries.
  - Pagination: Etherscan returns ≤1000 logs per call. The collector
    walks block ranges in 50k-block chunks (~7 days of Ethereum) and
    pages within a chunk if needed. Resume-from-last-block on
    incremental runs.

Modes:
  - **incremental** (default) — fetch logs from the last
    ingested block in ``onchain.staking_events`` (per
    ``protocol_slug``) up to the current head. Fast.
  - **backfill** (``--backfill``) — start at the contract's
    deployment block and walk forward. Multi-thousand block range;
    one-shot operation expected to take ~10-30 min depending on
    event volume.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

from genkei.common import db
from genkei.common.http import HttpClient, RateLimit

# Silence httpx's default INFO-level "HTTP Request: GET <full-url>" logging —
# Etherscan auth lives in URL params, and INFO-level URL logging leaks the
# API key to stdout / log aggregators / process output. WARNING still
# surfaces real problems (4xx, 5xx, network errors); only routine
# request-completion lines are suppressed. Set at module load so anyone
# importing this collector is protected.
logging.getLogger("httpx").setLevel(logging.WARNING)

SOURCE_NAME = "onchain_staking"
COLLECT_ENDPOINT_LABEL = "collect"
ETHERSCAN_V2_URL = "https://api.etherscan.io/v2/api"
ETHEREUM_CHAIN_ID = 1

# Chainlink v0.2 staking pools — the live system tracked at
# https://staking.chain.link. Both contracts deployed at block 18572190
# (2023-11-14); we use 18638000 as a safe lower bound for backfill since
# events only start landing a few weeks after deployment when the cap-and-
# intent ramp finished. Both pools emit the same Staked / Unstaked /
# UnbondingPeriodStarted event signatures (verified live via Etherscan
# topic-probe 2026-06-07), so DEFAULT_POOLS reuses the existing event
# constants for both — no parser changes needed for the operator pool.
CHAINLINK_V02_POOL_ADDRESS = "0xBc10f2E862ED4502144c7d632a3459F49DFCDB5e"
CHAINLINK_V02_OPERATOR_POOL_ADDRESS = "0xa1d76a7ca72128541e9fcacafbda3a92ef94fdc5"
CHAINLINK_V02_DEPLOYMENT_BLOCK = 18638000  # ~Nov 2023, safe lower bound
LINK_DECIMALS = 18  # standard ERC-20

# Chainlink v0.1 Staking contract — legacy pool, still holds 0.46M LINK
# during unwind as of 2026-06-07. NOT in DEFAULT_POOLS because the v0.1
# contract emits DIFFERENT event signatures than v0.2 (the v0.2 Staked
# topic returns 0 results on the v0.1 contract — verified live 2026-06-07).
# Wiring v0.1 up requires extending PoolConfig with per-pool event-topic
# overrides and a separate parse path. Filed as a follow-up; the operator
# + community pools together already reconcile within 3% of DefiLlama's
# chainlink-staking TVL so v0.1 is not load-bearing for v1.
CHAINLINK_V01_POOL_ADDRESS = "0x3feB1e09b4bb0E7f0387CeE092a52e85797ab889"

# Event signatures (keccak256 of the canonical event sig) for the
# Chainlink v0.2 Community Staking Pool. Verified live 2026-05-17 by
# fetching the contract's ABI from Etherscan and computing keccak256
# of each event's canonical signature — the first attempt at these
# constants was wrong and the parser silently dropped every event.
# All three events index the staker as topic[1].
#   Staked(address,uint256,uint256,uint256) — data = (amount, newStake, newTotalPrincipal)
#   Unstaked(address,uint256,uint256,uint256) — same data shape
#   UnbondingPeriodStarted(address) — no data; intent signal preceding Unstaked by ~28d
EVENT_TOPIC_STAKED = "0xb4caaf29adda3eefee3ad552a8e85058589bf834c7466cae4ee58787f70589ed"
EVENT_TOPIC_UNSTAKED = "0x204fccf0d92ed8d48f204adb39b2e81e92bad0dedb93f5716ca9478cfb57de00"
EVENT_TOPIC_UNBONDING_STARTED = "0x5b9cd1c6f24b416d2354b7b7ad07d92bc1c662a403180e84fac2782414a5f4ed"

ETHERSCAN_API_KEY_ENV = "ETHERSCAN_API_KEY"

# Chunk size for getLogs queries. Etherscan returns <=1000 logs per
# page; 50k blocks is roughly 7 days on Ethereum, with pagination
# within each block window when activity exceeds one page.
BLOCK_CHUNK_SIZE = 50_000
LOG_PAGE_SIZE = 1000

# Etherscan free tier is 3 req/s — using 2/s leaves headroom for the
# eth_blockNumber probe + concurrent retries. Earlier 4/s setting hit
# "Max calls per sec rate limit reached (3/sec)" repeatedly and
# dropped chunks in the first backfill attempt.
DEFAULT_RATE_LIMIT = RateLimit.per_second(2)
INSERT_SQL = (
    "INSERT INTO onchain.staking_events ("
    "tx_hash, log_index, chain, protocol_slug, contract_address, "
    "block_number, block_timestamp, event_type, staker_address, "
    "amount_token, amount_usd, source_endpoint, fetched_at, ingest_run_id"
    ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (tx_hash, log_index, block_timestamp) DO NOTHING"
)
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PoolConfig:
    """A staking pool we ingest events for."""

    chain: str  # e.g. "ethereum"
    chain_id: int  # Etherscan chain id (1 = Ethereum mainnet)
    protocol_slug: str  # canonical key in onchain.staking_events
    contract_address: str
    deployment_block: int  # for --backfill lower bound
    token_decimals: int


CHAINLINK_V02_POOL = PoolConfig(
    chain="ethereum",
    chain_id=ETHEREUM_CHAIN_ID,
    protocol_slug="chainlink-v02",
    contract_address=CHAINLINK_V02_POOL_ADDRESS,
    deployment_block=CHAINLINK_V02_DEPLOYMENT_BLOCK,
    token_decimals=LINK_DECIMALS,
)
# Operator-only counterpart to the community pool. Same v0.2 codebase,
# same event signatures, same deployment block. Holds the node-operator
# bonded stake — much smaller than the community pool (1.7M LINK vs 40.9M
# on 2026-06-07) but represents a distinct staker cohort whose flow
# behavior is operationally different from retail community stakers.
CHAINLINK_V02_OPERATOR_POOL = PoolConfig(
    chain="ethereum",
    chain_id=ETHEREUM_CHAIN_ID,
    protocol_slug="chainlink-v02-operator",
    contract_address=CHAINLINK_V02_OPERATOR_POOL_ADDRESS,
    deployment_block=CHAINLINK_V02_DEPLOYMENT_BLOCK,
    token_decimals=LINK_DECIMALS,
)
DEFAULT_POOLS: list[PoolConfig] = [CHAINLINK_V02_POOL, CHAINLINK_V02_OPERATOR_POOL]


def resolve_api_key() -> str | None:
    """Return the Etherscan API key from env, or ``None`` if unset."""
    key = os.environ.get(ETHERSCAN_API_KEY_ENV, "").strip()
    return key or None


# ---------------------------------------------------------------------------
# Etherscan V2 client (thin wrapper — keeps the API-shape logic in one place)
# ---------------------------------------------------------------------------


def fetch_logs(
    http: HttpClient,
    *,
    api_key: str,
    pool: PoolConfig,
    from_block: int,
    to_block: int,
) -> list[dict[str, Any]]:
    """Fetch Etherscan logs for one contract over a block range.

    Returns the combined ``result`` pages (possibly empty). Raises on
    transport/API errors; "No records found" is the benign empty case.
    """
    base_params = {
        "chainid": pool.chain_id,
        "module": "logs",
        "action": "getLogs",
        "address": pool.contract_address,
        "fromBlock": from_block,
        "toBlock": to_block,
        "apikey": api_key,
    }
    logs: list[dict[str, Any]] = []
    page = 1
    while True:
        params = {
            **base_params,
            "page": page,
            "offset": LOG_PAGE_SIZE,
        }
        url = ETHERSCAN_V2_URL + "?" + "&".join(f"{k}={v}" for k, v in params.items())
        payload = http.get_json(url)
        if not isinstance(payload, dict):
            raise RuntimeError(f"Etherscan getLogs malformed response: {payload!r}")
        status = payload.get("status")
        result = payload.get("result")
        # Etherscan returns status="1" + list on success, status="0" +
        # string message on failure / empty. "No records found" is a
        # benign empty.
        if isinstance(result, list):
            logs.extend(result)
            if len(result) < LOG_PAGE_SIZE:
                return logs
            page += 1
            continue
        if status == "0" and isinstance(result, str):
            if result == "No records found":
                return logs
            raise RuntimeError(f"Etherscan API error: {result}")
        raise RuntimeError(
            "Etherscan getLogs malformed response: "
            f"payload={payload!r}, status={status!r}, result={result!r}"
        )


# ---------------------------------------------------------------------------
# Log → row decoding
# ---------------------------------------------------------------------------


def hex_to_int(value: str) -> int:
    """Decode an Etherscan hex string ('0x1234' / '0x') to int."""
    if not isinstance(value, str) or not value.startswith("0x"):
        return 0
    payload = value[2:]
    if not payload:
        return 0
    return int(payload, 16)


def hex_to_address(value: str) -> str:
    """Decode the last 20 bytes of a 32-byte topic into a 0x-address."""
    if not isinstance(value, str):
        return ""
    cleaned = value.lower().removeprefix("0x")
    # Topics are 32-byte (64-hex) padded. Address is the last 40 hex chars.
    if len(cleaned) < 40:
        return ""
    return "0x" + cleaned[-40:]


def decode_amount_token(data_hex: str, decimals: int) -> Decimal:
    """Decode the first 32-byte word of the log `data` field as a uint256
    token amount and scale by ``10**decimals``.
    """
    raw = hex_to_int(data_hex[:66] if len(data_hex) >= 66 else data_hex)
    return Decimal(raw) / (Decimal(10) ** decimals)


def event_type_for_topic(topic0: str) -> str | None:
    """Map a topic0 hash to a human event name we know how to decode.

    UnbondingPeriodStarted is captured as a separate event_type because
    it's a meaningful intent signal (staker signaling unbond ~28d before
    the actual Unstaked event lands). Amount is 0 for those rows since
    the event carries no amount data; queries that compute net flow
    should aggregate explicitly by event_type to avoid double-counting.
    """
    t = topic0.lower()
    if t == EVENT_TOPIC_STAKED.lower():
        return "staked"
    if t == EVENT_TOPIC_UNSTAKED.lower():
        return "unstaked"
    if t == EVENT_TOPIC_UNBONDING_STARTED.lower():
        return "unbonding_started"
    return None


def parse_log(
    log: dict[str, Any],
    *,
    pool: PoolConfig,
    source_endpoint: str,
    ingest_run_id: int,
    fetched_at: datetime,
) -> dict[str, Any] | None:
    """Decode one Etherscan log dict into an onchain.staking_events row.

    Returns ``None`` if the log isn't a Staked/Unstaked event (other
    events from the same contract — e.g. RewardsAdded — are silently
    skipped). Returns ``None`` on malformed shape too; we'd rather drop
    one event than fail the whole batch.
    """
    topics = log.get("topics")
    if not isinstance(topics, list) or not topics:
        return None
    event_type = event_type_for_topic(str(topics[0]))
    if event_type is None:
        return None
    # Most pool events index the staker as topic[1].
    if len(topics) < 2:
        return None
    staker = hex_to_address(str(topics[1]))
    if not staker:
        return None
    data_hex = log.get("data")
    if not isinstance(data_hex, str):
        return None
    # UnbondingPeriodStarted has no data payload — emit amount=0 so the
    # row still records the intent signal. Staked / Unstaked carry
    # (amount, newStake, newTotalPrincipal); decode_amount_token reads
    # just the first 32-byte word (amount).
    if event_type == "unbonding_started":
        amount = Decimal(0)
    else:
        amount = decode_amount_token(data_hex, pool.token_decimals)
    block_number_raw = log.get("blockNumber")
    timestamp_raw = log.get("timeStamp")
    tx_hash = log.get("transactionHash")
    log_index_raw = log.get("logIndex")
    required = (block_number_raw, timestamp_raw, tx_hash, log_index_raw)
    if not all(isinstance(v, str) for v in required):
        return None
    block_number = hex_to_int(block_number_raw)  # type: ignore[arg-type]
    timestamp_int = hex_to_int(timestamp_raw)  # type: ignore[arg-type]
    log_index = hex_to_int(log_index_raw)  # type: ignore[arg-type]
    block_ts = datetime.fromtimestamp(timestamp_int, tz=timezone.utc)
    return {
        "tx_hash": tx_hash,
        "log_index": log_index,
        "chain": pool.chain,
        "protocol_slug": pool.protocol_slug,
        "contract_address": pool.contract_address.lower(),
        "block_number": block_number,
        "block_timestamp": block_ts,
        "event_type": event_type,
        "staker_address": staker,
        "amount_token": amount,
        "amount_usd": None,  # backfilled later via a price-join, not at ingest time
        "source_endpoint": source_endpoint,
        "fetched_at": fetched_at,
        "ingest_run_id": ingest_run_id,
    }


# ---------------------------------------------------------------------------
# Resume + chunked range walk
# ---------------------------------------------------------------------------


def latest_block_for_pool(pool: PoolConfig) -> int | None:
    """Return the highest block_number already ingested for this pool, or None."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT max(block_number) FROM onchain.staking_events "
            "WHERE protocol_slug = %s AND contract_address = %s",
            [pool.protocol_slug, pool.contract_address.lower()],
        )
        row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else None


def fetch_current_head_block(http: HttpClient, *, api_key: str) -> int:
    """Etherscan V2 eth_blockNumber proxy — current Ethereum head block."""
    url = (
        f"{ETHERSCAN_V2_URL}?chainid={ETHEREUM_CHAIN_ID}&module=proxy"
        f"&action=eth_blockNumber&apikey={api_key}"
    )
    payload = http.get_json(url)
    if not isinstance(payload, dict):
        raise RuntimeError("Etherscan eth_blockNumber returned non-dict")
    result = payload.get("result")
    if not isinstance(result, str):
        raise RuntimeError(f"Etherscan eth_blockNumber unexpected: {payload}")
    return hex_to_int(result)


def iter_block_chunks(
    *, from_block: int, to_block: int, chunk_size: int = BLOCK_CHUNK_SIZE
) -> list[tuple[int, int]]:
    """Split [from_block, to_block] inclusive into chunk-sized windows."""
    chunks: list[tuple[int, int]] = []
    cursor = from_block
    while cursor <= to_block:
        end = min(cursor + chunk_size - 1, to_block)
        chunks.append((cursor, end))
        cursor = end + 1
    return chunks


def _insert_rows(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    payload_values = [
        (
            row["tx_hash"],
            row["log_index"],
            row["chain"],
            row["protocol_slug"],
            row["contract_address"],
            row["block_number"],
            row["block_timestamp"],
            row["event_type"],
            row["staker_address"],
            row["amount_token"],
            row["amount_usd"],
            row["source_endpoint"],
            row["fetched_at"],
            row["ingest_run_id"],
        )
        for row in rows
    ]
    with db.connection() as conn, conn.cursor() as cur:
        cur.executemany(INSERT_SQL, payload_values)
        return max(cur.rowcount or 0, 0)


# ---------------------------------------------------------------------------
# Public entry: collect()
# ---------------------------------------------------------------------------


def collect(
    *,
    http: HttpClient | None = None,
    api_key: str | None = None,
    backfill: bool = False,
    pools: list[PoolConfig] | None = None,
) -> int:
    """Run the on-chain staking collector once. Returns meta.ingest_runs id.

    Graceful skip when no API key — records a successful run with 0
    rows and a clear metadata note. Same pattern as the CoinGecko
    keyless fallback (D-020): code ships fully, data flows when the
    key is configured.
    """
    resolved_key = api_key if api_key is not None else resolve_api_key()
    pools = pools if pools is not None else DEFAULT_POOLS

    owns_http = http is None
    if http is None:
        http = HttpClient(SOURCE_NAME, rate_limit=DEFAULT_RATE_LIMIT)

    try:
        with db.ingest_run(
            SOURCE_NAME,
            endpoint=COLLECT_ENDPOINT_LABEL,
            metadata={
                "mode": "backfill" if backfill else "incremental",
                "pool_count": len(pools),
                "has_api_key": resolved_key is not None,
            },
        ) as run:
            if resolved_key is None:
                LOGGER.warning(
                    "%s not set — skipping on-chain staking collect. "
                    "Register a free key at https://etherscan.io/myapikey "
                    "and set %s in .env / GH Actions secrets to enable.",
                    ETHERSCAN_API_KEY_ENV,
                    ETHERSCAN_API_KEY_ENV,
                )
                run.add_rows(0)
                return run.id

            total_written = 0
            for pool in pools:
                head_block = fetch_current_head_block(http, api_key=resolved_key)
                if backfill:
                    start_block = pool.deployment_block
                else:
                    last_seen = latest_block_for_pool(pool)
                    start_block = (last_seen + 1) if last_seen else pool.deployment_block
                if start_block > head_block:
                    LOGGER.info(
                        "%s already current (last=%s, head=%s)",
                        pool.protocol_slug,
                        start_block - 1,
                        head_block,
                    )
                    continue

                for chunk_from, chunk_to in iter_block_chunks(
                    from_block=start_block, to_block=head_block
                ):
                    rows: list[dict[str, Any]] = []
                    try:
                        logs = fetch_logs(
                            http,
                            api_key=resolved_key,
                            pool=pool,
                            from_block=chunk_from,
                            to_block=chunk_to,
                        )
                        fetched_at = datetime.now(timezone.utc)
                    except (httpx.TimeoutException, httpx.NetworkError, RuntimeError) as exc:
                        LOGGER.warning(
                            "%s logs fetch failed for blocks %s-%s: %s",
                            pool.protocol_slug,
                            chunk_from,
                            chunk_to,
                            exc,
                        )
                        raise
                    for log in logs:
                        row = parse_log(
                            log,
                            pool=pool,
                            source_endpoint=ETHERSCAN_V2_URL,
                            ingest_run_id=run.id,
                            fetched_at=fetched_at,
                        )
                        if row is not None:
                            rows.append(row)
                    written = _insert_rows(rows)
                    total_written += written
                    if written:
                        LOGGER.info(
                            "%s blocks %s-%s: +%s events",
                            pool.protocol_slug,
                            chunk_from,
                            chunk_to,
                            written,
                        )
            run.add_rows(total_written)
            return run.id
    finally:
        if owns_http:
            http.close()


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect on-chain staking-pool events into onchain.staking_events."
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Walk every pool from its deployment block. One-shot; default is incremental.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON output."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv if argv is not None else sys.argv[1:])
    run_id = collect(backfill=args.backfill)
    if args.json:
        print(
            json.dumps(
                {
                    "ingest_run_id": run_id,
                    "source": SOURCE_NAME,
                    "endpoint": COLLECT_ENDPOINT_LABEL,
                    "mode": "backfill" if args.backfill else "incremental",
                }
            )
        )
    else:
        print(f"On-chain staking collector wrote ingest_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
