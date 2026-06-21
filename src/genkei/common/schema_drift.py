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
from genkei.common.defillama import is_stablecoin_history_point


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
    # For object payloads: at least ONE of these ordered top-level keys must
    # be present (an "either/or" the AND-list ``required_keys`` can't express).
    # Use when a source publishes the same load-bearing field under more
    # than one name across payload versions and the normalizer checks them in
    # precedence order — e.g. DefiLlama's ``chainCirculating`` then the older
    # ``chainBalances``. A missing-from-all set is a real drift signal because
    # the normalizer silently yields zero rows when none resolve.
    any_of_keys: tuple[str, ...] = field(default_factory=tuple)
    # Optional value-shape guard for ``any_of_keys``. When set, the first
    # present interchangeable key must carry this top-level shape. This keeps
    # a present-but-unparseable preferred value (e.g. ``chainCirculating: []``)
    # from masking drift when the normalizer needs a dict.
    any_of_value_type: str | None = None  # "object" or "array"
    # Optional content guard for the selected ``any_of_keys`` value. This is
    # intentionally enumerated rather than open-ended so a spec has to name the
    # normalizer shape it is mirroring.
    any_of_value_content: str | None = None  # "stablecoin_history"
    # For array payloads, check the first N items rather than every item
    # (the goal is canary, not exhaustive).
    array_sample_size: int = 3
    # Nested path check: list of dotted paths whose leaf must exist (for
    # object payloads only). Each path is ``"top.sub.deeper"``.
    nested_paths: tuple[str, ...] = field(default_factory=tuple)
    # For array-of-arrays payloads (e.g. Coinbase candles, where each
    # item is ``[time, low, high, open, close, volume]`` not a dict):
    # validate each sampled item is a list with at least this many
    # elements. When set, ``required_keys`` is ignored and the
    # "items must be objects" check is skipped.
    array_item_min_length: int | None = None
    # When True, this endpoint is only fetched during backfill (not the
    # daily collector), so its blobs are expected to age past
    # ``max_age_hours`` between backfills. The freshness check skips the
    # NO_RECENT_SAMPLES alarm for these and instead validates the shape
    # of the latest blob whenever one exists — schema drift still gets
    # caught, but a stale-but-intact backfill artifact doesn't false-
    # alarm the health footer / B-071 issue opener. The daily forward
    # series these endpoints once backfilled is kept current by a
    # separate daily endpoint (e.g. stablecoin_<id> history is backfill-
    # only; the aggregate ``stablecoins`` blob carries the daily supply).
    backfill_only: bool = False


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
        required_keys=("symbol", "name", "pegType"),
        # The per-chain supply series lives under ``chainCirculating`` in
        # current payloads and ``chainBalances`` in older ones; the
        # normalizer checks chainCirculating first and falls back to
        # chainBalances, so require the selected key to be parseable rather
        # than hard-coding the newer name (which real blobs don't carry).
        any_of_keys=("chainCirculating", "chainBalances"),
        any_of_value_type="object",
        any_of_value_content="stablecoin_history",
        # Per-id /stablecoin/{id} history is backfill-only — the daily
        # collector keeps the forward supply series current through the
        # aggregate ``stablecoins`` blob, so these blobs legitimately age
        # past 72h between backfills (G-043).
        backfill_only=True,
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
    # Coinbase Exchange (B-035) — candle blobs are an array-of-arrays
    # with positional fields: [time, low, high, open, close, volume].
    # Both the daily-mode blob (candles_<product>) and the backfill
    # blob (candles_<product>_<start>_<end>) share this shape.
    EndpointSchema(
        source="coinbase",
        endpoint_kind="candles_<product>",
        endpoint_pattern="candles\\_%",
        payload_type="array",
        required_keys=(),  # ignored for array-of-arrays
        array_item_min_length=6,
    ),
    # Yahoo Finance (B-092) — chart endpoint returns a nested object
    # with parallel arrays under `chart.result[0].indicators.quote[0]`
    # plus the parallel `timestamp` array. The load-bearing keys are
    # the chart wrapper, the result list, and the indicators block;
    # validating the nested-array shapes is left to the normalizer
    # (which skips malformed rows). The `chart_` prefix is distinct
    # from Coinbase's `candles_` to avoid pattern-LIKE collisions.
    EndpointSchema(
        source="yahoo",
        endpoint_kind="chart_<ticker>",
        endpoint_pattern="chart\\_%",
        payload_type="object",
        required_keys=("chart",),
        nested_paths=(
            "chart.result",
        ),
    ),
    # CFTC (B-031) — Commitments of Traders weekly position breakdowns.
    # Each per-market blob is an array of row-dicts. The load-bearing
    # keys live on each row; the collector reads
    # `report_date_as_yyyy_mm_dd` to build the natural PK and
    # `cftc_contract_market_code` to verify the row belongs to the
    # market it was filtered for. Per-trader-category long/short fields
    # vary by report type (TFF vs Disaggregated) so they're not in the
    # canary — a normalizer regression there would surface as zero rows
    # for that category, not as drift.
    EndpointSchema(
        source="cftc",
        endpoint_kind="cot_<report_type>_<market_code>",
        endpoint_pattern="cot\\_%",
        payload_type="array",
        required_keys=("report_date_as_yyyy_mm_dd", "cftc_contract_market_code"),
    ),
)


