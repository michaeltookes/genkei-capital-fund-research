"""Signal-event emitters (B-064).

Each emitter is a thin adapter that walks one Phase 5 experiment's
output and writes atomic events into ``meta.signal_events`` via
``signal_store.emit_signals_bulk``. The correlator scans the unified
event stream — emitters never talk to each other directly.

Live emitters:

* Equity side (all four starter rules fully wired):
  ``insider_clusters_emitter`` (B-064),
  ``crowding_emitter`` (B-093),
  ``eight_k_emitter`` (B-094).
* Crypto side: ``tvl_drawdown_emitter`` (B-095) — first crypto-side
  source. Pairs with the follow-up ``relative_strength_emitter``
  (B-098) to satisfy the correlator's ``min_distinct_sources ≥ 2``
  gate for crypto-side stacks.

Follow-ups: ``macro_regime_emitter`` (B-096),
``watchlist_scoring_emitter`` (B-097), ``relative_strength_emitter``
(B-098) are tracked as separate backlog items.
"""

from genkei.experiments.emitters import (
    crowding_emitter,
    eight_k_emitter,
    insider_clusters_emitter,
    tvl_drawdown_emitter,
)

__all__ = [
    "crowding_emitter",
    "eight_k_emitter",
    "insider_clusters_emitter",
    "tvl_drawdown_emitter",
]
