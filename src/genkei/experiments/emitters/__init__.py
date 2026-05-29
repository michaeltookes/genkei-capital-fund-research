"""Signal-event emitters (B-064).

Each emitter is a thin adapter that walks one Phase 5 experiment's
output and writes atomic events into ``meta.signal_events`` via
``signal_store.emit_signals_bulk``. The correlator scans the unified
event stream — emitters never talk to each other directly.

Reference emitter: ``insider_clusters_emitter``. Follow-ups for
``crowding_emitter``, ``eight_k_emitter``, etc. share the same shape
and are tracked as separate backlog items.
"""

from genkei.experiments.emitters import insider_clusters_emitter

__all__ = ["insider_clusters_emitter"]
