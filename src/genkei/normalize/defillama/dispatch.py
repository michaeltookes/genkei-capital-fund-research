"""Raw-blob → endpoint routing for the DeFiLlama normalizer (B-121).

The collector lands raw blobs keyed by ``endpoint_name`` (``protocol_<slug>``,
``protocol_fees_<slug>``, ``stablecoin_<asset_id>``, …). Both the daily and
backfill normalizers route each blob to the right per-endpoint normalizer by
matching that name against a known prefix and stripping it to recover the
slug / asset id.

This used to be two hand-rolled ``if endpoint_name.startswith(...)`` chains
inside ``core.normalize`` / ``core.normalize_backfill`` — fragile because the
prefixes *collide*: ``protocol_fees_`` and ``protocol_revenue_`` both start
with ``protocol_``, so the order of the checks is load-bearing (fees/revenue
must be tested before the generic ``protocol_`` history prefix). Pulling the
matching into one ordered table makes that ordering explicit and lets it be
unit-tested without a database — the orchestration in ``core`` stays the same,
it just asks this table which kind of blob it's holding.
"""

from __future__ import annotations

from dataclasses import dataclass

# Blob-name prefixes (mirror genkei.ingest.defillama).
CHAIN_HISTORY_PREFIX = "chain_tvl_history_"
PRICE_HISTORICAL_PREFIX = "prices_historical_"
PROTOCOL_HISTORY_PREFIX = "protocol_"
PROTOCOL_FEES_PREFIX = "protocol_fees_"
PROTOCOL_REVENUE_PREFIX = "protocol_revenue_"
STABLECOIN_HISTORY_PREFIX = "stablecoin_"


@dataclass(frozen=True)
class PrefixRoute:
    """Maps a raw-blob name prefix to a normalizer ``kind`` token."""

    prefix: str
    kind: str


# Ordered most-specific-first. ``protocol_fees_`` / ``protocol_revenue_`` MUST
# precede the generic ``protocol_`` history prefix, or a fees blob would be
# misrouted to the TVL-history normalizer.
BLOB_ROUTES: tuple[PrefixRoute, ...] = (
    PrefixRoute(PRICE_HISTORICAL_PREFIX, "price_historical"),
    PrefixRoute(PROTOCOL_FEES_PREFIX, "protocol_fees"),
    PrefixRoute(PROTOCOL_REVENUE_PREFIX, "protocol_revenue"),
    PrefixRoute(PROTOCOL_HISTORY_PREFIX, "protocol_history"),
    PrefixRoute(STABLECOIN_HISTORY_PREFIX, "stablecoin_history"),
)


def classify_blob(endpoint_name: str) -> tuple[str, str] | None:
    """Classify a raw-blob endpoint name into ``(kind, identifier)``.

    ``identifier`` is the slug / asset id left after stripping the matched
    prefix (empty for price-history blobs, which carry their keys in the
    payload). Returns ``None`` for names that match no known prefix — the
    caller decides whether that's an expected skip or a surprise to log.
    Routes are tried in ``BLOB_ROUTES`` order, so the first (most specific)
    match wins.
    """
    for route in BLOB_ROUTES:
        if endpoint_name.startswith(route.prefix):
            return route.kind, endpoint_name[len(route.prefix) :]
    return None
