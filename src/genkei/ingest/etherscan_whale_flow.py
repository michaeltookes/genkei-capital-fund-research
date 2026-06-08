"""ETH whale-flow snapshot collector (B-106).

For each curated address in ``watchlist.eth_whale_addresses`` we land one
row per ``(address, ts)`` per day in ``onchain.eth_whale_flows``:

  - ``balance_eth``               — current Etherscan-reported balance
  - ``net_flow_eth_24h``          — Σ(incoming) − Σ(outgoing) over the
                                    24h window ending at snapshot time,
                                    filtered to ``isError=0`` only
  - ``net_flow_usd_24h``          — net_flow_eth × ETH price at snapshot
  - ``tx_count_24h``              — non-error tx count touching the
                                    address in the same window

Per the B-106 spec, **ERC-20 transfers are ignored** — we hit the
``account/txlist`` endpoint which only returns native-ETH transactions.
The ``account/tokentx`` endpoint would surface ERC-20 movements but
that's deliberate v2 scope, not v1.

ETH USD pricing pulls the latest price from ``coingecko.market_data``
(via a single inline SELECT at the top of the run) and applies it
uniformly to every snapshot row in the run. The snapshot price is
deliberately *one* number per run — Etherscan publishes per-tx wei
amounts but not per-tx USD, and the daily aggregate signal is robust to
the resulting 24h price-drift approximation. Backfill rows born from
historical txlist queries will use the same single price, which is
honest about the data's actual provenance.

The ETH USD column is NULL when no recent CoinGecko row is available
(e.g. the coingecko collector hasn't run yet) — the ETH-denominated
columns are still load-bearing, USD is a derived convenience.

**Read each category's sign carefully** — see the methodology doc at
``docs/sources/eth-whale-addresses.md``. The biggest gotcha: exchange
inflow REVERSES the intuitive read (it's sell pressure, not exchange
buying).

Configuration:
  - ``ETHERSCAN_API_KEY`` — required (Etherscan V2 has no keyless tier).
    When unset, the collector gracefully **skips** (D-020 pattern):
    logs a warning, records a successful run with 0 rows, returns.
  - Rate limit: free Etherscan tier is 3 req/s. We use 2/s to leave
    headroom for the eth_blockNumber + getblocknobytime probes that
    every run does once up-front.
  - Per-run API calls: 2 (block-resolution probes) + 2 per address
    (balance + txlist). For 20 v1 addresses → ~42 calls per run,
    comfortably under the 100k/day Etherscan free-tier ceiling.

Modes:
  - **incremental** (default) — snapshot every address at "now".
    Idempotent on ``(address, ts)`` — the ts column is the UTC midnight
    floor of the run's wall clock, so re-running within the same day is
    an upsert that overwrites earlier-in-day numbers with later ones.
  - **--since YYYY-MM-DD** — backfill mode. Walks the calendar from
    ``since`` to today; one snapshot per address per day. The 24h
    window for each historical day uses the day's midnight-to-midnight
    range computed against Etherscan's getblocknobytime. Slow — for 20
    addresses × 365 days, ~14,600 calls + 365 boundary lookups; runs
    in ~3 hours at the 2 req/s throttle.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from genkei.common import db
from genkei.common.http import HttpClient, RateLimit
from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    EthWhaleAddressEntry,
    load_watchlist,
)

# Suppress httpx INFO logging — Etherscan auth lives in URL params, and
# INFO-level URL logging would leak the API key to stdout / log aggregators.
logging.getLogger("httpx").setLevel(logging.WARNING)

SOURCE_NAME = "eth_whale_flow"
COLLECT_ENDPOINT_LABEL = "collect"
ETHERSCAN_V2_URL = "https://api.etherscan.io/v2/api"
ETHEREUM_CHAIN_ID = 1
ETH_DECIMALS = 18

ETHERSCAN_API_KEY_ENV = "ETHERSCAN_API_KEY"
DEFAULT_RATE_LIMIT = RateLimit.per_second(2)
TXLIST_PAGE_SIZE = 10_000  # Etherscan caps at 10000; one page covers any
                            # plausible per-day whale tx count.

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Snapshot:
    """Normalized per-(address, ts) row ready for bulk_upsert."""

    address: str
    ts: datetime
    label: str
    category: str
    balance_eth: Decimal
    balance_usd_at_snapshot: Decimal | None
    net_flow_eth_24h: Decimal
    net_flow_usd_24h: Decimal | None
    tx_count_24h: int


def resolve_api_key() -> str | None:
    """Return the Etherscan API key from env, or ``None`` if unset."""
    key = os.environ.get(ETHERSCAN_API_KEY_ENV, "").strip()
    return key or None


def _build_url(params: dict[str, Any]) -> str:
    """Construct an Etherscan v2 URL from a params dict (no quoting needed
    because all our values are ints / addresses / well-formed strings)."""
    return ETHERSCAN_V2_URL + "?" + "&".join(f"{k}={v}" for k, v in params.items())


def fetch_block_by_time(http: HttpClient, *, api_key: str, ts: int) -> int:
    """Resolve a UNIX-second timestamp to the closest-before block number."""
    url = _build_url(
        {
            "chainid": ETHEREUM_CHAIN_ID,
            "module": "block",
            "action": "getblocknobytime",
            "timestamp": ts,
            "closest": "before",
            "apikey": api_key,
        }
    )
    payload = http.get_json(url)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Etherscan getblocknobytime malformed: {payload!r}")
    result = payload.get("result")
    if isinstance(result, str) and result.isdigit():
        return int(result)
    raise RuntimeError(
        f"Etherscan getblocknobytime returned non-numeric result: {payload!r}"
    )


def fetch_current_head_block(http: HttpClient, *, api_key: str) -> int:
    """Pull the current Ethereum mainnet head block via eth_blockNumber."""
    url = _build_url(
        {
            "chainid": ETHEREUM_CHAIN_ID,
            "module": "proxy",
            "action": "eth_blockNumber",
            "apikey": api_key,
        }
    )
    payload = http.get_json(url)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Etherscan eth_blockNumber malformed: {payload!r}")
    result = payload.get("result")
    if isinstance(result, str) and result.startswith("0x"):
        return int(result, 16)
    raise RuntimeError(f"Etherscan eth_blockNumber non-hex result: {payload!r}")


def fetch_balance_wei(http: HttpClient, *, api_key: str, address: str) -> int:
    """Pull a single address's current balance in wei."""
    url = _build_url(
        {
            "chainid": ETHEREUM_CHAIN_ID,
            "module": "account",
            "action": "balance",
            "address": address,
            "tag": "latest",
            "apikey": api_key,
        }
    )
    payload = http.get_json(url)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Etherscan balance malformed: {payload!r}")
    result = payload.get("result")
    if isinstance(result, str) and result.lstrip("-").isdigit():
        return int(result)
    raise RuntimeError(f"Etherscan balance non-numeric result: {payload!r}")


