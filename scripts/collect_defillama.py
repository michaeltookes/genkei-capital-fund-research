#!/usr/bin/env python3
"""Collect raw public DeFiLlama API snapshots for the research MVP."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

DEFAULT_CONFIG_PATH = Path("config/defillama.sources.json")
DEFAULT_OUTPUT_DIR = Path("data/raw/defillama")
REQUEST_TIMEOUT_SECONDS = 30
USER_AGENT = "genkei-capital-fund-research/0.1"
ALLOWED_URL_SCHEMES = {"https", "http"}

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class CollectionTarget:
    """A DeFiLlama endpoint to capture as raw JSON."""

    name: str
    url: str
    required: bool = True


def utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp suitable for artifact metadata."""
    return datetime.now(timezone.utc).isoformat()


def build_run_id(timestamp: str) -> str:
    """Build a filesystem-safe snapshot run identifier from a timestamp."""
    safe_timestamp = timestamp.replace(":", "").replace("+", "Z")
    return f"{safe_timestamp}-{uuid4().hex[:8]}"


def load_config(path: Path) -> JsonObject:
    """Load collector configuration from disk."""
    try:
        with path.open("r", encoding="utf-8") as config_file:
            data = json.load(config_file)
    except FileNotFoundError as exc:
        raise SystemExit(f"Config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Config file is not valid JSON: {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise SystemExit("Config root must be a JSON object.")
    return data


def build_price_target(config: JsonObject) -> CollectionTarget:
    """Build the DeFiLlama current-price URL for configured target assets."""
    assets = config.get("target_assets", [])
    if not isinstance(assets, list) or not assets:
        raise SystemExit("Config must define at least one target asset.")

    coin_keys = []
    for asset in assets:
        if not isinstance(asset, dict) or not asset.get("coingecko_id"):
            raise SystemExit("Each target asset must include coingecko_id.")
        coin_keys.append(f"coingecko:{asset['coingecko_id']}")

    base_urls = read_base_urls(config)
    if "coins" not in base_urls:
        raise SystemExit("defillama_base_urls.coins is missing")
    joined_keys = quote(",".join(coin_keys), safe=":,")
    return CollectionTarget("prices_current", f"{base_urls['coins']}/prices/current/{joined_keys}")


def build_collection_targets(config: JsonObject) -> list[CollectionTarget]:
    """Build all configured API collection targets."""
    base_urls = read_base_urls(config)
    endpoints = config.get("collection_endpoints", [])
    if not isinstance(endpoints, list):
        raise SystemExit("collection_endpoints must be a list.")

    targets = [build_price_target(config)]
    targets.extend(build_chain_history_targets(config))
    seen_names = set()
    for target in targets:
        if target.name in seen_names:
            raise SystemExit(f"Duplicate collection endpoint name: {target.name}")
        seen_names.add(target.name)
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            raise SystemExit("Each collection endpoint must be an object.")
        name = require_string(endpoint, "name")
        if name in seen_names:
            raise SystemExit(f"Duplicate collection endpoint name: {name}")
        base_key = require_string(endpoint, "base")
        path = require_string(endpoint, "path")
        if base_key not in base_urls:
            raise SystemExit(f"Unknown base URL key for endpoint {name}: {base_key}")
        seen_names.add(name)
        targets.append(CollectionTarget(name, f"{base_urls[base_key]}{path}"))
    return targets


def build_chain_history_targets(config: JsonObject) -> list[CollectionTarget]:
    """Build historical TVL URLs for configured focus chains."""
    base_urls = read_base_urls(config)
    if "core" not in base_urls:
        raise SystemExit("defillama_base_urls.core is missing")
    chain_focus = config.get("chain_focus", [])
    if not isinstance(chain_focus, list):
        raise SystemExit("chain_focus must be a list.")
    targets = []
    for chain_name in chain_focus:
        if not isinstance(chain_name, str) or not chain_name:
            raise SystemExit("chain_focus must contain only non-empty strings.")
        encoded_chain = quote(chain_name, safe="")
        targets.append(
            CollectionTarget(
                chain_history_target_name(chain_name),
                f"{base_urls['core']}/v2/historicalChainTvl/{encoded_chain}",
                required=False,
            )
        )
    return targets


def chain_history_target_name(chain_name: str) -> str:
    """Return a stable raw snapshot filename stem for a chain TVL history."""
    safe_name = "".join(character if character.isalnum() else "_" for character in chain_name.lower())
    return f"chain_tvl_history_{safe_name}"


def read_base_urls(config: JsonObject) -> dict[str, str]:
    """Read configured DeFiLlama base URLs."""
    base_urls = config.get("defillama_base_urls")
    if not isinstance(base_urls, dict):
        raise SystemExit("defillama_base_urls must be an object.")
    return {str(key): str(value).rstrip("/") for key, value in base_urls.items()}


def require_string(source: JsonObject, key: str) -> str:
    """Return a required string field from a JSON object."""
    value = source.get(key)
    if not isinstance(value, str) or not value:
        raise SystemExit(f"Missing required string field: {key}")
    return value


def fetch_json(url: str) -> Any:
    """Fetch JSON from a public API endpoint."""
    parsed_url = urlparse(url)
    if parsed_url.scheme not in ALLOWED_URL_SCHEMES:
        scheme = parsed_url.scheme or "missing"
        raise RuntimeError(f"Unsupported URL scheme for {url}: {scheme}")
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} while fetching {url}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error while fetching {url}: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON returned by {url}: {exc}") from exc


def write_json(path: Path, payload: Any) -> None:
    """Write a JSON payload with stable formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output_file:
        json.dump(payload, output_file, indent=2, sort_keys=True)
        output_file.write("\n")


def collect_snapshots(config_path: Path, output_dir: Path) -> Path:
    """Collect configured API snapshots and return the manifest path."""
    config = load_config(config_path)
    timestamp = utc_now_iso()
    run_id = build_run_id(timestamp)
    target_dir = output_dir / run_id
    manifest_entries = []

    for target in build_collection_targets(config):
        file_path = target_dir / f"{target.name}.json"
        try:
            payload = fetch_json(target.url)
            status = "ok"
        except RuntimeError as exc:
            if target.required:
                raise
            print(f"Warning: partial data for {target.name}: {exc}", file=sys.stderr)
            payload = {"partial": True, "error": str(exc), "url": target.url}
            status = "partial"
        write_json(file_path, payload)
        manifest_entries.append(
            {"name": target.name, "url": target.url, "path": str(file_path), "status": status}
        )

    manifest = {
        "schema_version": "1.0",
        "collected_at": timestamp,
        "source_config": str(config_path),
        "entries": manifest_entries,
    }
    manifest_path = target_dir / "manifest.json"
    write_json(manifest_path, manifest)
    return manifest_path


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Collect raw DeFiLlama public API snapshots.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the collector CLI."""
    args = parse_args(argv or sys.argv[1:])
    manifest_path = collect_snapshots(args.config, args.output_dir)
    print(f"Wrote DeFiLlama raw manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
