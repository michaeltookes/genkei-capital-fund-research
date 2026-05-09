"""Lightweight `.env` loader.

Reads ``KEY=VALUE`` pairs from a file and writes them into ``os.environ``
when the variable isn't already set. Stdlib-only — no ``python-dotenv``
dependency. Sufficient for local dev and the CLI entry point; CI reads
GitHub Actions secrets directly from the environment so ``.env`` is never
present there.

Usage:

    from genkei.common import load_env_file
    load_env_file()                     # default: .env in cwd
    load_env_file(".env.local")         # explicit path
"""

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: str | Path = ".env") -> int:
    """Load ``KEY=VALUE`` pairs from ``path`` into ``os.environ``.

    - Existing env vars take precedence (the file does not override them).
    - Lines starting with ``#``, blank lines, and lines without ``=`` are
      skipped without error.
    - Values may be wrapped in single or double quotes; the surrounding
      quotes are stripped.
    - Returns the number of variables actually written. Returns ``0`` if
      ``path`` does not exist (so callers can invoke this unconditionally
      at startup).
    """
    p = Path(path)
    if not p.exists():
        return 0

    written = 0
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
            written += 1
    return written