def fetch_txlist(
    http: HttpClient,
    *,
    api_key: str,
    address: str,
    start_block: int,
    end_block: int,
) -> list[dict[str, Any]]:
    """Pull native-ETH transactions for one address in a block range.

    Returns the ``result`` list (possibly empty). "No transactions found"
    is a benign empty case; other error strings raise RuntimeError so a
    real upstream failure doesn't silently land as zero flow.
    """
    url = _build_url(
        {
            "chainid": ETHEREUM_CHAIN_ID,
            "module": "account",
            "action": "txlist",
            "address": address,
            "startblock": start_block,
            "endblock": end_block,
            "page": 1,
            "offset": TXLIST_PAGE_SIZE,
            "sort": "asc",
            "apikey": api_key,
        }
    )
    payload = http.get_json(url)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Etherscan txlist malformed: {payload!r}")
    status = payload.get("status")
    result = payload.get("result")
    if isinstance(result, list):
        return result
    if status == "0" and isinstance(result, str):
        if result.lower().startswith("no transactions found"):
            return []
        raise RuntimeError(f"Etherscan txlist error: {result}")
    raise RuntimeError(
        f"Etherscan txlist unexpected shape: status={status!r}, "
        f"result={str(result)[:80]!r}"
    )


def compute_net_flow_and_count(
    txs: list[dict[str, Any]], *, address: str
) -> tuple[int, int]:
    """Sum incoming - outgoing native-ETH wei and count non-error txs.

    Filters by ``isError == "0"`` (Etherscan publishes reverted txns with
    isError="1"; those move no real value and would inflate the flow).
    Returns ``(net_wei, tx_count)``. The address comparison is
    case-insensitive because Etherscan returns lowercase but the watchlist
    might be passed checksum-cased.
    """
    addr_lower = address.lower()
    net_wei = 0
    count = 0
    for tx in txs:
        if not isinstance(tx, dict):
            continue
        if tx.get("isError") == "1":
            continue
        value_raw = tx.get("value")
        if not isinstance(value_raw, str) or not value_raw.lstrip("-").isdigit():
            continue
        value = int(value_raw)
        to_addr = (tx.get("to") or "").lower()
        from_addr = (tx.get("from") or "").lower()
        touched = False
        if to_addr == addr_lower:
            net_wei += value
            touched = True
        if from_addr == addr_lower:
            net_wei -= value
            touched = True
        if touched:
            count += 1
    return net_wei, count


