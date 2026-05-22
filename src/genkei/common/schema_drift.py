"""Schema-drift detection for raw_blobs payloads (B-072).

External API shapes change. DeFiLlama renames a field, FRED ships a new
JSON structure, CoinGecko adds an array nesting layer — and our
normalizers silently start producing zero rows (or wrong rows) without
failing. B-071's failure-alert + staleness-check catches *some* of
this — a normalizer that fails outright surfaces as FAIL; a stale
table surfaces as STALE. What they miss: a normalizer that *succeeds*
with zero rows because the field it reads is gone or renamed.

This module is the canary: it inspects the most recent ``meta.raw_blobs``
row per endpoint kind, verifies the load-bearing keys the normalizer
relies on, and emits a ``DriftIssue`` per violation. Output is consumed
by:

  * ``genkei watchlist drift`` — a focused CLI surface
  * ``genkei watchlist health`` — drift entries get ``health_status:
    "DRIFT"`` so the existing B-071 staleness check parser picks them
    up automatically and opens GitHub issues

Specs are intentionally narrow: we check *only* the fields the normalizer
actually consumes, not every key in the payload. Adding non-load-bearing
fields to a spec invites false positives (DeFiLlama adds an optional
``logo_v2`` field — irrelevant to us, should not be drift). When a
normalizer starts depending on a new field, the spec gets updated in
the same commit (CLAUDE.md "extract canonical helpers" principle).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from genkei.common import db


@dataclass(frozen=True)
class EndpointSchema:
    """Declares the load-bearing shape of one endpoint kind's payload.

    ``endpoint_kind`` is the canonical name (e.g. ``"protocol_<slug>"``)
    used in error reports. ``endpoint_pattern`` is the SQL LIKE pattern
    that matches actual ``raw_blobs.endpoint_name`` rows (e.g.
    ``"protocol\\_%"`` with the underscore escaped).

    ``endpoint_pattern_excludes`` is critical for shared-prefix endpoint
    families: ``protocol_<slug>``, ``protocol_fees_<slug>``, and
    ``protocol_revenue_<slug>`` all match ``protocol\\_%`` in SQL LIKE,
    so the protocol_<slug> spec must exclude the fees/revenue subset
    explicitly. The detector applies these as ``AND endpoint_name NOT
    LIKE %s ESCAPE '\\'`` clauses against the same column.

    ``payload_type`` is ``"object"`` or ``"array"``.

      * ``object``: ``required_keys`` lists keys that must exist at the
        payload root.
      * ``array``: payload must be a non-empty list; ``required_keys``
        lists keys that must exist on each item (sampled — see
        ``array_sample_size``).
    """

    source: str
    endpoint_kind: str
    endpoint_pattern: str
    payload_type: str  # "object" or "array"
    required_keys: tuple[str, ...]
    # SQL LIKE patterns whose matches should be REMOVED from the candidate
    # set after `endpoint_pattern` matches. Used to disambiguate
    # shared-prefix endpoint families (see class docstring).
    endpoint_pattern_excludes: tuple[str, ...] = field(default_factory=tuple)
    # For array payloads, check the first N items rather than every item
    # (the goal is canary, not exhaustive).
    array_sample_size: int = 3
    # Nested path check: list of dotted paths whose leaf must exist (for
    # object payloads only). Each path is ``"top.sub.deeper"``.
    nested_paths: tuple[str, ...] = field(default_factory=tuple)


# Spec registry. Each entry captures the load-bearing fields the
# corresponding normalizer reads. Keep narrow; expand only when a
# normalizer starts depending on a new field.
#
# To add a new endpoint to the canary set: append an EndpointSchema
# below, write a unit test fixture with the canonical shape, and verify
# the spec matches a real blob via `genkei watchlist drift`.
SCHEMA_SPECS: tuple[EndpointSchema, ...] = (
    # DefiLlama — daily snapshot endpoints
    EndpointSchema(
        source="defillama",
        endpoint_kind="protocols",
        endpoint_pattern="protocols",
        payload_type="array",
        required_keys=("slug", "name", "tvl"),
    ),
    EndpointSchema(
        source="defillama",
        endpoint_kind="prices_current",
        endpoint_pattern="prices_current",
        payload_type="object",
        required_keys=("coins",),
    ),
    EndpointSchema(
        source="defillama",
        endpoint_kind="stablecoins",
        endpoint_pattern="stablecoins",
        payload_type="object",
        required_keys=("peggedAssets",),
    ),
    EndpointSchema(
        source="defillama",
        endpoint_kind="chain_tvl_history_<chain>",
        endpoint_pattern="chain_tvl_history_%",
        payload_type="array",
        required_keys=("date", "tvl"),
    ),
    # DefiLlama — per-entity history endpoints (also hit during daily
    # collector for the watchlist subset; same payload shape).
    EndpointSchema(
        source="defillama",
        endpoint_kind="protocol_<slug>",
        # The literal underscore is escaped in the pattern, but
        # `protocol\\_%` still matches `protocol_fees_<slug>` and
        # `protocol_revenue_<slug>` because `%` is greedy. Exclude
        # those shared-prefix families explicitly so the per-protocol
        # spec doesn't false-positive on fee/revenue blobs.
        endpoint_pattern="protocol\\_%",
        endpoint_pattern_excludes=("protocol_fees_%", "protocol_revenue_%"),
        payload_type="object",
        required_keys=("name", "chainTvls"),
    ),
    EndpointSchema(
        source="defillama",
        endpoint_kind="protocol_fees_<slug>",
        endpoint_pattern="protocol_fees_%",
        payload_type="object",
        required_keys=("totalDataChart",),
    ),
    EndpointSchema(
        source="defillama",
        endpoint_kind="protocol_revenue_<slug>",
        endpoint_pattern="protocol_revenue_%",
        payload_type="object",
        required_keys=("totalDataChart",),
    ),
    EndpointSchema(
        source="defillama",
        endpoint_kind="stablecoin_<id>",
        endpoint_pattern="stablecoin\\_%",
        payload_type="object",
        required_keys=("symbol", "name", "pegType", "chainCirculating"),
    ),
    # CoinGecko
    EndpointSchema(
        source="coingecko",
        endpoint_kind="coin_<id>",
        endpoint_pattern="coin\\_%",
        payload_type="object",
        required_keys=("id", "symbol", "name", "market_data"),
    ),
    EndpointSchema(
        source="coingecko",
        endpoint_kind="market_chart_<id>",
        endpoint_pattern="market_chart_%",
        payload_type="object",
        # Three parallel arrays per G-024. All three are load-bearing —
        # the normalizer zips by index and skips rows where any are missing.
        required_keys=("prices", "market_caps", "total_volumes"),
    ),
    # FRED — note the "seriess" typo (FRED's, not ours) per G-018.
    EndpointSchema(
        source="fred",
        endpoint_kind="series_<id>",
        endpoint_pattern="series\\_%",
        payload_type="object",
        required_keys=("seriess",),
    ),
    EndpointSchema(
        source="fred",
        endpoint_kind="observations_<id>",
        endpoint_pattern="observations\\_%",
        payload_type="object",
        required_keys=("observations",),
    ),
    # SEC EDGAR
    EndpointSchema(
        source="sec",
        endpoint_kind="submissions_<cik>",
        endpoint_pattern="submissions\\_%",
        endpoint_pattern_excludes=("submissions\\_history\\_%",),
        payload_type="object",
        required_keys=("cik", "name", "filings"),
    ),
    EndpointSchema(
        source="sec",
        endpoint_kind="companyfacts_<cik>",
        endpoint_pattern="companyfacts_%",
        payload_type="object",
        required_keys=("cik", "facts"),
    ),
)


@dataclass(frozen=True)
class DriftIssue:
    """One schema-drift finding, surfaced via `watchlist drift` / `watchlist health`."""

    source: str
    endpoint_kind: str
    sample_endpoint_name: str | None
    # One of: MISSING_REQUIRED_KEY, WRONG_TOP_LEVEL_TYPE, EMPTY_ARRAY,
    # MISSING_NESTED_PATH, NO_RECENT_SAMPLES, CHECKER_ERROR.
    kind: str
    detail: str


def check_payload(payload: Any, spec: EndpointSchema) -> list[DriftIssue]:
    """Pure check: compare one payload against one spec.

    Returns a list of ``DriftIssue``s (may be empty). Each issue's
    ``sample_endpoint_name`` is None — the caller fills it in when the
    check runs against a live raw_blobs row.
    """
    issues: list[DriftIssue] = []
    if spec.payload_type == "object":
        if not isinstance(payload, dict):
            issues.append(
                DriftIssue(
                    source=spec.source,
                    endpoint_kind=spec.endpoint_kind,
                    sample_endpoint_name=None,
                    kind="WRONG_TOP_LEVEL_TYPE",
                    detail=f"expected object, got {_describe_type(payload)}",
                )
            )
            return issues
        for key in spec.required_keys:
            if key not in payload:
                issues.append(
                    DriftIssue(
                        source=spec.source,
                        endpoint_kind=spec.endpoint_kind,
                        sample_endpoint_name=None,
                        kind="MISSING_REQUIRED_KEY",
                        detail=f"required key {key!r} not in top-level object",
                    )
                )
        for path in spec.nested_paths:
            if not _path_exists(payload, path):
                issues.append(
                    DriftIssue(
                        source=spec.source,
                        endpoint_kind=spec.endpoint_kind,
                        sample_endpoint_name=None,
                        kind="MISSING_NESTED_PATH",
                        detail=f"nested path {path!r} not resolvable",
                    )
                )
        return issues

    if spec.payload_type == "array":
        if not isinstance(payload, list):
            issues.append(
                DriftIssue(
                    source=spec.source,
                    endpoint_kind=spec.endpoint_kind,
                    sample_endpoint_name=None,
                    kind="WRONG_TOP_LEVEL_TYPE",
                    detail=f"expected array, got {_describe_type(payload)}",
                )
            )
            return issues
        if not payload:
            issues.append(
                DriftIssue(
                    source=spec.source,
                    endpoint_kind=spec.endpoint_kind,
                    sample_endpoint_name=None,
                    kind="EMPTY_ARRAY",
                    detail="payload is an empty array; expected at least one item",
                )
            )
            return issues
        sample = payload[: max(1, spec.array_sample_size)]
        missing_by_key: dict[str, int] = {key: 0 for key in spec.required_keys}
        non_dict_count = 0
        for item in sample:
            if not isinstance(item, dict):
                non_dict_count += 1
                continue
            for key in spec.required_keys:
                if key not in item:
                    missing_by_key[key] += 1
        if non_dict_count == len(sample):
            issues.append(
                DriftIssue(
                    source=spec.source,
                    endpoint_kind=spec.endpoint_kind,
                    sample_endpoint_name=None,
                    kind="WRONG_TOP_LEVEL_TYPE",
                    detail=(
                        f"none of the first {len(sample)} array items are objects "
                        f"(types: {_describe_sample_types(sample)})"
                    ),
                )
            )
            return issues
        for key, missing_count in missing_by_key.items():
            if missing_count > 0:
                issues.append(
                    DriftIssue(
                        source=spec.source,
                        endpoint_kind=spec.endpoint_kind,
                        sample_endpoint_name=None,
                        kind="MISSING_REQUIRED_KEY",
                        detail=(
                            f"required key {key!r} missing in {missing_count}/"
                            f"{len(sample)} sampled array items"
                        ),
                    )
                )
        return issues

    raise ValueError(
        f"unsupported payload_type {spec.payload_type!r} in spec {spec.endpoint_kind!r}"
    )


def check_recent_blobs(
    *,
    max_age_hours: int = 72,
    specs: tuple[EndpointSchema, ...] = SCHEMA_SPECS,
) -> list[DriftIssue]:
    """Check the most recent blob per endpoint_kind against its spec.

    Reports ``NO_RECENT_SAMPLES`` when an expected endpoint has nothing
    fresher than ``max_age_hours`` — that's its own signal (either the
    collector stopped or the spec covers an endpoint we never reach
    anymore).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    issues: list[DriftIssue] = []
    with db.connection() as conn, conn.cursor() as cur:
        for spec in specs:
            # Build the WHERE clause dynamically because each spec can
            # contribute zero or more NOT-LIKE exclusions. Params bind
            # positionally so the query parser stays happy.
            sql = (
                "SELECT endpoint_name, payload, fetched_at "
                "FROM meta.raw_blobs "
                "WHERE endpoint_name LIKE %s ESCAPE %s "
                "AND fetched_at >= %s"
            )
            params: list[Any] = [spec.endpoint_pattern, "\\", cutoff]
            for exclude in spec.endpoint_pattern_excludes:
                sql += " AND endpoint_name NOT LIKE %s ESCAPE %s"
                params.extend([exclude, "\\"])
            sql += " ORDER BY fetched_at DESC LIMIT 1"
            cur.execute(sql, params)
            row = cur.fetchone()
            if row is None:
                issues.append(
                    DriftIssue(
                        source=spec.source,
                        endpoint_kind=spec.endpoint_kind,
                        sample_endpoint_name=None,
                        kind="NO_RECENT_SAMPLES",
                        detail=(
                            f"no raw_blobs rows matching pattern {spec.endpoint_pattern!r} "
                            f"newer than {max_age_hours}h"
                        ),
                    )
                )
                continue
            endpoint_name, payload, _ = row
            per_payload_issues = check_payload(payload, spec)
            for issue in per_payload_issues:
                # check_payload doesn't know which blob it was sampling.
                # Fill in here so reports show "sampled from blob X".
                issues.append(
                    DriftIssue(
                        source=issue.source,
                        endpoint_kind=issue.endpoint_kind,
                        sample_endpoint_name=endpoint_name,
                        kind=issue.kind,
                        detail=issue.detail,
                    )
                )
    return issues


def _describe_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _describe_sample_types(sample: list[Any]) -> str:
    """Used in error messages — `[null, integer, string]` style."""
    return "[" + ", ".join(_describe_type(item) for item in sample) + "]"


def _path_exists(payload: Any, dotted_path: str) -> bool:
    """Resolve a `.`-separated path against a nested dict; True if leaf is set."""
    cursor: Any = payload
    for segment in dotted_path.split("."):
        if not isinstance(cursor, dict) or segment not in cursor:
            return False
        cursor = cursor[segment]
    return True
