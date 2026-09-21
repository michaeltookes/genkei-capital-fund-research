"""Sui on-chain validator + staking-flow collector (B-088, GraphQL since B-145).

Fetches the current epoch's validator state from the public Sui mainnet
GraphQL API at ``https://graphql.mainnet.sui.io/graphql`` and lands one
row per ``(epoch, validator_address)`` in ``onchain.sui_validators``.
v1 captures the dominant institutional-flow signals the 2026-05-20 SUI
research session named as missing: total staked SUI trajectory,
per-validator pending stake / pending withdraw (the actual flow signal —
net delta across all validators answers "are stakers committing more
capital or unbonding"), voting power distribution, and commission rates.

**History:** v1 (B-088) used JSON-RPC on the public fullnode
(``suix_getLatestSuiSystemState`` + ``suix_getValidatorsApy``). Sui
deprecated JSON-RPC on public fullnodes upstream — observed failing with
``-32601 Method not found`` plus an explicit migration notice by
2026-09-17 — so B-145 ported the collector to the GraphQL API. The
per-validator data now comes from ``epoch.validatorSet.activeValidators``
whose ``contents.json`` is the on-chain ``ValidatorV1`` Move struct
(snake_case field names, u64s as JSON strings).

**APY is no longer available.** ``suix_getValidatorsApy`` was an
RPC-computed convenience with no GraphQL equivalent (deriving it from
``staking_pool.exchange_rates`` dynamic fields is possible but heavy).
Rows written since the port carry ``apy = NULL``; pre-port rows keep
their stored APYs (the upsert never nulls an existing APY). Recorded in
``docs/sources/sui-staking.md``.

The GraphQL API requires no key or auth, but paginates
``activeValidators`` (50 per page, ~120 validators → 3 requests/run).
Pagination is guarded against an epoch boundary mid-fetch: if
``epochId`` changes between pages the run fails loudly rather than
stitching two epochs into one snapshot.

**Backfill is NOT supported** at this layer — same posture as v1:
forward-only from the day of first run. Idempotent via the
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
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from genkei.common import db
from genkei.common.http import HttpClient, RateLimit

SOURCE_NAME = "sui_staking"
COLLECT_ENDPOINT_LABEL = "collect"

# Public Sui mainnet GraphQL API — no auth, no key (B-145 probing,
# 2026-09-18). One req/s is a polite ceiling for a daily ~3-page run.
SUI_GRAPHQL_URL = "https://graphql.mainnet.sui.io/graphql"
DEFAULT_RATE_LIMIT = RateLimit.per_second(1)

# activeValidators page size. The server serves 50/page; ~120 mainnet
# validators means 3 pages per run.
VALIDATORS_PAGE_SIZE = 50

# One query serves the whole run: epoch metadata rides along on every
# page (cheap) and doubles as the epoch-boundary guard between pages.
ACTIVE_VALIDATORS_QUERY = """
query ($first: Int!, $after: String) {
  epoch {
    epochId
    startTimestamp
    validatorSet {
      activeValidators(first: $first, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { contents { json } }
      }
    }
  }
}
""".strip()

LOGGER = logging.getLogger(__name__)
_SUI_VALIDATOR_CONFLICT_KEYS = ("epoch", "validator_address")


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


def _graphql_post(
    http: HttpClient, query: str, variables: dict[str, Any] | None = None
) -> Any:
    """Issue one GraphQL POST and return the ``data`` field on success.

    Raises ``httpx.HTTPStatusError`` on non-2xx HTTP, ``ValueError`` when
    the response carries GraphQL ``errors`` or is missing ``data``.
    """
    payload = {"query": query, "variables": variables or {}}
    response = http.request(
        "POST",
        SUI_GRAPHQL_URL,
        retryable=True,
        json=payload,
        headers={"Content-Type": "application/json"},
    )
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError(
            f"Sui GraphQL response is not a JSON object: {type(body).__name__}"
        )
    if body.get("errors"):
        first = body["errors"][0] if isinstance(body["errors"], list) else body["errors"]
        message = first.get("message") if isinstance(first, dict) else first
        raise ValueError(f"Sui GraphQL error: {message!r}")
    if body.get("data") is None:
        raise ValueError(f"Sui GraphQL response missing 'data' field: {body!r}")
    return body["data"]


def fetch_active_validators(http: HttpClient) -> tuple[Any, Any, list[Any], list[Any]]:
    """Page through ``epoch.validatorSet.activeValidators``.

    Returns ``(epoch_id_raw, start_timestamp_raw, validator_json_list,
    page_payloads)`` where ``page_payloads`` are the raw per-page ``data``
    dicts for blob storage. Raises ``ValueError`` on a malformed page or
    when ``epochId`` changes between pages (epoch boundary mid-fetch —
    the next daily run lands the new epoch cleanly instead).
    """
    epoch_id_raw: Any = None
    start_ts_raw: Any = None
    validators: list[Any] = []
    pages: list[Any] = []
    after: str | None = None
    while True:
        data = _graphql_post(
            http,
            ACTIVE_VALIDATORS_QUERY,
            {"first": VALIDATORS_PAGE_SIZE, "after": after},
        )
        epoch = data.get("epoch") if isinstance(data, dict) else None
        if not isinstance(epoch, dict):
            raise ValueError(f"Sui GraphQL page missing 'epoch' object: {data!r}")
        if not pages:
            epoch_id_raw = epoch.get("epochId")
            start_ts_raw = epoch.get("startTimestamp")
        elif epoch.get("epochId") != epoch_id_raw:
            raise ValueError(
                f"Sui epoch changed mid-pagination "
                f"({epoch_id_raw!r} -> {epoch.get('epochId')!r}) — aborting run"
            )
        try:
            connection = epoch["validatorSet"]["activeValidators"]
            nodes = connection["nodes"]
            page_info = connection["pageInfo"]
        except (KeyError, TypeError) as exc:
            raise ValueError(
                f"Sui GraphQL page missing activeValidators structure: {exc}"
            ) from exc
        if not isinstance(nodes, list):
            raise ValueError(
                f"Sui activeValidators nodes is not a list: {type(nodes).__name__}"
            )
        pages.append(data)
        for node in nodes:
            contents = node.get("contents") if isinstance(node, dict) else None
            payload = contents.get("json") if isinstance(contents, dict) else None
            if payload is not None:
                validators.append(payload)
        if not isinstance(page_info, dict) or not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")
        if not after:
            raise ValueError(
                "Sui activeValidators hasNextPage without endCursor — aborting run"
            )
    return epoch_id_raw, start_ts_raw, validators, pages


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
        except (ValueError, InvalidOperation):
            return None
        except Exception:  # pragma: no cover - defensive
            # An unparseable u64 string is benign (handled above); anything
            # else is a surprise worth surfacing rather than swallowing in
            # unattended daily ingest (B-121).
            LOGGER.warning(
                "sui_staking _coerce_decimal: unexpected error coercing %r to Decimal",
                stripped,
                exc_info=True,
            )
            return None
    if isinstance(raw, float):
        return Decimal(str(raw))
    return None


def _iso_to_utc_datetime(raw: Any) -> datetime | None:
    """Parse a GraphQL ``startTimestamp`` (ISO-8601, e.g. ``…T00:05:39.687Z``) → UTC dt."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_validator_rows(
    epoch_id_raw: Any,
    start_timestamp_raw: Any,
    validators: list[Any],
) -> list[_ValidatorRow]:
    """Decode ValidatorV1 Move-struct JSON payloads (GraphQL
    ``contents.json``) into per-validator snapshot rows.

    ``apy`` is always ``None`` — the GraphQL API has no equivalent of the
    retired ``suix_getValidatorsApy`` method (see module docstring). The
    upsert path preserves any APY already stored for the same
    ``(epoch, validator_address)`` row.
    """
    epoch = _coerce_int(epoch_id_raw)
    epoch_start_ts = _iso_to_utc_datetime(start_timestamp_raw)
    if epoch is None or epoch_start_ts is None:
        raise ValueError(
            f"Sui GraphQL epoch missing required epochId/startTimestamp "
            f"(got epochId={epoch_id_raw!r}, startTimestamp={start_timestamp_raw!r})"
        )

    rows: list[_ValidatorRow] = []
    for v in validators:
        if not isinstance(v, dict):
            continue
        metadata = v.get("metadata")
        if not isinstance(metadata, dict):
            LOGGER.warning(
                "Sui validator payload missing metadata (epoch=%s) — skipping", epoch
            )
            continue
        addr = metadata.get("sui_address")
        if not isinstance(addr, str) or not addr:
            LOGGER.warning(
                "Sui validator payload missing sui_address (epoch=%s) — skipping",
                epoch,
            )
            continue
        pool = v.get("staking_pool")
        if not isinstance(pool, dict):
            LOGGER.warning("Sui validator %s missing staking_pool — skipping", addr)
            continue
        stake = _coerce_decimal(pool.get("sui_balance"))
        if stake is None:
            LOGGER.warning(
                "Sui validator %s missing staking_pool.sui_balance — skipping", addr
            )
            continue

        name = metadata.get("name")
        rows.append(
            _ValidatorRow(
                epoch=epoch,
                epoch_start_ts=epoch_start_ts,
                validator_address=addr,
                name=name if isinstance(name, str) else None,
                voting_power=_coerce_int(v.get("voting_power")),
                stake_amount_mist=stake,
                next_epoch_stake_mist=_coerce_decimal(v.get("next_epoch_stake")),
                pending_stake_mist=(
                    _coerce_decimal(pool.get("pending_stake")) or Decimal(0)
                ),
                pending_withdraw_mist=(
                    _coerce_decimal(pool.get("pending_total_sui_withdraw"))
                    or Decimal(0)
                ),
                commission_rate_bps=_coerce_int(v.get("commission_rate")),
                gas_price=_coerce_int(v.get("gas_price")),
                apy=None,
                staking_pool_activation_epoch=_coerce_int(
                    pool.get("activation_epoch")
                ),
                staking_pool_deactivation_epoch=_coerce_int(
                    pool.get("deactivation_epoch")
                ),
                rewards_pool_mist=_coerce_decimal(pool.get("rewards_pool")),
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


def _sui_validator_update_cols(
    row: dict[str, Any],
    *,
    preserve_apy: bool,
) -> list[str]:
    """Columns to update on same-epoch conflicts for Sui validator snapshots."""
    excluded = set(_SUI_VALIDATOR_CONFLICT_KEYS)
    if preserve_apy:
        excluded.add("apy")
    return [col for col in row if col not in excluded]


def _bulk_upsert_sui_validator_rows(
    conn: Any,
    rows: list[dict[str, Any]],
) -> int:
    """Upsert validator rows without nulling APY on partial APY payloads."""
    written = 0
    rows_with_apy = [row for row in rows if row.get("apy") is not None]
    rows_without_apy = [row for row in rows if row.get("apy") is None]
    for batch, preserve_apy in (
        (rows_with_apy, False),
        (rows_without_apy, True),
    ):
        if not batch:
            continue
        written += db.bulk_upsert(
            conn,
            "onchain.sui_validators",
            batch,
            conflict_keys=_SUI_VALIDATOR_CONFLICT_KEYS,
            update_cols=_sui_validator_update_cols(
                batch[0],
                preserve_apy=preserve_apy,
            ),
        )
    return written


def collect(*, http: HttpClient | None = None) -> int:
    """Run the Sui staking collector once. Returns the meta.ingest_runs id.

    The GraphQL API publishes only the current epoch's state — there is
    no ``backfill`` mode at this layer. Re-running within the same epoch is
    an idempotent upsert on the ``(epoch, validator_address)`` PK. Same-epoch
    reruns refresh stake/flow data and preserve any existing APY values
    (all newly parsed rows carry apy=None since the GraphQL port).
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
                        "url": SUI_GRAPHQL_URL,
                        "error": str(error),
                    }
                )
                db.record_partial_endpoints(run.id, partial_failures)

            try:
                epoch_id_raw, start_ts_raw, validators, pages = (
                    fetch_active_validators(http)
                )
            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.HTTPStatusError,
                json.JSONDecodeError,
                ValueError,
            ) as exc:
                LOGGER.error("Sui GraphQL fetch failed: %s", exc)
                record_partial(COLLECT_ENDPOINT_LABEL, exc)
                raise RuntimeError(f"Sui GraphQL fetch failed: {exc}") from exc

            fetched_at = datetime.now(timezone.utc)

            for page_index, page in enumerate(pages, start=1):
                db.store_raw_blob(
                    run.id,
                    f"active_validators_page_{page_index}",
                    SUI_GRAPHQL_URL,
                    page,
                )

            try:
                validator_rows = parse_validator_rows(
                    epoch_id_raw, start_ts_raw, validators
                )
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
                    source_endpoint=SUI_GRAPHQL_URL,
                    fetched_at=fetched_at,
                )
                for r in validator_rows
            ]
            with db.connection() as conn:
                written = _bulk_upsert_sui_validator_rows(conn, rows)
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