@dataclass(frozen=True)
class NaturalKeyUniquenessSpec:
    """Declares that ``<source>.<table>`` should have at most 1 row per
    natural-key + day on recent data.

    The natural-key columns are joined to ``ts::date`` to form the
    per-day group; ``COUNT(*) > 1`` on any group is a drift. A regression
    where someone reintroduces dual-ingest or removes the day-align
    semantics surfaces here as a ``DUPLICATE_NATURAL_KEY`` DriftIssue
    in ``genkei watchlist health`` rather than in the next research
    session's manual SQL escape hatch.
    """

    source: str
    table: str  # schema-qualified, e.g. "defillama.stablecoins"
    natural_key_cols: tuple[str, ...]
    # Look back this many days; older rows were already normalized by the
    # one-shot cleanup migration.
    lookback_days: int = 30


# Tables whose ``ts`` semantics are per-day (one row per natural-key per
# day). Add a new entry when a future ingester lands a similar shape.
NATURAL_KEY_SPECS: tuple[NaturalKeyUniquenessSpec, ...] = (
    NaturalKeyUniquenessSpec(
        source="defillama",
        table="defillama.stablecoins",
        natural_key_cols=("asset_id", "chain"),
    ),
    NaturalKeyUniquenessSpec(
        source="defillama",
        table="defillama.protocol_tvl",
        natural_key_cols=("slug", "chain"),
    ),
    NaturalKeyUniquenessSpec(
        source="defillama",
        table="defillama.protocol_fees",
        natural_key_cols=("slug",),
    ),
)


