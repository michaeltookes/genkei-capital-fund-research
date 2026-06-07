"""Sui on-chain validator + staking-flow collector (B-088).

Fetches the current epoch's validator state from the public Sui mainnet
JSON-RPC at ``https://fullnode.mainnet.sui.io`` and lands one row per
``(epoch, validator_address)`` in ``onchain.sui_validators``. v1 captures
the dominant institutional-flow signals the 2026-05-20 SUI research
session named as missing: total staked SUI trajectory, per-validator
pending stake / pending withdraw (the actual flow signal — net delta
across all validators answers "are stakers committing more capital or
unbonding"), voting power distribution, commission rates, and APYs.

Methods used per run (two RPC POSTs, both deterministic):

  - ``suix_getLatestSuiSystemState`` — returns the full system state
    including ``activeValidators`` (a list of ~129 validator records,
    each with stake / pending flow / commission / lifecycle epochs)
    plus epoch metadata (number, start timestamp, duration, totalStake).
  - ``suix_getValidatorsApy`` — returns ``{epoch, apys: [{address, apy}]}``
    which we join into the validator rows by ``address`` → ``suiAddress``.

The public RPC requires no API key, no auth, no Cloudflare token. The
B-088 backlog spec offered Blockvision's managed RPC as the working path;
that turned out to be unnecessary — the public fullnode serves the
needed methods cleanly. Skipping Blockvision keeps the collector simpler
(no key, no D-020 graceful-skip plumbing) and removes a third-party
dependency.

**Backfill is NOT supported** by the public RPC. ``suix_getEpochs``
returns ``Method not found`` on the public fullnode (it's an indexer-API
method, not standard JSON-RPC), so historical epoch reconstruction would
require a different data path. v1 is forward-only from the day of first
run; backfill is filed as a v2 follow-up. Idempotent via the
``(epoch, validator_address)`` PK so re-runs within the same epoch are
no-op upserts.

Stake amounts are stored as MIST (Sui's atomic unit, 1 SUI = 10^9 MIST)
without unit conversion at write time — the raw upstream value preserves
audit fidelity and keeps the divide-by-1e9 a query-time concern.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

from genkei.common import db
from genkei.common.http import HttpClient, RateLimit

SOURCE_NAME = "sui_staking"
COLLECT_ENDPOINT_LABEL = "collect"

# Public Sui mainnet fullnode — no auth, no rate-limit headers observed in
# Phase 1 probing. One req/s is a polite ceiling for a daily 2-call run.
SUI_RPC_URL = "https://fullnode.mainnet.sui.io"
DEFAULT_RATE_LIMIT = RateLimit.per_second(1)

# JSON-RPC method names used by the collector. Stable for the standard
# Sui fullnode API.
METHOD_SYSTEM_STATE = "suix_getLatestSuiSystemState"
METHOD_VALIDATORS_APY = "suix_getValidatorsApy"

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ValidatorRow:
    """A normalized per-(epoch, validator) snapshot ready for bulk_upsert."""

    epoch: int
    epoch_start_ts: datetime
    validator_address: str
    name: str | None
    voting_power: int | None
    stake_amount_mist: Decimal
    next_epoch_stake_mist: Decimal | None
    pending_stake_mist: Decimal
    pending_withdraw_mist: Decimal
    commission_rate_bps: int | None
    gas_price: int | None
    apy: Decimal | None
    staking_pool_activation_epoch: int | None
    staking_pool_deactivation_epoch: int | None
    rewards_pool_mist: Decimal | None


def _rpc_post(http: HttpClient, method: str, params: list[Any] | None = None) -> Any:
    """Issue one JSON-RPC POST and return the ``result`` field on success.

    Raises ``httpx.HTTPStatusError`` on non-2xx HTTP, ``ValueError`` on
    JSON-RPC application-level errors (the ``error`` field is set) or on
    malformed responses missing both ``result`` and ``error``.
    """
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
    response = http.request(
        "POST", SUI_RPC_URL, json=payload, headers={"Content-Type": "application/json"}
    )
    response.raise_for_status()
    body = response.json()
    if "error" in body:
        err = body["error"]
        raise ValueError(
            f"Sui RPC error on {method}: code={err.get('code')} message={err.get('message')!r}"
        )
    if "result" not in body:
        raise ValueError(
            f"Sui RPC response on {method} missing 'result' field: {body!r}"
        )
    return body["result"]


def _coerce_int(raw: Any) -> int | None:
    """Sui RPC returns u64s as JSON strings; parse them as int."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            return None
    if isinstance(raw, float):
        return int(raw)
    return None


