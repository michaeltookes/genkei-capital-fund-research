#!/usr/bin/env python3
"""Normalize DeFiLlama snapshots into a compact research dataset."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path("config/defillama.sources.json")
DEFAULT_RAW_DIR = Path("data/raw/defillama")
DEFAULT_OUTPUT_DIR = Path("data/normalized/defillama")
MOMENTUM_LOSS_THRESHOLD = -5.0
ZOMBIE_TVL_THRESHOLD = 10_000_000.0
ZOMBIE_WEEKLY_CHANGE_THRESHOLD = -10.0
FLOW_STRESS_THRESHOLD = -5.0
BITCOIN_GENERIC_LABELS = {"Bitcoin"}
BITCOIN_EXCLUDED_CATEGORY_KEYWORDS = (
    "cex",
    "centralized exchange",
    "custody",
    "custodian",
)
BITCOIN_EXCLUDED_NAME_KEYWORDS = (
    "binance",
    "coinbase",
    "kraken",
    "okx",
    "bybit",
    "bitfinex",
    "wbtc",
    "fbtc",
    "lbtc",
    "solvbtc",
    "wrapped",
)

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class TargetAsset:
    """Configured investable asset for the DeFiLlama research MVP."""

    symbol: str
    priority: int
    name: str
    coingecko_id: str
    primary_chain_labels: tuple[str, ...]
    ecosystem: str


def utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp for normalized artifacts."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path) -> Any:
    """Read a JSON file from disk."""
    try:
        with path.open("r", encoding="utf-8") as input_file:
            return json.load(input_file)
    except FileNotFoundError as exc:
        raise SystemExit(f"JSON file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc


def write_json(path: Path, payload: Any) -> None:
    """Write JSON with deterministic formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output_file:
        json.dump(payload, output_file, indent=2, sort_keys=True)
        output_file.write("\n")


def parse_target_assets(config: JsonObject) -> list[TargetAsset]:
    """Parse configured target assets into typed records."""
    raw_assets = config.get("target_assets", [])
    if not isinstance(raw_assets, list) or not raw_assets:
        raise SystemExit("Config must include target_assets.")

    assets = []
    for raw_asset in raw_assets:
        if not isinstance(raw_asset, dict):
            raise SystemExit("Each target asset must be an object.")
        chain_labels = raw_asset.get("primary_chain_labels", [])
        if not isinstance(chain_labels, list):
            raise SystemExit("primary_chain_labels must be a list.")
        for label in chain_labels:
            if not isinstance(label, str):
                symbol = raw_asset.get("symbol", "unknown")
                raise SystemExit(f"primary_chain_labels for target asset {symbol} must contain only strings.")
        assets.append(
            TargetAsset(
                symbol=require_string(raw_asset, "symbol"),
                priority=parse_priority(raw_asset),
                name=require_string(raw_asset, "name"),
                coingecko_id=require_string(raw_asset, "coingecko_id"),
                primary_chain_labels=tuple(label for label in chain_labels),
                ecosystem=require_string(raw_asset, "ecosystem"),
            )
        )
    return assets


def parse_priority(source: JsonObject) -> int:
    """Return a positive integer asset priority from config."""
    value = source.get("priority", 999)
    if isinstance(value, bool):
        raise SystemExit("Asset priority must be a positive integer.")
    try:
        priority = int(value)
    except (TypeError, ValueError) as exc:
        raise SystemExit("Asset priority must be a positive integer.") from exc
    if priority < 1:
        raise SystemExit("Asset priority must be a positive integer.")
    return priority


def require_string(source: JsonObject, key: str) -> str:
    """Return a required string value from a JSON object."""
    value = source.get(key)
    if not isinstance(value, str) or not value:
        raise SystemExit(f"Missing required string field: {key}")
    return value


