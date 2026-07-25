"""Connection-pool ceiling for the read API (B-131 / B-137).

The API shares ``genkeicapital-postgres`` with every ingest workload. To keep
a burst of cockpit requests from starving the daily pipelines, the API caps
``genkei.common.db``'s process-wide pool at a **small** ``max_size`` on
startup (default 4). This is the resource-protection ceiling recorded in
``docs/api-deployment.md``.

The pool is a process-wide singleton (``db.get_pool`` returns the same pool
regardless of later args), so configuring it once at startup — before any
request opens a connection — is what pins the ceiling. A test that has already
injected its own pool (via ``db.set_pool``) is left untouched.
"""

from __future__ import annotations

import os

# Small by design: the API runs in its own container and must never hold more
# than a handful of the shared Postgres server's connections at once. Ingest
# jobs are short-lived and bursty; leaving them headroom matters more than API
# throughput for a single-user cockpit.
DEFAULT_MAX_POOL_SIZE = 4
DEFAULT_MIN_POOL_SIZE = 1

_ENV_MAX_POOL_SIZE = "GENKEI_API_MAX_POOL_SIZE"


def max_pool_size() -> int:
    """Return the configured pool ceiling (env override, else the default)."""
    raw = os.environ.get(_ENV_MAX_POOL_SIZE)
    if raw is None or not raw.strip():
        return DEFAULT_MAX_POOL_SIZE
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_POOL_SIZE
    return value if value > 0 else DEFAULT_MAX_POOL_SIZE


def configure_pool() -> None:
    """Initialize ``genkei.common.db``'s shared pool with the API's ceiling.

    Idempotent-ish: ``db.get_pool`` only builds the pool on first call, so if a
    test (or an earlier import) has already created or injected a pool this is
    a no-op ceiling-wise. Called from the app's startup lifespan.
    """
    from genkei.common import db

    db.get_pool(min_size=DEFAULT_MIN_POOL_SIZE, max_size=max_pool_size())