def _coerce_decimal(raw: Any) -> Decimal | None:
    """Sui RPC returns u64s as JSON strings; parse them into Decimal.

    Used for MIST-denominated stake columns where Python int would also
    work — Decimal is the canonical type for the column's NUMERIC(40, 0)
    schema, avoiding a psycopg adapter round-trip.
    """
    if raw is None:
        return None
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, int):
        return Decimal(raw)
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return None
        try:
            return Decimal(stripped)
        except Exception:
            return None
    if isinstance(raw, float):
        return Decimal(str(raw))
    return None


def _ms_to_utc_datetime(raw: Any) -> datetime | None:
    """Parse a Sui ``epochStartTimestampMs`` (string-encoded u64 ms) → UTC dt."""
    ms = _coerce_int(raw)
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def parse_validator_rows(
    system_state: Any,
    apy_payload: Any,
) -> list[_ValidatorRow]:
    """Decode the suix_getLatestSuiSystemState + suix_getValidatorsApy
    payloads into a list of per-validator snapshot rows.

    Joins APY into each validator row by suiAddress = apys[].address. A
    validator missing from the APY response is kept with apy=None rather
    than dropped — the missing APY is a soft signal-quality issue, not a
    reason to discard the staking/flow data we already have.
    """
    if not isinstance(system_state, dict):
        raise ValueError(
            f"suix_getLatestSuiSystemState payload is not a JSON object: "
            f"{type(system_state).__name__}"
        )
    epoch = _coerce_int(system_state.get("epoch"))
    epoch_start_ts = _ms_to_utc_datetime(system_state.get("epochStartTimestampMs"))
    if epoch is None or epoch_start_ts is None:
        raise ValueError(
            f"Sui system state missing required epoch/epochStartTimestampMs "
            f"(got epoch={system_state.get('epoch')!r}, "
            f"epochStartTimestampMs={system_state.get('epochStartTimestampMs')!r})"
        )

    active = system_state.get("activeValidators")
    if not isinstance(active, list):
        raise ValueError(
            f"Sui system state activeValidators is not a list: {type(active).__name__}"
        )

    # Build APY lookup: {validator_address: apy_decimal}. apys may legitimately
    # be empty (e.g. if the APY method is briefly unavailable); fall through.
    apy_by_address: dict[str, Decimal] = {}
    if isinstance(apy_payload, dict):
        apys_list = apy_payload.get("apys")
        if isinstance(apys_list, list):
            for entry in apys_list:
                if not isinstance(entry, dict):
                    continue
                addr = entry.get("address")
                apy_value = entry.get("apy")
                if not isinstance(addr, str):
                    continue
                if isinstance(apy_value, (int, float)):
                    # Quantize to the schema's 6 fractional digits. Sui APYs
                    # are typically 1-5% range so 6 digits gives 1bp resolution.
                    apy_by_address[addr] = Decimal(str(apy_value)).quantize(
                        Decimal("0.000001")
                    )

    rows: list[_ValidatorRow] = []
    for v in active:
        if not isinstance(v, dict):
            continue
        addr = v.get("suiAddress")
        if not isinstance(addr, str) or not addr:
            LOGGER.warning(
                "Sui validator row missing suiAddress (epoch=%s) — skipping", epoch
            )
            continue
        stake = _coerce_decimal(v.get("stakingPoolSuiBalance"))
        if stake is None:
            LOGGER.warning(
                "Sui validator %s missing stakingPoolSuiBalance — skipping", addr
            )
            continue

        rows.append(
            _ValidatorRow(
                epoch=epoch,
                epoch_start_ts=epoch_start_ts,
                validator_address=addr,
                name=v.get("name") if isinstance(v.get("name"), str) else None,
                voting_power=_coerce_int(v.get("votingPower")),
                stake_amount_mist=stake,
                next_epoch_stake_mist=_coerce_decimal(v.get("nextEpochStake")),
                pending_stake_mist=_coerce_decimal(v.get("pendingStake")) or Decimal(0),
                pending_withdraw_mist=(
                    _coerce_decimal(v.get("pendingTotalSuiWithdraw")) or Decimal(0)
                ),
                commission_rate_bps=_coerce_int(v.get("commissionRate")),
                gas_price=_coerce_int(v.get("gasPrice")),
                apy=apy_by_address.get(addr),
                staking_pool_activation_epoch=_coerce_int(
                    v.get("stakingPoolActivationEpoch")
                ),
                staking_pool_deactivation_epoch=_coerce_int(
                    v.get("stakingPoolDeactivationEpoch")
                ),
                rewards_pool_mist=_coerce_decimal(v.get("rewardsPool")),
            )
        )
    return rows


