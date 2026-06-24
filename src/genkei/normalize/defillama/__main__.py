"""``python -m genkei.normalize.defillama`` entry point.

The daily-ingest workflow (``.github/workflows/defillama-daily.yml``) invokes
the normalizer this way; the package keeps that command working after the
B-121 split from a single module into a package.
"""

from __future__ import annotations

from genkei.normalize.defillama.core import main

if __name__ == "__main__":
    raise SystemExit(main())