@dataclass(frozen=True)
class DriftIssue:
    """One schema-drift finding, surfaced via `watchlist drift` / `watchlist health`."""

    source: str
    endpoint_kind: str
    sample_endpoint_name: str | None
    # One of: MISSING_REQUIRED_KEY, MISSING_ANY_OF_KEYS, WRONG_TOP_LEVEL_TYPE,
    # EMPTY_ARRAY, MISSING_NESTED_PATH, NO_RECENT_SAMPLES,
    # DUPLICATE_NATURAL_KEY, CHECKER_ERROR.
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
        if spec.any_of_keys and not _any_of_keys_resolves(payload, spec):
            issues.append(
                DriftIssue(
                    source=spec.source,
                    endpoint_kind=spec.endpoint_kind,
                    sample_endpoint_name=None,
                    kind="MISSING_ANY_OF_KEYS",
                    detail=(
                        _missing_any_of_keys_detail(spec)
                    ),
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
        # Array-of-arrays mode (Coinbase candles): check item length only.
        if spec.array_item_min_length is not None:
            short_count = 0
            non_list_count = 0
            for item in sample:
                if not isinstance(item, list):
                    non_list_count += 1
                elif len(item) < spec.array_item_min_length:
                    short_count += 1
            if non_list_count == len(sample):
                issues.append(
                    DriftIssue(
                        source=spec.source,
                        endpoint_kind=spec.endpoint_kind,
                        sample_endpoint_name=None,
                        kind="WRONG_TOP_LEVEL_TYPE",
                        detail=(
                            f"array-of-arrays: none of the first {len(sample)} items "
                            f"are lists (types: {_describe_sample_types(sample)})"
                        ),
                    )
                )
            elif non_list_count > 0:
                issues.append(
                    DriftIssue(
                        source=spec.source,
                        endpoint_kind=spec.endpoint_kind,
                        sample_endpoint_name=None,
                        kind="WRONG_TOP_LEVEL_TYPE",
                        detail=(
                            f"array-of-arrays: {non_list_count}/{len(sample)} sampled "
                            f"items are not lists (types: {_describe_sample_types(sample)})"
                        ),
                    )
                )
            elif short_count > 0:
                issues.append(
                    DriftIssue(
                        source=spec.source,
                        endpoint_kind=spec.endpoint_kind,
                        sample_endpoint_name=None,
                        kind="MISSING_REQUIRED_KEY",
                        detail=(
                            f"array-of-arrays: {short_count}/{len(sample)} items have "
                            f"length < {spec.array_item_min_length}"
                        ),
                    )
                )
            return issues
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
        if non_dict_count > 0:
            issues.append(
                DriftIssue(
                    source=spec.source,
                    endpoint_kind=spec.endpoint_kind,
                    sample_endpoint_name=None,
                    kind="WRONG_TOP_LEVEL_TYPE",
                    detail=(
                        f"{non_dict_count}/{len(sample)} sampled array items are not objects "
                        f"(types: {_describe_sample_types(sample)})"
                    ),
                )
            )
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


def _any_of_keys_resolves(payload: dict[str, Any], spec: EndpointSchema) -> bool:
    for key in spec.any_of_keys:
        if key not in payload:
            continue
        if spec.any_of_value_type is None:
            return _any_of_value_content_resolves(payload[key], spec)
        return _matches_schema_type(
            payload[key], spec.any_of_value_type
        ) and _any_of_value_content_resolves(payload[key], spec)
    return False


def _any_of_value_content_resolves(value: Any, spec: EndpointSchema) -> bool:
    if spec.any_of_value_content is None:
        return True
    if spec.any_of_value_content == "stablecoin_history":
        return _has_stablecoin_history_point(value)
    raise ValueError(f"unsupported any_of_value_content {spec.any_of_value_content!r}")


def _has_stablecoin_history_point(chain_balances: Any) -> bool:
    if not isinstance(chain_balances, dict):
        return False
    for chain_data in chain_balances.values():
        if not isinstance(chain_data, dict):
            continue
        series = chain_data.get("tokens")
        if not isinstance(series, list):
            continue
        if any(is_stablecoin_history_point(point) for point in series):
            return True
    return False


def _missing_any_of_keys_detail(spec: EndpointSchema) -> str:
    if spec.any_of_value_type is None:
        return (
            "none of the interchangeable top-level keys "
            f"{spec.any_of_keys!r} are present"
        )
    content = (
        f" with {_describe_value_content(spec.any_of_value_content)}"
        if spec.any_of_value_content is not None
        else ""
    )
    return (
        "no selected interchangeable top-level key "
        f"{spec.any_of_keys!r} carries expected "
        f"{_describe_schema_type(spec.any_of_value_type)} shape"
        f"{content}"
    )


def _matches_schema_type(value: Any, schema_type: str) -> bool:
    if schema_type == "object":
        return isinstance(value, dict)
    if schema_type == "array":
        return isinstance(value, list)
    raise ValueError(f"unsupported any_of_value_type {schema_type!r}")


def _describe_schema_type(schema_type: str | None) -> str:
    return {"object": "object", "array": "array"}.get(str(schema_type), str(schema_type))


def _describe_value_content(value_content: str | None) -> str:
    return {
        "stablecoin_history": "at least one stablecoin history point",
    }.get(str(value_content), str(value_content))


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
            # Backfill-only endpoints are exempt from the freshness window:
            # their blobs are meant to age between backfills, so we sample
            # the latest blob of any age and only validate its shape.
            sql = (
                "SELECT endpoint_name, payload, fetched_at "
                "FROM meta.raw_blobs "
                "WHERE endpoint_name LIKE %s ESCAPE %s"
            )
            params: list[Any] = [spec.endpoint_pattern, "\\"]
            if not spec.backfill_only:
                sql += " AND fetched_at >= %s"
                params.append(cutoff)
            for exclude in spec.endpoint_pattern_excludes:
                sql += " AND endpoint_name NOT LIKE %s ESCAPE %s"
                params.extend([exclude, "\\"])
            sql += " ORDER BY fetched_at DESC LIMIT 1"
            cur.execute(sql, params)
            row = cur.fetchone()
            if row is None:
                # A backfill-only endpoint with zero blobs has simply
                # never been backfilled — that's not drift, so stay quiet.
                if spec.backfill_only:
                    continue
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


def check_natural_key_uniqueness(
    specs: tuple[NaturalKeyUniquenessSpec, ...] = NATURAL_KEY_SPECS,
) -> list[DriftIssue]:
    """Table-level canary: assert per-day natural-key uniqueness.

    For each spec, query the table for any group of rows sharing the
    same natural key + ``ts::date`` with COUNT > 1 in the most recent
    ``lookback_days`` days. Such a group means the day-align contract
    has been silently broken (e.g. a new ingest path landed without
    going through ``day_align_utc`` in the normalize layer).

    Returns one ``DriftIssue`` per affected table (not per duplicate
    group) — the detail string carries the count of affected groups.
    Zero issues = uniqueness contract intact.
    """
    issues: list[DriftIssue] = []
    with db.connection() as conn, conn.cursor() as cur:
        for spec in specs:
            key_cols_sql = ", ".join(spec.natural_key_cols)
            sql = (
                f"SELECT COUNT(*) FROM ("
                f"  SELECT {key_cols_sql}, ts::date AS day FROM {spec.table}"
                f"  WHERE ts::date >= (CURRENT_DATE - %s)::date"
                f"  GROUP BY {key_cols_sql}, ts::date HAVING COUNT(*) > 1"
                f") t"
            )
            try:
                cur.execute(sql, [spec.lookback_days])
                row = cur.fetchone()
            except Exception as exc:  # noqa: BLE001 — defensive
                conn.rollback()
                issues.append(
                    DriftIssue(
                        source=spec.source,
                        endpoint_kind=spec.table,
                        sample_endpoint_name=None,
                        kind="CHECKER_ERROR",
                        detail=str(exc)[:300],
                    )
                )
                continue
            n_groups = int(row[0]) if row and row[0] is not None else 0
            if n_groups > 0:
                key_label = " + ".join(spec.natural_key_cols)
                issues.append(
                    DriftIssue(
                        source=spec.source,
                        endpoint_kind=spec.table,
                        sample_endpoint_name=None,
                        kind="DUPLICATE_NATURAL_KEY",
                        detail=(
                            f"{n_groups} ({key_label}, ts::date) group(s) "
                            f"have >1 row in the last {spec.lookback_days}d "
                            "— day-align contract broken"
                        ),
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