def _row_to_dict(
    row: _ValidatorRow,
    *,
    ingest_run_id: int,
    source_endpoint: str,
    fetched_at: datetime,
) -> dict[str, Any]:
    """Convert a _ValidatorRow to the dict bulk_upsert expects."""
    return {
        "epoch": row.epoch,
        "epoch_start_ts": row.epoch_start_ts,
        "validator_address": row.validator_address,
        "name": row.name,
        "voting_power": row.voting_power,
        "stake_amount_mist": row.stake_amount_mist,
        "next_epoch_stake_mist": row.next_epoch_stake_mist,
        "pending_stake_mist": row.pending_stake_mist,
        "pending_withdraw_mist": row.pending_withdraw_mist,
        "commission_rate_bps": row.commission_rate_bps,
        "gas_price": row.gas_price,
        "apy": row.apy,
        "staking_pool_activation_epoch": row.staking_pool_activation_epoch,
        "staking_pool_deactivation_epoch": row.staking_pool_deactivation_epoch,
        "rewards_pool_mist": row.rewards_pool_mist,
        "source_endpoint": source_endpoint,
        "fetched_at": fetched_at,
        "ingest_run_id": ingest_run_id,
    }


def collect(*, http: HttpClient | None = None) -> int:
    """Run the Sui staking collector once. Returns the meta.ingest_runs id.

    The public Sui RPC publishes only the current epoch's state — there is
    no ``backfill`` mode at this layer. Re-running within the same epoch is
    a no-op upsert on the ``(epoch, validator_address)`` PK; once a new
    epoch begins (~24h cadence) the next run lands one new row per
    validator.
    """
    owns_http = http is None
    if http is None:
        http = HttpClient(SOURCE_NAME, rate_limit=DEFAULT_RATE_LIMIT)

    try:
        with db.ingest_run(
            SOURCE_NAME,
            endpoint=COLLECT_ENDPOINT_LABEL,
        ) as run:
            partial_failures: list[dict[str, str]] = []

            def record_partial(name: str, error: Exception) -> None:
                partial_failures.append(
                    {
                        "name": name,
                        "url": SUI_RPC_URL,
                        "error": str(error),
                    }
                )
                db.record_partial_endpoints(run.id, partial_failures)

            try:
                system_state = _rpc_post(http, METHOD_SYSTEM_STATE)
            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.HTTPStatusError,
                json.JSONDecodeError,
                ValueError,
            ) as exc:
                LOGGER.error("Sui RPC fetch failed: %s", exc)
                record_partial(COLLECT_ENDPOINT_LABEL, exc)
                raise RuntimeError(f"Sui RPC fetch failed: {exc}") from exc

            try:
                apy_payload = _rpc_post(http, METHOD_VALIDATORS_APY)
            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.HTTPStatusError,
                json.JSONDecodeError,
                ValueError,
            ) as exc:
                LOGGER.warning("Sui APY RPC fetch failed: %s", exc)
                record_partial(METHOD_VALIDATORS_APY, exc)
                apy_payload = None

            fetched_at = datetime.now(timezone.utc)

            db.store_raw_blob(
                run.id, f"{METHOD_SYSTEM_STATE}", SUI_RPC_URL, system_state
            )
            if apy_payload is not None:
                db.store_raw_blob(
                    run.id, f"{METHOD_VALIDATORS_APY}", SUI_RPC_URL, apy_payload
                )

            try:
                validator_rows = parse_validator_rows(system_state, apy_payload)
            except ValueError as exc:
                LOGGER.error("Sui payload parse failed: %s", exc)
                record_partial(COLLECT_ENDPOINT_LABEL, exc)
                raise RuntimeError(f"Sui payload parse failed: {exc}") from exc
            if not validator_rows:
                LOGGER.warning(
                    "Sui collector parsed 0 validator rows — possible upstream shape change"
                )
                run.add_rows(0)
                return run.id

            rows = [
                _row_to_dict(
                    r,
                    ingest_run_id=run.id,
                    source_endpoint=SUI_RPC_URL,
                    fetched_at=fetched_at,
                )
                for r in validator_rows
            ]
            with db.connection() as conn:
                written = db.bulk_upsert(
                    conn,
                    "onchain.sui_validators",
                    rows,
                    conflict_keys=("epoch", "validator_address"),
                )
            run.add_rows(written)
            LOGGER.info(
                "Sui staking: +%s rows (epoch=%s, %s validators)",
                written,
                validator_rows[0].epoch,
                len(validator_rows),
            )
            return run.id
    finally:
        if owns_http:
            http.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI flags for the collector entry point."""
    parser = argparse.ArgumentParser(
        description=(
            "Collect Sui mainnet per-epoch validator snapshots into "
            "onchain.sui_validators."
        )
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the collector from ``python -m genkei.ingest.sui_staking``."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parse_args(argv if argv is not None else sys.argv[1:])
    run_id = collect()
    print(f"Sui staking collector wrote ingest_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
