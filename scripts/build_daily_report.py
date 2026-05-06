#!/usr/bin/env python3
"""Build an analyst-style daily market brief from normalized DeFiLlama data."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_NORMALIZED_DIR = Path("data/normalized/defillama")
DEFAULT_OUTPUT_DIR = Path("reports/daily")
TOP_PROTOCOL_LIMIT = 8
TOP_BITCOIN_ECOSYSTEM_LIMIT = 10
TOP_EXCLUDED_BITCOIN_LIMIT = 5

JsonObject = dict[str, Any]


def load_json(path: Path) -> Any:
    """Load JSON from disk."""
    try:
        with path.open("r", encoding="utf-8") as input_file:
            return json.load(input_file)
    except FileNotFoundError as exc:
        raise SystemExit(f"Normalized file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc


def latest_normalized_file(normalized_dir: Path) -> Path:
    """Return the latest normalized daily JSON file."""
    candidates = sorted(normalized_dir.glob("daily-*.json"))
    if not candidates:
        raise SystemExit(f"No normalized daily files found in {normalized_dir}")
    return candidates[-1]


def parse_iso_date(value: Any, context: str) -> date:
    """Parse an ISO date or datetime value into a date."""
    if not isinstance(value, str) or not value:
        raise SystemExit(f"Missing normalized date in {context}.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError as exc:
            raise SystemExit(f"Invalid normalized date in {context}: {value}") from exc


def normalized_date_stamp(data: JsonObject, normalized_path: Path) -> str:
    """Return the dataset date for a normalized artifact."""
    if "snapshot_date" in data:
        return parse_iso_date(data["snapshot_date"], "snapshot_date").isoformat()

    stem = normalized_path.stem
    if stem.startswith("daily-"):
        return parse_iso_date(stem.removeprefix("daily-"), str(normalized_path)).isoformat()

    if "generated_at" in data:
        return parse_iso_date(data["generated_at"], "generated_at").isoformat()

    raise SystemExit(f"Cannot determine normalized date for {normalized_path}")


def format_usd(value: Any) -> str:
    """Format a numeric value as compact USD."""
    number = as_float(value)
    if number is None:
        return "n/a"
    absolute = abs(number)
    if absolute >= 1_000_000_000:
        return f"${number / 1_000_000_000:.2f}B"
    if absolute >= 1_000_000:
        return f"${number / 1_000_000:.2f}M"
    if absolute >= 1_000:
        return f"${number / 1_000:.2f}K"
    return f"${number:.2f}"


def format_pct(value: Any) -> str:
    """Format a numeric value as a percentage."""
    number = as_float(value)
    if number is None:
        return "n/a"
    return f"{number:+.2f}%"


def as_float(value: Any) -> float | None:
    """Convert a JSON value to float if it is numeric."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_asset_table(data: JsonObject) -> list[str]:
    """Build the target asset table lines."""
    lines = ["| Asset | Ecosystem | Price |", "| --- | --- | ---: |"]
    for asset in data.get("asset_prices", []):
        lines.append(
            "| {symbol} | {ecosystem} | {price} |".format(
                symbol=asset.get("symbol", "n/a"),
                ecosystem=asset.get("ecosystem", "n/a"),
                price=format_usd(asset.get("price_usd")),
            )
        )
    return lines


