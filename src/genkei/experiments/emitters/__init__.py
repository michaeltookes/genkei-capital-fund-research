"""Signal-event emitters (B-064).

Each emitter is a thin adapter that walks one Phase 5 experiment's
output and writes atomic events into ``meta.signal_events`` via
``signal_store.emit_signals_bulk``. The correlator scans the unified
event stream — emitters never talk to each other directly.

Live emitters: ``insider_clusters_emitter`` (B-064), ``crowding_emitter``
(B-093), ``eight_k_emitter`` (B-094). Together they cover every
component of the ``smart_money_buy`` and ``deterioration_stack`` rules.
Follow-ups for ``tvl_drawdown_emitter``, ``macro_regime_emitter``,
``watchlist_scoring_emitter``, ``relative_strength_emitter`` are
tracked as separate backlog items (B-095-B-098).
"""

from genkei.experiments.emitters import (
    crowding_emitter,
    eight_k_emitter,
    insider_clusters_emitter,
)

__all__ = ["crowding_emitter", "eight_k_emitter", "insider_clusters_emitter"]
