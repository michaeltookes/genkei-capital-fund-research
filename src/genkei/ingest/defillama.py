"""DeFiLlama collector — fetches public payloads and lands them in Postgres.

Replaces the file-based ``scripts/collect_defillama.py`` (B-017 + B-013).
Each run records a row in ``meta.ingest_runs`` and inserts one
``meta.raw_blobs`` row per endpoint. The downstream normalizer
(``genkei.normalize.defillama``) reads from ``meta.raw_blobs`` rather
than re-hitting the upstream API — replay is a single SQL query.

Endpoints collected (driven by ``config/defillama.sources.json``):

  - ``prices_current``               coins.llama.fi /prices/current
  - ``protocols``                    api.llama.fi /protocols
  - ``chains``                       api.llama.fi /v2/chains (optional; not normalized)
  - ``stablecoins``                  stablecoins.llama.fi /stablecoins
  - ``chain_tvl_history_<chain>``    one per ``chain_focus`` entry

Required normalized endpoints raise on failure and the run is marked
``failed``. Optional endpoints such as ``chains`` and per-chain history are
logged and recorded in run metadata under ``partial_endpoints`` so the run
still completes.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from genkei.common import db
from genkei.common.http import HttpClient, RateLimit

DEFAULT_CONFIG_PATH = Path("config/defillama.sources.json")
SOURCE_NAME = "defillama"
COLLECT_ENDPOINT_LABEL = "collect"
RAW_BLOBS_INSERT = (
    "INSERT INTO meta.raw_blobs (ingest_run_id, endpoint_name, url, payload) "
    "VALUES (%s, %s, %s, %s::jsonb) "
    "ON CONFLICT (ingest_run_id, endpoint_name) DO NOTHING"
)
JsonObject = dict[str, Any]
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CollectionTarget:
    """A DeFiLlama endpoint to fetch."""

    name: str
    url: str
    required: bool = True


def load_config(path: Path) -> JsonObject:
    """Load the collector configuration from disk."""
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise SystemExit(f"Config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Config file is not valid JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("Config root must be a JSON object.")
    return data


def read_base_urls(config: JsonObject) -> dict[str, str]:
    """Return configured DeFiLlama base URLs with trailing slashes stripped."""
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


def chain_history_target_name(chain_name: str) -> str:
    """Normalizer-friendly name stem for a chain TVL history payload."""
    safe = "".join(ch if ch.isalnum() else "_" for ch in chain_name.lower())
    return f"chain_tvl_history_{safe}"


def build_price_target(config: JsonObject) -> CollectionTarget:
    """Build the bulk price URL for the configured target assets."""
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
    joined = quote(",".join(coin_keys), safe=":,")
    return CollectionTarget("prices_current", f"{base_urls['coins']}/prices/current/{joined}")


def build_chain_history_targets(config: JsonObject) -> list[CollectionTarget]:
    """Build per-chain TVL history URLs for the focus chain set."""
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
        encoded = quote(chain_name, safe="")
        targets.append(
            CollectionTarget(
                chain_history_target_name(chain_name),
                f"{base_urls['core']}/v2/historicalChainTvl/{encoded}",
                required=False,
            )
        )
    return targets


def build_collection_targets(config: JsonObject) -> list[CollectionTarget]:
    """Build the full ordered set of endpoints the collector hits."""
    base_urls = read_base_urls(config)
    endpoints = config.get("collection_endpoints", [])
    if not isinstance(endpoints, list):
        raise SystemExit("collection_endpoints must be a list.")

    config_endpoint_names: set[str] = set()
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            raise SystemExit("Each collection endpoint must be an object.")
        config_endpoint_names.add(require_string(endpoint, "name"))
    missing = {"protocols", "stablecoins"} - config_endpoint_names
    if missing:
        raise SystemExit(f"Missing required collection endpoints: {', '.join(sorted(missing))}")

    targets: list[CollectionTarget] = [build_price_target(config)]
    targets.extend(build_chain_history_targets(config))
    seen = {target.name for target in targets}
    for endpoint in endpoints:
        name = require_string(endpoint, "name")
        if name in seen:
            raise SystemExit(f"Duplicate collection endpoint name: {name}")
        base_key = require_string(endpoint, "base")
        path = require_string(endpoint, "path")
        if base_key not in base_urls:
            raise SystemExit(f"Unknown base URL key for endpoint {name}: {base_key}")
        seen.add(name)
        targets.append(
            CollectionTarget(name, f"{base_urls[base_key]}{path}", required=name != "chains")
        )
    return targets


def _store_blob(ingest_run_id: int, target: CollectionTarget, payload: Any) -> None:
    """Insert one ``meta.raw_blobs`` row for a successful endpoint fetch."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            RAW_BLOBS_INSERT,
            [ingest_run_id, target.name, target.url, json.dumps(payload)],
        )


def collect(config_path: Path, *, http: HttpClient | None = None) -> int:
    """Run the collector once and return the ``meta.ingest_runs`` row id.

    ``http`` is injectable for testing; the production path constructs a
    rate-limited :class:`HttpClient`.
    """
    config = load_config(config_path)
    targets = build_collection_targets(config)

    owns_http = http is None
    if http is None:
        http = HttpClient(SOURCE_NAME, rate_limit=RateLimit.per_second(5))

    partial: list[dict[str, str]] = []
    try:
        with db.ingest_run(
            SOURCE_NAME,
            endpoint=COLLECT_ENDPOINT_LABEL,
            metadata={"config_path": str(config_path)},
        ) as run:
            for target in targets:
                try:
                    payload = http.get_json(target.url)
                except Exception as exc:
                    if target.required:
                        raise
                    LOGGER.warning("partial data for %s: %s", target.name, exc)
                    partial.append({"name": target.name, "url": target.url, "error": str(exc)})
                    continue
                _store_blob(run.id, target, payload)
                run.add_rows(1)

            if partial:
                _record_partial(run.id, partial)
            return run.id
    finally:
        if owns_http:
            http.close()


def _record_partial(ingest_run_id: int, partial: list[dict[str, str]]) -> None:
    """Stash per-endpoint partial-failure metadata on the ingest_runs row."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE meta.ingest_runs SET metadata = "
            "COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('partial_endpoints', %s::jsonb) "
            "WHERE id = %s",
            [json.dumps(partial), ingest_run_id],
        )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect DeFiLlama public API snapshots into Postgres."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv or sys.argv[1:])
    run_id = collect(args.config)
    print(f"DeFiLlama collector wrote ingest_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