def build_chain_table(data: JsonObject) -> list[str]:
    """Build the chain TVL and momentum table lines."""
    lines = [
        "| Chain | TVL | 1D | 7D | 1M | Momentum | Trend | Zombie risk |",
        "| --- | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for chain in data.get("chain_tvl", []):
        lines.append(
            "| {name} | {tvl} | {one_day} | {seven_day} | {month} | {momentum} | {trend} | {risk} |".format(
                name=chain.get("name", "n/a"),
                tvl=format_usd(chain.get("tvl_usd")),
                one_day=format_pct(chain.get("change_1d_pct")),
                seven_day=format_pct(chain.get("change_7d_pct")),
                month=format_pct(chain.get("change_1m_pct")),
                momentum=chain.get("momentum_label", "unknown"),
                trend=chain.get("trend_label", "unknown"),
                risk=chain.get("zombie_risk", "unknown"),
            )
        )
    return lines


def build_stablecoin_section(data: JsonObject) -> list[str]:
    """Build stablecoin money-flow context lines with quality guardrails."""
    flows = data.get("stablecoin_flows", [])
    quality = data.get("data_quality", {})
    status = (
        quality.get("stablecoin_chain_data", "unknown")
        if isinstance(quality, dict)
        else "unknown"
    )
    lines = [f"- Stablecoin chain data status: **{status}**."]
    if not flows:
        lines.append("- Stablecoin chain data unavailable in this snapshot; do not infer money-flow direction.")
        return lines
    for flow in flows:
        lines.append(
            "- {chain}: {supply} stablecoin supply ({share} of focused-chain supply); {label}.".format(
                chain=flow.get("chain", "n/a"),
                supply=format_usd(flow.get("stablecoin_supply_usd")),
                share=format_pct(flow.get("focus_supply_share_pct")),
                label=flow.get("money_flow_label", "flow label unavailable"),
            )
        )
    missing = quality.get("missing_stablecoin_chains", []) if isinstance(quality, dict) else []
    if missing:
        missing_chains = ", ".join(str(chain) for chain in missing)
        lines.append(f"- Missing focused-chain stablecoin coverage: {missing_chains}.")
    return lines


def build_risk_section(data: JsonObject) -> list[str]:
    """Build momentum-loss and zombie-risk observations."""
    watched = [chain for chain in data.get("chain_tvl", []) if chain.get("zombie_risk") != "normal"]
    if not watched:
        return ["- No focused chain is currently flagged above normal zombie-risk thresholds."]
    return [
        "- {name}: {risk} risk; 7D TVL {change}; momentum label `{momentum}`.".format(
            name=chain.get("name", "n/a"),
            risk=chain.get("zombie_risk", "unknown"),
            change=format_pct(chain.get("change_7d_pct")),
            momentum=chain.get("momentum_label", "unknown"),
        )
        for chain in watched
    ]


def build_protocol_lines(protocols: list[JsonObject], limit: int) -> list[str]:
    """Build protocol exposure bullet lines."""
    if not protocols:
        return ["- No matching protocol exposure found in this snapshot."]
    lines = []
    for protocol in protocols[:limit]:
        matched_chains = protocol.get("matched_chains")
        if (
            matched_chains is None
            or isinstance(matched_chains, (str, bytes))
            or not hasattr(matched_chains, "__iter__")
        ):
            matched_chains = []
        chains = ", ".join(str(chain) for chain in matched_chains if chain is not None) or "n/a"
        lines.append(
            "- {name} ({chains}): TVL {tvl}; 7D {change}; {momentum}.".format(
                name=protocol.get("name", "n/a"),
                chains=chains,
                tvl=format_usd(protocol.get("tvl_usd")),
                change=format_pct(protocol.get("change_7d_pct")),
                momentum=protocol.get("momentum_label", "unknown"),
            )
        )
    return lines


def build_dca_timing_notes(data: JsonObject) -> list[str]:
    """Build DCA timing support notes from TVL momentum and data completeness."""
    expanding = [
        chain for chain in data.get("chain_tvl", []) if chain.get("momentum_label") == "expanding"
    ]
    losing = [
        chain for chain in data.get("chain_tvl", []) if chain.get("momentum_label") == "momentum loss"
    ]
    acute = [
        chain
        for chain in data.get("chain_tvl", [])
        if chain.get("trend_label") == "acute outflow pressure"
    ]
    stablecoin_status = stablecoin_quality_status(data)
    label = classify_dca_signal(expanding, losing, acute, stablecoin_status)
    notes = [f"- Signal label: **{label}**."]
    if expanding:
        names = ", ".join(chain.get("name", "n/a") for chain in expanding)
        notes.append(
            f"- Constructive: TVL momentum is expanding for {names}; "
            "use as context, not an auto-buy rule."
        )
    if losing:
        names = ", ".join(chain.get("name", "n/a") for chain in losing)
        notes.append(
            "- Caution: TVL momentum loss argues against increasing allocation "
            f"without confirmation: {names}."
        )
    if acute:
        names = ", ".join(chain.get("name", "n/a") for chain in acute)
        notes.append(f"- Wait-for-confirmation: acute outflow pressure is present on {names}.")
    if stablecoin_status != "available":
        notes.append("- Data caveat: stablecoin coverage is incomplete, so money-flow conviction is capped.")
    if len(notes) == 1:
        notes.append("- Neutral: TVL alone does not show a decisive DCA timing edge today.")
    notes.append("- Research signal only; this is not financial advice or a trade recommendation.")
    return notes


def stablecoin_quality_status(data: JsonObject) -> str:
    """Return stablecoin data quality status from normalized metadata."""
    quality = data.get("data_quality", {})
    if isinstance(quality, dict):
        status = quality.get("stablecoin_chain_data")
        if isinstance(status, str) and status:
            return status
    return "unknown"


def classify_dca_signal(
    expanding: list[JsonObject],
    losing: list[JsonObject],
    acute: list[JsonObject],
    stablecoin_status: str,
) -> str:
    """Classify DCA context without turning it into financial advice."""
    if acute or losing:
        return "caution"
    if expanding and stablecoin_status == "available":
        return "constructive"
    return "neutral"


def build_excluded_bitcoin_section(data: JsonObject) -> list[str]:
    """Build caveated Bitcoin exposure lines excluded from ecosystem signal."""
    excluded = data.get("bitcoin_excluded_exposure", [])
    if not excluded:
        return ["- No generic Bitcoin CEX/custody exposure was separated in this snapshot."]
    lines = [
        "- These records are separated from Bitcoin ecosystem signal because CEX/custody "
        "TVL can reflect custody or venue activity, not Bitcoin-native DeFi demand."
    ]
    lines.extend(build_protocol_lines(excluded, TOP_EXCLUDED_BITCOIN_LIMIT))
    return lines


def focused_assets_text(data: JsonObject) -> str:
    """Return a display string for the report's focused target assets."""
    scope = data.get("scope", {})
    target_assets = scope.get("target_assets") if isinstance(scope, dict) else None
    if isinstance(target_assets, list) and target_assets:
        return ", ".join(str(asset) for asset in target_assets)
    return "BTC, ETH, SOL, LINK, SUI"


def build_report(data: JsonObject) -> str:
    """Build the complete analyst-style market brief."""
    generated_at = data.get("generated_at", datetime.now(timezone.utc).isoformat())
    focused_assets = focused_assets_text(data)
    lines = [
        "# DeFiLlama Daily Market Brief",
        "",
        f"Generated: {generated_at}",
        "",
        "## Scope",
        "",
        f"Focused assets: {focused_assets}. Non-target assets are ignored unless needed ",
        "as ecosystem context. This brief is DeFiLlama-only and intentionally avoids ",
        "Twitter-only sentiment.",
        "",
        "## Target asset prices",
        "",
        *build_asset_table(data),
        "",
        "## Chain liquidity and momentum",
        "",
        *build_chain_table(data),
        "",
        "## Money-flow context",
        "",
        *build_stablecoin_section(data),
        "",
        "## DCA timing support",
        "",
        *build_dca_timing_notes(data),
        "",
        "## Zombie-chain / momentum-loss watch",
        "",
        *build_risk_section(data),
        "",
        "## Target ecosystem protocol exposure",
        "",
        *build_protocol_lines(data.get("protocol_exposure", []), TOP_PROTOCOL_LIMIT),
        "",
        "## Bitcoin ecosystem",
        "",
        "Bitcoin-adjacent chains/projects are grouped here when DeFiLlama exposes labels such ",
        "as Lightning, Stacks, Rootstock/RSK, Babylon, Botanix, Merlin, Bitlayer, BOB, or ",
        "equivalents from the configured label list.",
        "",
        *build_protocol_lines(data.get("bitcoin_ecosystem", []), TOP_BITCOIN_ECOSYSTEM_LIMIT),
        "",
        "## Bitcoin CEX/custody exposure excluded from ecosystem signal",
        "",
        *build_excluded_bitcoin_section(data),
        "",
        "## Caveats",
        "",
        "- This is research signal only, not financial advice.",
        "- DeFiLlama TVL and stablecoin data are useful flow proxies, not complete order-flow data.",
        "- Price action, liquidity depth, unlocks, and macro catalysts require separate validation.",
        "- This scaffold does not use social sentiment inputs by design.",
    ]
    return "\n".join(lines).replace(" \n", "\n") + "\n"


def write_text(path: Path, content: str) -> None:
    """Write a text artifact to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_daily_report(normalized_path: Path, output_dir: Path) -> Path:
    """Build the daily markdown report and return its path."""
    data = load_json(normalized_path)
    if not isinstance(data, dict):
        raise SystemExit("Normalized data root must be a JSON object.")
    date_stamp = normalized_date_stamp(data, normalized_path)
    output_path = output_dir / f"defillama-daily-{date_stamp}.md"
    write_text(output_path, build_report(data))
    return output_path


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Build an analyst-style DeFiLlama daily brief.")
    parser.add_argument("--normalized", type=Path)
    parser.add_argument("--normalized-dir", type=Path, default=DEFAULT_NORMALIZED_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the report builder CLI."""
    args = parse_args(argv or sys.argv[1:])
    normalized_path = args.normalized or latest_normalized_file(args.normalized_dir)
    report_path = build_daily_report(normalized_path, args.output_dir)
    print(f"Wrote DeFiLlama daily report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
