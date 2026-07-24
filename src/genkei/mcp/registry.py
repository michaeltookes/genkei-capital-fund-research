"""Declarative registry mapping ``genkei`` subcommands to MCP tools (B-130).

Each :class:`ToolSpec` describes one MCP tool: its name, the ``genkei``
subcommand path it shells out to (e.g. ``["watchlist", "health"]``), a
one-line description, and its typed parameters. The MCP server
(:mod:`genkei.mcp.server`) turns each spec into an MCP tool definition and
routes tool calls through :mod:`genkei.mcp.adapter`, which builds the argv
and runs the CLI.

This module is intentionally SDK-free — it holds only plain dataclasses and
data — so it imports and tests without the ``mcp`` package installed. Adding
a new tool later is a **one-line append** to :data:`TOOL_SPECS`; extending a
tool's surface is one more :class:`ToolParam`.

Design note — why a registry, not 31 hand-written tool defs
-----------------------------------------------------------
Every subcommand shares the same shape (``--json`` output, ``_helpers``
serialization, freshness-to-stderr). A declarative registry captures the
*varying* parts (name, subcommand path, params) once and lets the server
generate the boilerplate. We deliberately expose a curated, well-described
subset rather than auto-reflecting all 31 subcommands: a smaller surface the
agent can reason about beats an exhaustive one it can't. The set below covers
the B-130-required tools (``prices``, ``signals``, ``tvl``, ``zcash-usage``,
``watchlist`` list/health/gaps/score, ``query``) plus the high-value macro /
momentum / news / anomaly surfaces. New tools land as they prove useful.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# JSON-schema-ish primitive types we map params to. Kept to the small set the
# CLI actually uses; the server renders these into an MCP input schema.
ParamType = Literal["string", "integer", "number", "boolean"]


@dataclass(frozen=True)
class ToolParam:
    """One parameter of an MCP tool → one ``genkei`` CLI flag (or positional).

    * ``name`` is the MCP-facing parameter name (snake_case).
    * ``flag`` is the CLI flag it maps to (e.g. ``"--ticker"``). When
      ``None`` the value is passed as a positional argument (used by
      ``query``'s SQL string).
    * ``type`` drives the generated input schema and how the adapter
      renders the value (booleans become bare flags when true).
    * ``required`` params must be supplied by the caller.
    """

    name: str
    flag: str | None
    type: ParamType = "string"
    description: str = ""
    required: bool = False


@dataclass(frozen=True)
class ToolSpec:
    """One MCP tool backed by a ``genkei`` subcommand invocation.

    ``subcommand`` is the argv path after ``genkei`` (e.g.
    ``["watchlist", "health"]``). ``emits_json`` records whether the
    subcommand supports ``--json`` (every exposed one does today); the
    adapter appends ``--json`` when true so tool output is machine-readable.
    """

    name: str
    subcommand: tuple[str, ...]
    description: str
    params: tuple[ToolParam, ...] = field(default_factory=tuple)
    emits_json: bool = True


# Shared param fragments reused across tools — declared once so a tweak to the
# common date-window / limit surface lands everywhere.
_SINCE = ToolParam("since", "--since", "string", "Start date (YYYY-MM-DD).")
_UNTIL = ToolParam("until", "--until", "string", "End date (YYYY-MM-DD).")


def _limit(help_text: str = "Maximum rows to return.") -> ToolParam:
    return ToolParam("limit", "--limit", "integer", help_text)


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="prices",
        subcommand=("prices",),
        description=(
            "Asset prices from the lake — crypto (CoinGecko/Coinbase) and "
            "equities/benchmarks (Yahoo). Latest by default; pass a date window "
            "for history."
        ),
        params=(
            ToolParam(
                "ticker", "--ticker", "string", "Asset ticker, e.g. BTC or AAPL.", required=True
            ),
            ToolParam(
                "source",
                "--source",
                "string",
                "coingecko | coinbase | yahoo. Omit to auto-route by asset class.",
            ),
            _SINCE,
            _UNTIL,
            _limit(),
        ),
    ),
    ToolSpec(
        name="signals",
        subcommand=("signals",),
        description=(
            "Cross-source signal correlation engine (B-064) — multi-source "
            "agreement stacks per asset, tagged by horizon."
        ),
        params=(
            ToolParam("asset", "--asset", "string", "Filter to one asset (symbol)."),
            _SINCE,
            _UNTIL,
            ToolParam("top", "--top", "integer", "Maximum stacks to return."),
        ),
    ),
    ToolSpec(
        name="tvl",
        subcommand=("tvl",),
        description=(
            "DeFiLlama chain / protocol TVL. Default: chains overview. Pass "
            "--chain or --protocol to drill in."
        ),
        params=(
            ToolParam("chain", "--chain", "string", "Chain name, e.g. Ethereum, Solana."),
            ToolParam("protocol", "--protocol", "string", "Protocol slug, e.g. aave-v3, lido."),
            _SINCE,
            _UNTIL,
            _limit(),
        ),
    ),
    ToolSpec(
        name="zcash_usage",
        subcommand=("zcash-usage",),
        description=(
            "Zcash shielded-pool adoption — shielded share of supply + trend "
            "(the ZEC privacy-narrative signal)."
        ),
        params=(
            _SINCE,
            _UNTIL,
            _limit("Maximum snapshots to return."),
            ToolParam(
                "by_pool", "--by-pool", "boolean", "Latest per-pool breakdown instead of the trend."
            ),
        ),
    ),
    ToolSpec(
        name="macro",
        subcommand=("macro",),
        description="FRED macro series observations (vintage-aware), e.g. DGS10, T10Y2Y.",
        params=(
            ToolParam("series", "--series", "string", "FRED series id, e.g. DGS10.", required=True),
            _SINCE,
            _UNTIL,
            _limit(),
        ),
    ),
    ToolSpec(
        name="momentum",
        subcommand=("momentum",),
        description="Trailing 3/7/30-day price momentum per asset (B-067, materialized).",
        params=(
            ToolParam("asset", "--asset", "string", "Filter to one asset (symbol)."),
            ToolParam("asset_class", "--asset-class", "string", "Filter to 'crypto' or 'equity'."),
            ToolParam(
                "window", "--window", "integer", "Sort by this window's return (3, 7, or 30)."
            ),
            _limit(),
        ),
    ),
    ToolSpec(
        name="anomalies",
        subcommand=("anomalies",),
        description="Per-series return anomalies (B-069) — rolling MAD-based outlier flags.",
        params=(
            ToolParam("asset", "--asset", "string", "Filter to one asset (symbol)."),
            _SINCE,
            _UNTIL,
            _limit(),
        ),
    ),
    ToolSpec(
        name="news",
        subcommand=("news",),
        description=(
            "GDELT GKG article clusters — filter by watchlist asset / theme / topic / tone."
        ),
        params=(
            ToolParam("asset", "--asset", "string", "Filter to one watchlist asset."),
            ToolParam("theme", "--theme", "string", "Filter to a GKG theme."),
            _SINCE,
            _UNTIL,
            _limit("Maximum clusters to return."),
        ),
    ),
    # --- watchlist subgroup: one tool per action ---------------------------
    ToolSpec(
        name="watchlist_list",
        subcommand=("watchlist", "list"),
        description="Dump the watchlist by sleeve (crypto / equities / macro / prices).",
        params=(
            ToolParam(
                "sleeve",
                "--sleeve",
                "string",
                "Filter to one sleeve: crypto | equity | macro | prices.",
            ),
        ),
    ),
    ToolSpec(
        name="watchlist_health",
        subcommand=("watchlist", "health"),
        description=(
            "Per-source ingest health + primary-table liveness + schema drift — "
            "the lake's is-data-flowing check."
        ),
        params=(
            ToolParam(
                "stale_hours",
                "--stale-hours",
                "number",
                "A successful run older than this is STALE.",
            ),
            ToolParam(
                "skip_drift", "--skip-drift", "boolean", "Skip the schema-drift check (faster)."
            ),
        ),
    ),
    ToolSpec(
        name="watchlist_gaps",
        subcommand=("watchlist", "gaps"),
        description="Per-asset freshness across sleeves — which assets have fallen behind.",
        params=(
            ToolParam(
                "threshold_hours",
                "--threshold-hours",
                "number",
                "Per-asset last-data older than this is tagged GAP.",
            ),
        ),
    ),
    ToolSpec(
        name="watchlist_score",
        subcommand=("watchlist", "score"),
        description="Per-asset composite signal score (B-065 rubric), sorted most-positive first.",
        params=(
            ToolParam("ticker", "--ticker", "string", "Filter to a single asset."),
            ToolParam(
                "sleeve",
                "--sleeve",
                "string",
                "Filter to one sleeve: equity-core | crypto-core | crypto-tactical.",
            ),
            ToolParam(
                "since",
                "--since",
                "string",
                "Read persisted history from this date (YYYY-MM-DD) instead of computing today.",
            ),
        ),
    ),
    # --- ad-hoc SQL escape hatch ------------------------------------------
    ToolSpec(
        name="query",
        subcommand=("query",),
        description=(
            "Ad-hoc read-only SQL against the lake — the escape hatch for "
            "questions the typed tools don't express. Engine-enforced READ ONLY "
            "with a statement timeout, server-side row cap, and multi-statement "
            "rejection; writes and DDL are rejected by Postgres itself."
        ),
        params=(
            ToolParam(
                "sql",
                None,  # positional
                "string",
                "A single read-only SELECT. Multi-statement input is rejected.",
                required=True,
            ),
            ToolParam(
                "limit", "--limit", "integer", "Server-side row cap wrapped around the query."
            ),
            ToolParam(
                "timeout_seconds",
                "--timeout-seconds",
                "integer",
                "Postgres statement_timeout in seconds.",
            ),
        ),
    ),
)


_BY_NAME: dict[str, ToolSpec] = {spec.name: spec for spec in TOOL_SPECS}


def tool_by_name(name: str) -> ToolSpec:
    """Return the :class:`ToolSpec` for ``name`` or raise ``KeyError``."""
    return _BY_NAME[name]