def latest_snapshot_dir(raw_dir: Path) -> Path:
    """Find the latest raw snapshot directory containing a manifest."""
    if not raw_dir.exists() or not raw_dir.is_dir():
        raise SystemExit(f"Raw snapshots directory {raw_dir} not found")
    candidates = [path for path in raw_dir.iterdir() if path.is_dir() and (path / "manifest.json").exists()]
    if not candidates:
        raise SystemExit(f"No raw snapshot directories found in {raw_dir}")
    return sorted(candidates)[-1]


def parse_manifest_date(manifest: Any) -> str:
    """Return the UTC collection date encoded in a raw snapshot manifest."""
    if not isinstance(manifest, dict):
        raise SystemExit("Raw manifest must be a JSON object.")
    collected_at = manifest.get("collected_at")
    if not isinstance(collected_at, str) or not collected_at:
        raise SystemExit("Raw manifest must include collected_at.")
    try:
        parsed = datetime.fromisoformat(collected_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(f"Invalid raw manifest collected_at: {collected_at}") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.date().isoformat()


def load_raw_snapshot(snapshot_dir: Path) -> JsonObject:
    """Load all expected raw snapshot files from a collection directory."""
    chain_histories = {
        path.stem.removeprefix("chain_tvl_history_"): load_json(path)
        for path in snapshot_dir.glob("chain_tvl_history_*.json")
    }
    return {
        "manifest": load_json(snapshot_dir / "manifest.json"),
        "prices_current": load_json(snapshot_dir / "prices_current.json"),
        "chains": load_json(snapshot_dir / "chains.json"),
        "chain_tvl_history": chain_histories,
        "protocols": load_json(snapshot_dir / "protocols.json"),
        "stablecoins": load_json(snapshot_dir / "stablecoins.json"),
    }


def normalize_prices(prices_payload: JsonObject, assets: list[TargetAsset]) -> list[JsonObject]:
    """Normalize current price records for configured target assets only."""
    coins = prices_payload.get("coins", {})
    if not isinstance(coins, dict):
        return []

    records = []
    for asset in assets:
        coin_key = f"coingecko:{asset.coingecko_id}"
        price_record = coins.get(coin_key, {})
        if not isinstance(price_record, dict):
            price_record = {}
        records.append(
            {
                "symbol": asset.symbol,
                "priority": asset.priority,
                "name": asset.name,
                "ecosystem": asset.ecosystem,
                "price_usd": as_float(price_record.get("price")),
                "timestamp": price_record.get("timestamp"),
            }
        )
    return records


def normalize_chains(
    chains_payload: Any,
    chain_history_payloads: Mapping[str, Any],
    chain_focus: list[str],
) -> list[JsonObject]:
    """Normalize DeFiLlama chain TVL records for focused chains."""
    if not isinstance(chains_payload, list):
        return []
    chain_priority = {name: index for index, name in enumerate(chain_focus)}
    history_by_chain = normalize_chain_history_payloads(chain_history_payloads, chain_focus)
    records = []
    for chain in chains_payload:
        if not isinstance(chain, dict):
            continue
        chain_name = str(chain.get("name", ""))
        if chain_name not in chain_priority:
            continue
        tvl = as_float(chain.get("tvl"))
        historical_changes = calculate_history_changes(history_by_chain.get(chain_name, []))
        one_day = first_float(chain.get("change_1d"), historical_changes.get("change_1d_pct"))
        seven_day = first_float(chain.get("change_7d"), historical_changes.get("change_7d_pct"))
        one_month = first_float(chain.get("change_1m"), historical_changes.get("change_1m_pct"))
        records.append(
            {
                "name": chain_name,
                "tvl_usd": tvl,
                "change_1d_pct": one_day,
                "change_7d_pct": seven_day,
                "change_1m_pct": one_month,
                "momentum_label": classify_momentum(seven_day),
                "trend_label": classify_trend(one_day, seven_day, one_month),
                "zombie_risk": classify_zombie_risk(tvl, seven_day),
            }
        )
    return sorted(records, key=lambda item: chain_priority.get(str(item["name"]), len(chain_priority)))


def normalize_chain_history_payloads(
    payloads: Mapping[str, Any],
    chain_focus: list[str],
) -> dict[str, list[tuple[datetime, float]]]:
    """Normalize captured chain TVL history payloads by chain name."""
    chain_names_by_stem = {chain_history_stem(chain_name): chain_name for chain_name in chain_focus}
    histories = {}
    for safe_chain_name, payload in payloads.items():
        chain_name = chain_names_by_stem.get(safe_chain_name)
        if chain_name is None:
            continue
        records = parse_chain_history(payload)
        if records:
            histories[chain_name] = records
    return histories


def chain_history_stem(chain_name: str) -> str:
    """Return the collector filename stem suffix for a chain TVL history."""
    return "".join(character if character.isalnum() else "_" for character in chain_name.lower())


def parse_chain_history(payload: Any) -> list[tuple[datetime, float]]:
    """Parse DeFiLlama historical chain TVL records."""
    if not isinstance(payload, list):
        return []
    records = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        tvl = as_float(item.get("tvl"))
        observed_at = parse_history_timestamp(item.get("date"))
        if tvl is None or observed_at is None:
            continue
        records.append((observed_at, tvl))
    return sorted(records, key=lambda record: record[0])


def parse_history_timestamp(value: Any) -> datetime | None:
    """Parse DeFiLlama history timestamps as UTC datetimes."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def calculate_history_changes(records: list[tuple[datetime, float]]) -> JsonObject:
    """Calculate 1D, 7D, and 30D TVL change percentages from history."""
    if len(records) < 2:
        return {}
    latest_date, latest_tvl = records[-1]
    return {
        "change_1d_pct": percentage_change(latest_tvl, tvl_at_or_before(records, latest_date - timedelta(days=1))),
        "change_7d_pct": percentage_change(latest_tvl, tvl_at_or_before(records, latest_date - timedelta(days=7))),
        "change_1m_pct": percentage_change(latest_tvl, tvl_at_or_before(records, latest_date - timedelta(days=30))),
    }


def tvl_at_or_before(records: list[tuple[datetime, float]], target_date: datetime) -> float | None:
    """Return the latest TVL record at or before the requested date."""
    candidates = [tvl for observed_at, tvl in records if observed_at <= target_date]
    if not candidates:
        return None
    return candidates[-1]


def percentage_change(current_value: float, previous_value: float | None) -> float | None:
    """Return percentage change while avoiding division by zero."""
    if previous_value is None or previous_value == 0:
        return None
    return ((current_value - previous_value) / previous_value) * 100


def first_float(*values: Any) -> float | None:
    """Return the first value that can be represented as a float."""
    for value in values:
        converted = as_float(value)
        if converted is not None:
            return converted
    return None


def normalize_protocols(protocols_payload: Any, assets: list[TargetAsset]) -> list[JsonObject]:
    """Normalize protocols exposed to the target asset ecosystems."""
    if not isinstance(protocols_payload, list):
        return []
    chain_labels = {label for asset in assets for label in asset.primary_chain_labels}
    records = []
    for protocol in protocols_payload:
        if not isinstance(protocol, dict):
            continue
        chains = protocol.get("chains", [])
        if not isinstance(chains, list):
            continue
        matched_chains = sorted({str(chain) for chain in chains if str(chain) in chain_labels})
        if not matched_chains or is_cex_or_custody_protocol(protocol):
            continue
        records.append(build_protocol_record(protocol, matched_chains, "Target ecosystem"))
    return sort_protocol_records(records)


def normalize_bitcoin_ecosystem(protocols_payload: Any, labels: set[str]) -> list[JsonObject]:
    """Normalize Bitcoin-adjacent protocols under a Bitcoin ecosystem bucket."""
    records, _excluded = split_bitcoin_ecosystem_protocols(protocols_payload, labels)
    return records


def normalize_excluded_bitcoin_exposure(protocols_payload: Any, labels: set[str]) -> list[JsonObject]:
    """Return generic Bitcoin CEX/custody exposure excluded from ecosystem signal."""
    _records, excluded = split_bitcoin_ecosystem_protocols(protocols_payload, labels)
    return excluded


def split_bitcoin_ecosystem_protocols(
    protocols_payload: Any, labels: set[str]
) -> tuple[list[JsonObject], list[JsonObject]]:
    """Split Bitcoin-adjacent protocols from generic CEX/custody exposure."""
    if not isinstance(protocols_payload, list):
        return [], []
    records = []
    excluded = []
    for protocol in protocols_payload:
        if not isinstance(protocol, dict):
            continue
        chains = protocol.get("chains", [])
        if not isinstance(chains, list):
            continue
        matched_chains = sorted({str(chain) for chain in chains if str(chain) in labels})
        if not matched_chains:
            continue
        record = build_protocol_record(protocol, matched_chains, "Bitcoin ecosystem")
        if is_generic_bitcoin_cex_or_custody(protocol, matched_chains):
            record["bucket"] = "Bitcoin CEX/custody exposure (excluded)"
            record["exclusion_reason"] = (
                "Generic Bitcoin CEX/custody exposure is not Bitcoin DeFi ecosystem signal."
            )
            excluded.append(record)
            continue
        records.append(record)
    return sort_protocol_records(records), sort_protocol_records(excluded)


def is_generic_bitcoin_cex_or_custody(protocol: JsonObject, matched_chains: list[str]) -> bool:
    """Return whether a Bitcoin match should be excluded from ecosystem signal."""
    matched_label_set = set(matched_chains)
    if is_cex_or_custody_protocol(protocol):
        return True
    return not bool(matched_label_set - BITCOIN_GENERIC_LABELS) and is_custody_like_protocol(protocol)


def is_cex_or_custody_protocol(protocol: JsonObject) -> bool:
    """Return whether a protocol looks like CEX or custody exposure."""
    category = str(protocol.get("category", "")).lower()
    name = str(protocol.get("name", "")).lower()
    category_match = any(keyword in category for keyword in BITCOIN_EXCLUDED_CATEGORY_KEYWORDS)
    name_match = any(keyword in name for keyword in BITCOIN_EXCLUDED_NAME_KEYWORDS)
    return category_match or name_match


def is_custody_like_protocol(protocol: JsonObject) -> bool:
    """Return whether a protocol is custody-like by category metadata."""
    category = str(protocol.get("category", "")).lower()
    custody_keywords = ("custody", "custodian")
    return any(keyword in category for keyword in custody_keywords)


def build_protocol_record(protocol: JsonObject, matched_chains: list[str], bucket: str) -> JsonObject:
    """Build a normalized protocol exposure record."""
    return {
        "name": str(protocol.get("name", "Unknown")),
        "slug": protocol.get("slug"),
        "bucket": bucket,
        "category": protocol.get("category"),
        "matched_chains": matched_chains,
        "tvl_usd": as_float(protocol.get("tvl")),
        "change_1d_pct": as_float(protocol.get("change_1d")),
        "change_7d_pct": as_float(protocol.get("change_7d")),
        "change_1m_pct": as_float(protocol.get("change_1m")),
        "momentum_label": classify_momentum(protocol.get("change_7d")),
        "trend_label": classify_trend(
            protocol.get("change_1d"),
            protocol.get("change_7d"),
            protocol.get("change_1m"),
        ),
        "zombie_risk": classify_zombie_risk(protocol.get("tvl"), protocol.get("change_7d")),
    }


def normalize_stablecoins(stablecoins_payload: JsonObject, chain_focus: list[str]) -> list[JsonObject]:
    """Extract focused-chain stablecoin supply where DeFiLlama exposes chain data."""
    pegged_assets = stablecoins_payload.get("peggedAssets", [])
    if not isinstance(pegged_assets, list):
        return []
    totals: dict[str, float] = {chain: 0.0 for chain in chain_focus}
    for asset in pegged_assets:
        if not isinstance(asset, dict):
            continue
        chain_balances = asset.get("chainBalances", {})
        if not isinstance(chain_balances, dict):
            continue
        for chain_name in chain_focus:
            totals[chain_name] += stablecoin_chain_value(chain_balances.get(chain_name))
    total_supply = sum(totals.values())
    records = []
    for chain_name in chain_focus:
        value = totals[chain_name]
        if value <= 0:
            continue
        share = (value / total_supply) * 100 if total_supply > 0 else None
        records.append(
            {
                "chain": chain_name,
                "stablecoin_supply_usd": round(value, 2),
                "focus_supply_share_pct": round(share, 2) if share is not None else None,
                "money_flow_label": classify_money_flow(value),
            }
        )
    return records


def stablecoin_chain_value(value: Any) -> float:
    """Return a USD stablecoin value from a DeFiLlama chain balance record."""
    if isinstance(value, dict):
        for key in ("current", "circulating", "peggedUSD"):
            nested_value = as_float(value.get(key))
            if nested_value is not None:
                return nested_value
    direct_value = as_float(value)
    return direct_value or 0.0


def classify_momentum(change_7d: Any) -> str:
    """Classify short-term momentum from seven-day TVL change."""
    change = as_float(change_7d)
    if change is None:
        return "unknown"
    if change <= MOMENTUM_LOSS_THRESHOLD:
        return "momentum loss"
    if change < 0:
        return "softening"
    return "expanding"


def classify_trend(change_1d: Any, change_7d: Any, change_1m: Any) -> str:
    """Classify short-vs-medium TVL trend changes for analyst context."""
    one_day = as_float(change_1d)
    seven_day = as_float(change_7d)
    month = as_float(change_1m)
    if seven_day is None or month is None:
        return "unknown"
    if one_day is not None and one_day <= FLOW_STRESS_THRESHOLD and seven_day < 0:
        return "acute outflow pressure"
    if seven_day >= 0 and month < 0:
        return "reversal attempt"
    if seven_day < 0 and month >= 0:
        return "short-term deterioration"
    if seven_day > 0 and month > 0:
        return "confirmed uptrend"
    if seven_day < 0 and month < 0:
        return "confirmed downtrend"
    return "mixed"


def classify_money_flow(stablecoin_supply_usd: Any) -> str:
    """Classify stablecoin supply as money-flow context, not a trade signal."""
    supply = as_float(stablecoin_supply_usd)
    if supply is None or supply <= 0:
        return "unavailable"
    if supply >= 1_000_000_000:
        return "deep stablecoin liquidity"
    if supply >= 100_000_000:
        return "usable stablecoin liquidity"
    return "thin stablecoin liquidity"


def build_data_quality(stablecoin_flows: list[JsonObject], chain_focus: list[str]) -> JsonObject:
    """Build report-quality guardrails for incomplete external data."""
    stablecoin_chains = {str(flow.get("chain")) for flow in stablecoin_flows}
    missing_stablecoin_chains = [chain for chain in chain_focus if chain not in stablecoin_chains]
    stablecoin_status = "available" if not missing_stablecoin_chains else "partial"
    if not stablecoin_flows:
        stablecoin_status = "unavailable"
    completeness_notes = []
    if stablecoin_status != "available":
        completeness_notes.append(
            "Stablecoin chain data is incomplete; money-flow and DCA wording must stay caveated."
        )
    return {
        "stablecoin_chain_data": stablecoin_status,
        "missing_stablecoin_chains": missing_stablecoin_chains,
        "completeness_notes": completeness_notes,
    }


def classify_zombie_risk(tvl: Any, change_7d: Any) -> str:
    """Classify early zombie-chain or dead-liquidity risk from TVL and momentum."""
    tvl_value = as_float(tvl)
    weekly_change = as_float(change_7d)
    if tvl_value is None or weekly_change is None:
        return "unknown"
    if tvl_value < ZOMBIE_TVL_THRESHOLD and weekly_change <= ZOMBIE_WEEKLY_CHANGE_THRESHOLD:
        return "elevated"
    if weekly_change <= ZOMBIE_WEEKLY_CHANGE_THRESHOLD:
        return "watch"
    return "normal"


def sort_protocol_records(records: list[JsonObject]) -> list[JsonObject]:
    """Sort protocol records by TVL descending with nulls last."""
    return sorted(records, key=lambda item: item.get("tvl_usd") or 0.0, reverse=True)


def as_float(value: Any) -> float | None:
    """Convert numeric API values to float while preserving missing values."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def validate_list_of_strings(value: Any, field_name: str) -> list[str]:
    """Validate that a config field is an iterable container of strings."""
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or isinstance(value, Mapping) or not isinstance(value, Iterable):
        raise TypeError(f"{field_name} must be a list, tuple, or set of strings.")
    strings = []
    for item in value:
        if not isinstance(item, str):
            raise TypeError(f"{field_name} must contain only strings.")
        strings.append(item)
    return strings


def build_priority_order(assets: list[TargetAsset]) -> list[str]:
    """Build configured asset priority labels for normalized scope metadata."""
    symbols_by_priority: dict[int, list[str]] = {}
    for asset in assets:
        symbols_by_priority.setdefault(asset.priority, []).append(asset.symbol)
    return [
        f"{priority} {' + '.join(symbols_by_priority[priority])}"
        for priority in sorted(symbols_by_priority)
    ]


def normalize_snapshot(config_path: Path, raw_dir: Path, output_dir: Path) -> Path:
    """Normalize the latest raw snapshot into a daily JSON artifact."""
    config = load_json(config_path)
    if not isinstance(config, dict):
        raise SystemExit("Config root must be a JSON object.")
    snapshot_dir = latest_snapshot_dir(raw_dir)
    raw_snapshot = load_raw_snapshot(snapshot_dir)
    assets = parse_target_assets(config)
    try:
        chain_focus = validate_list_of_strings(config.get("chain_focus", []), "chain_focus")
        bitcoin_labels = {
            chain
            for chain in validate_list_of_strings(
                config.get("bitcoin_ecosystem_labels", []),
                "bitcoin_ecosystem_labels",
            )
        }
    except TypeError as exc:
        raise SystemExit(f"Invalid config: {exc}") from exc
    snapshot_date = parse_manifest_date(raw_snapshot["manifest"])
    bitcoin_ecosystem, bitcoin_excluded_exposure = split_bitcoin_ecosystem_protocols(
        raw_snapshot["protocols"],
        bitcoin_labels,
    )

    normalized = {
        "schema_version": "1.0",
        "generated_at": utc_now_iso(),
        "snapshot_date": snapshot_date,
        "raw_snapshot": str(snapshot_dir),
        "scope": {
            "target_assets": [asset.symbol for asset in sorted(assets, key=lambda asset: asset.priority)],
            "priority_order": build_priority_order(assets),
            "non_target_assets_policy": "ignored unless used as ecosystem context",
        },
        "asset_prices": normalize_prices(raw_snapshot["prices_current"], assets),
        "chain_tvl": normalize_chains(
            raw_snapshot["chains"],
            raw_snapshot["chain_tvl_history"],
            chain_focus,
        ),
        "stablecoin_flows": normalize_stablecoins(raw_snapshot["stablecoins"], chain_focus),
        "protocol_exposure": normalize_protocols(raw_snapshot["protocols"], assets),
        "bitcoin_ecosystem": bitcoin_ecosystem,
        "bitcoin_excluded_exposure": bitcoin_excluded_exposure,
    }
    normalized["data_quality"] = build_data_quality(normalized["stablecoin_flows"], chain_focus)
    output_path = output_dir / f"daily-{snapshot_date}.json"
    write_json(output_path, normalized)
    return output_path


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Normalize DeFiLlama raw snapshots.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the normalizer CLI."""
    args = parse_args(argv or sys.argv[1:])
    output_path = normalize_snapshot(args.config, args.raw_dir, args.output_dir)
    print(f"Wrote normalized DeFiLlama dataset: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
