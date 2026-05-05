#!/usr/bin/env python3
"""Normalize DeFiLlama snapshots into a compact research dataset."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path("config/defillama.sources.json")
DEFAULT_RAW_DIR = Path("data/raw/defillama")
DEFAULT_OUTPUT_DIR = Path("data/normalized/defillama")
MOMENTUM_LOSS_THRESHOLD = -5.0
ZOMBIE_TVL_THRESHOLD = 10_000_000.0
ZOMBIE_WEEKLY_CHANGE_THRESHOLD = -10.0

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class TargetAsset:
    """Configured investable asset for the DeFiLlama research MVP."""

    symbol: str
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
        assets.append(
            TargetAsset(
                symbol=require_string(raw_asset, "symbol"),
                name=require_string(raw_asset, "name"),
                coingecko_id=require_string(raw_asset, "coingecko_id"),
                primary_chain_labels=tuple(str(label) for label in chain_labels),
                ecosystem=require_string(raw_asset, "ecosystem"),
            )
        )
    return assets


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
    return {
        "manifest": load_json(snapshot_dir / "manifest.json"),
        "prices_current": load_json(snapshot_dir / "prices_current.json"),
        "chains": load_json(snapshot_dir / "chains.json"),
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
                "name": asset.name,
                "ecosystem": asset.ecosystem,
                "price_usd": as_float(price_record.get("price")),
                "timestamp": price_record.get("timestamp"),
            }
        )
    return records


def normalize_chains(chains_payload: Any, chain_focus: set[str]) -> list[JsonObject]:
    """Normalize DeFiLlama chain TVL records for focused chains."""
    if not isinstance(chains_payload, list):
        return []
    records = []
    for chain in chains_payload:
        if not isinstance(chain, dict):
            continue
        chain_name = str(chain.get("name", ""))
        if chain_name not in chain_focus:
            continue
        records.append(
            {
                "name": chain_name,
                "tvl_usd": as_float(chain.get("tvl")),
                "change_1d_pct": as_float(chain.get("change_1d")),
                "change_7d_pct": as_float(chain.get("change_7d")),
                "change_1m_pct": as_float(chain.get("change_1m")),
                "momentum_label": classify_momentum(chain.get("change_7d")),
                "zombie_risk": classify_zombie_risk(chain.get("tvl"), chain.get("change_7d")),
            }
        )
    return sorted(records, key=lambda item: item["name"])


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
        if not matched_chains:
            continue
        records.append(build_protocol_record(protocol, matched_chains, "Target ecosystem"))
    return sort_protocol_records(records)


def normalize_bitcoin_ecosystem(protocols_payload: Any, labels: set[str]) -> list[JsonObject]:
    """Normalize Bitcoin-adjacent protocols under a Bitcoin ecosystem bucket."""
    if not isinstance(protocols_payload, list):
        return []
    records = []
    for protocol in protocols_payload:
        if not isinstance(protocol, dict):
            continue
        chains = protocol.get("chains", [])
        if not isinstance(chains, list):
            continue
        matched_chains = sorted({str(chain) for chain in chains if str(chain) in labels})
        if matched_chains:
            records.append(build_protocol_record(protocol, matched_chains, "Bitcoin ecosystem"))
    return sort_protocol_records(records)


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
        "zombie_risk": classify_zombie_risk(protocol.get("tvl"), protocol.get("change_7d")),
    }


def normalize_stablecoins(stablecoins_payload: JsonObject, chain_focus: set[str]) -> list[JsonObject]:
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
    return [
        {"chain": chain_name, "stablecoin_supply_usd": round(value, 2)}
        for chain_name, value in sorted(totals.items())
        if value > 0
    ]


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


def normalize_snapshot(config_path: Path, raw_dir: Path, output_dir: Path) -> Path:
    """Normalize the latest raw snapshot into a daily JSON artifact."""
    config = load_json(config_path)
    if not isinstance(config, dict):
        raise SystemExit("Config root must be a JSON object.")
    snapshot_dir = latest_snapshot_dir(raw_dir)
    raw_snapshot = load_raw_snapshot(snapshot_dir)
    assets = parse_target_assets(config)
    chain_focus = set(validate_list_of_strings(config.get("chain_focus", []), "chain_focus"))
    bitcoin_labels = {
        chain
        for chain in validate_list_of_strings(
            config.get("bitcoin_ecosystem_labels", []),
            "bitcoin_ecosystem_labels",
        )
    }
    snapshot_date = parse_manifest_date(raw_snapshot["manifest"])

    normalized = {
        "schema_version": "1.0",
        "generated_at": utc_now_iso(),
        "snapshot_date": snapshot_date,
        "raw_snapshot": str(snapshot_dir),
        "scope": {
            "target_assets": [asset.symbol for asset in assets],
            "non_target_assets_policy": "ignored unless used as ecosystem context",
        },
        "asset_prices": normalize_prices(raw_snapshot["prices_current"], assets),
        "chain_tvl": normalize_chains(raw_snapshot["chains"], chain_focus),
        "stablecoin_flows": normalize_stablecoins(raw_snapshot["stablecoins"], chain_focus),
        "protocol_exposure": normalize_protocols(raw_snapshot["protocols"], assets),
        "bitcoin_ecosystem": normalize_bitcoin_ecosystem(raw_snapshot["protocols"], bitcoin_labels),
    }
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