def latest_eth_price_usd() -> Decimal | None:
    """Pull the most recent ETH USD price from coingecko.market_data.

    Returns None when the coingecko table is empty / unrun — the collector
    still writes ETH-denominated rows with NULL USD columns rather than
    failing the run. Re-running once coingecko backfills will overwrite
    the NULLs via the natural-key upsert.
    """
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT price_usd FROM coingecko.market_data "
            "WHERE coingecko_id = 'ethereum' ORDER BY ts DESC LIMIT 1"
        )
        row = cur.fetchone()
    if row is None or row[0] is None:
        return None
    return Decimal(str(row[0]))


def _wei_to_eth(wei: int) -> Decimal:
    """Convert wei to ETH at 18 decimal places (lossless for any plausible amount)."""
    return Decimal(wei) / Decimal(10**ETH_DECIMALS)


def build_snapshot(
    *,
    address_entry: EthWhaleAddressEntry,
    ts: datetime,
    balance_wei: int,
    net_wei: int,
    tx_count: int,
    eth_price_usd: Decimal | None,
) -> _Snapshot:
    """Assemble an _UnlockRow-style snapshot from the raw inputs."""
    balance_eth = _wei_to_eth(balance_wei)
    net_flow_eth = _wei_to_eth(net_wei)
    if eth_price_usd is not None:
        balance_usd = (balance_eth * eth_price_usd).quantize(Decimal("0.01"))
        net_flow_usd = (net_flow_eth * eth_price_usd).quantize(Decimal("0.01"))
    else:
        balance_usd = None
        net_flow_usd = None
    return _Snapshot(
        address=address_entry.address,
        ts=ts,
        label=address_entry.label,
        category=address_entry.category,
        balance_eth=balance_eth,
        balance_usd_at_snapshot=balance_usd,
        net_flow_eth_24h=net_flow_eth,
        net_flow_usd_24h=net_flow_usd,
        tx_count_24h=tx_count,
    )


def _snapshot_to_row(
    snap: _Snapshot,
    *,
    ingest_run_id: int,
    source_endpoint: str,
    fetched_at: datetime,
) -> dict[str, Any]:
    """Convert a _Snapshot to the dict bulk_upsert expects."""
    return {
        "address": snap.address,
        "ts": snap.ts,
        "label": snap.label,
        "category": snap.category,
        "balance_eth": snap.balance_eth,
        "balance_usd_at_snapshot": snap.balance_usd_at_snapshot,
        "net_flow_eth_24h": snap.net_flow_eth_24h,
        "net_flow_usd_24h": snap.net_flow_usd_24h,
        "tx_count_24h": snap.tx_count_24h,
        "source_endpoint": source_endpoint,
        "fetched_at": fetched_at,
        "ingest_run_id": ingest_run_id,
    }


def _iter_snapshot_dates(*, since: date | None, today: date) -> list[date]:
    """Return the list of snapshot dates to fetch — one per day for backfill,
    or just [today] for incremental.

    The endpoint of the range is ``today`` inclusive; the 24h window for each
    listed date runs from ``date`` midnight UTC to ``date + 1`` midnight UTC.
    """
    if since is None:
        return [today]
    days: list[date] = []
    cursor = since
    while cursor <= today:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _utc_midnight(d: date) -> datetime:
    """UTC midnight for a calendar date — the canonical ts for daily snapshots."""
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


