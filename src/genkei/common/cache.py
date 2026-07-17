"""Disk-backed TTL cache for repeated CLI query results (B-046).

The backlog asked for an "in-process cache with a sensible TTL" so the agent
doesn't re-run the same query many times in a session. But the genkei CLI is
**one-shot per process** — each ``genkei …`` bash call is a fresh interpreter,
so a same-process (in-memory) cache can never see the previous invocation's
result. The stated use case ("the agent issues the same query multiple times in
a session") *is* many separate processes, so the cache has to survive across
invocations. This module is therefore a small **disk-backed** TTL cache; the
freshness bound comes from the TTL (default 5 min), which is short enough that a
burst of identical research queries hits the cache while the next daily-cron
refresh is never more than a TTL stale.

Design:
  * **Key** — a sha256 of the caller's key parts (canonical-JSON encoded), so a
    change in *any* parameter yields a different key (B-046: "cache key includes
    all query parameters"). Callers pass the parts via :func:`make_key`.
  * **Entry** — one JSON file per key under the cache dir, ``{stored_at, value}``.
    ``value`` is the already-rendered output string, so a cache hit is
    byte-identical to a fresh run (no row/Decimal/JSONB re-serialization drift).
  * **TTL applied at read** — the *reader* decides how fresh it needs to be
    (:func:`load` takes ``ttl``), so ``--cache-ttl`` tunes staleness without
    rewriting entries. Expired entries are unlinked opportunistically on read.
  * **Atomic writes** — write to a temp file + ``os.replace`` so a concurrent
    reader never sees a half-written entry.

``genkei query`` is the first consumer; the helper is deliberately generic
(keyed by arbitrary parts, caches a string) so other read-only subcommands can
adopt it later without a rewrite.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

DEFAULT_TTL_SECONDS = 300
_ENV_DIR = "GENKEI_CACHE_DIR"
_ENV_TTL = "GENKEI_CACHE_TTL"


def cache_dir() -> Path:
    """Return the query-cache directory, creating it if needed.

    Honors ``GENKEI_CACHE_DIR``; otherwise ``$XDG_CACHE_HOME/genkei`` or
    ``~/.cache/genkei``. The ``query`` subdir isolates this cache from any
    future cache namespaces.
    """
    raw = os.environ.get(_ENV_DIR)
    if raw:
        base = Path(raw)
    else:
        xdg = os.environ.get("XDG_CACHE_HOME")
        base = (Path(xdg) if xdg else Path.home() / ".cache") / "genkei"
    directory = base / "query"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def default_ttl() -> int:
    """Resolve the default TTL: ``GENKEI_CACHE_TTL`` if a positive int, else 300."""
    raw = os.environ.get(_ENV_TTL)
    if raw:
        try:
            value = int(raw)
        except ValueError:
            return DEFAULT_TTL_SECONDS
        if value > 0:
            return value
    return DEFAULT_TTL_SECONDS


def make_key(*parts: Any) -> str:
    """Hash the given key parts into a stable filename-safe cache key.

    Parts are canonical-JSON encoded (sorted keys, ``default=str`` for any
    non-JSON value) so the same logical inputs always hash identically and any
    difference in a part changes the key.
    """
    canonical = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load(key: str, *, ttl: int, now: float | None = None) -> str | None:
    """Return the cached value for ``key`` if present and younger than ``ttl``.

    Returns ``None`` on a miss, an expired entry (which is unlinked), or any
    read/parse error — a broken cache file must never break a query.
    """
    current = time.time() if now is None else now
    path = cache_dir() / f"{key}.json"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        entry = json.loads(raw)
        stored_at = float(entry["stored_at"])
        value = entry["value"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(value, str):
        return None
    if current - stored_at >= ttl:
        _unlink_quietly(path)
        return None
    return value


def store(key: str, value: str, *, now: float | None = None) -> None:
    """Write ``value`` under ``key`` with the current timestamp (atomic).

    Best-effort: any filesystem error is swallowed — caching is an
    optimization, never a correctness dependency.
    """
    current = time.time() if now is None else now
    directory = cache_dir()
    path = directory / f"{key}.json"
    payload = json.dumps({"stored_at": current, "value": value})
    try:
        fd, tmp_name = tempfile.mkstemp(dir=str(directory), suffix=".tmp")
    except OSError:
        return
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_name, path)
    except OSError:
        _unlink_quietly(Path(tmp_name))


def clear() -> int:
    """Delete every entry in the query cache; return the count removed."""
    directory = cache_dir()
    removed = 0
    for entry in directory.glob("*.json"):
        with contextlib.suppress(OSError):
            entry.unlink()
            removed += 1
    return removed


def _unlink_quietly(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()