def collect(
    config_path: Path = DEFAULT_WATCHLIST_PATH,
    *,
    http: HttpClient | None = None,
    api_key: str | None = None,
    since: date | None = None,
    now: datetime | None = None,
) -> int:
    """Run the whale-flow collector once. Returns meta.ingest_runs id.

    Graceful skip when no API key (D-020). When ``since`` is None, snapshots
    a single ``ts == utc_midnight(today)`` row per address. When ``since`` is
    set, walks the calendar from ``since`` to today and writes one row per
    address per day — see the per-day Etherscan boundary-block resolution
    in the body. ``now`` is injectable for testing.
    """
    resolved_key = api_key if api_key is not None else resolve_api_key()
    wall_now = (now if now is not None else datetime.now(timezone.utc)).astimezone(
        timezone.utc
    )
    today = wall_now.date()
    snapshot_days = _iter_snapshot_dates(since=since, today=today)

    watchlist = load_watchlist(config_path)
    addresses = list(watchlist.eth_whale_addresses)
    if not addresses:
        raise SystemExit(
            "watchlists.yml has no eth_whale_addresses entries — nothing to fetch."
        )

    owns_http = http is None
    if http is None:
        http = HttpClient(SOURCE_NAME, rate_limit=DEFAULT_RATE_LIMIT)

    try:
        with db.ingest_run(
            SOURCE_NAME,
            endpoint=COLLECT_ENDPOINT_LABEL,
            metadata={
                "mode": "backfill" if since else "incremental",
                "address_count": len(addresses),
                "snapshot_day_count": len(snapshot_days),
                "has_api_key": resolved_key is not None,
            },
        ) as run:
            if resolved_key is None:
                LOGGER.warning(
                    "%s not set — skipping whale-flow collect. "
                    "Register a free key at https://etherscan.io/myapikey "
                    "and set %s in .env / GH Actions secrets to enable.",
                    ETHERSCAN_API_KEY_ENV,
                    ETHERSCAN_API_KEY_ENV,
                )
                run.add_rows(0)
                return run.id

            eth_price_usd = latest_eth_price_usd()
            total_written = 0
            for snapshot_date in snapshot_days:
                # 24h window:
                #   - Historical day (snapshot_date < today): midnight-to-
                #     midnight UTC, both boundaries resolved via
                #     getblocknobytime against Etherscan.
                #   - Today's run (snapshot_date == today): from today's
                #     UTC midnight to the current head block. The naive
                #     "today midnight to tomorrow midnight" approach gets
                #     rejected by Etherscan ("Block timestamp too far in
                #     the future") since tomorrow-midnight hasn't happened.
                window_start = _utc_midnight(snapshot_date)
                ts = window_start  # canonical PK ts; midnight-floored
                is_today = snapshot_date == today

                try:
                    start_block = fetch_block_by_time(
                        http,
                        api_key=resolved_key,
                        ts=int(window_start.timestamp()),
                    )
                    if is_today:
                        end_block = fetch_current_head_block(
                            http, api_key=resolved_key
                        )
                    else:
                        window_end = window_start + timedelta(days=1)
                        end_block = fetch_block_by_time(
                            http,
                            api_key=resolved_key,
                            ts=int(window_end.timestamp()),
                        )
                    fetched_at = datetime.now(timezone.utc)
                except (
                    httpx.TimeoutException,
                    httpx.NetworkError,
                    RuntimeError,
                ) as exc:
                    LOGGER.error(
                        "Etherscan block-by-time probe failed for %s: %s",
                        snapshot_date,
                        exc,
                    )
                    raise

                rows: list[dict[str, Any]] = []
                for entry in addresses:
                    try:
                        balance_wei = fetch_balance_wei(
                            http, api_key=resolved_key, address=entry.address
                        )
                        txs = fetch_txlist(
                            http,
                            api_key=resolved_key,
                            address=entry.address,
                            start_block=start_block,
                            end_block=end_block,
                        )
                    except (
                        httpx.TimeoutException,
                        httpx.NetworkError,
                        RuntimeError,
                    ) as exc:
                        LOGGER.warning(
                            "Etherscan fetch failed for %s on %s: %s — skipping address",
                            entry.address,
                            snapshot_date,
                            exc,
                        )
                        continue
                    net_wei, tx_count = compute_net_flow_and_count(
                        txs, address=entry.address
                    )
                    snap = build_snapshot(
                        address_entry=entry,
                        ts=ts,
                        balance_wei=balance_wei,
                        net_wei=net_wei,
                        tx_count=tx_count,
                        eth_price_usd=eth_price_usd,
                    )
                    rows.append(
                        _snapshot_to_row(
                            snap,
                            ingest_run_id=run.id,
                            source_endpoint=ETHERSCAN_V2_URL,
                            fetched_at=fetched_at,
                        )
                    )

                if not rows:
                    LOGGER.info(
                        "Whale-flow: 0 rows landed for %s (every address fetch failed)",
                        snapshot_date,
                    )
                    continue
                with db.connection() as conn:
                    written = db.bulk_upsert(
                        conn,
                        "onchain.eth_whale_flows",
                        rows,
                        conflict_keys=("address", "ts"),
                    )
                total_written += written
                LOGGER.info(
                    "Whale-flow: +%s rows for %s (%s addresses)",
                    written,
                    snapshot_date,
                    len(rows),
                )
            run.add_rows(total_written)
            return run.id
    finally:
        if owns_http:
            http.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Snapshot ETH whale-address balance + 24h net flow into "
            "onchain.eth_whale_flows."
        )
    )
    parser.add_argument(
        "--since",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help=(
            "Backfill from this YYYY-MM-DD (inclusive) through today. Slow — "
            "expect ~3h for 1y of history at 20 addresses."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_WATCHLIST_PATH,
        help="Watchlist path.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv if argv is not None else sys.argv[1:])
    run_id = collect(args.config, since=args.since)
    print(f"Whale-flow collector wrote ingest_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
