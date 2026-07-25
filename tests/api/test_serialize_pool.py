"""Offline unit tests for the read-API serializer + pool config (B-131).

These need only the ``[api]`` extra (fastapi) — no Docker — so they run
locally whenever fastapi is installed and skip cleanly otherwise. They pin the
two pieces the endpoint contract depends on but that don't require a live DB:
the shared-``json_default`` JSON shape and the small pool ceiling.
"""

from __future__ import annotations

import json
import os
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

try:
    import fastapi  # noqa: F401

    _FASTAPI_OK = True
except ImportError:
    _FASTAPI_OK = False

_fastapi_required = unittest.skipUnless(
    _FASTAPI_OK, "fastapi ([api] extra) required for read-API serializer tests"
)


@_fastapi_required
class SerializerTests(unittest.TestCase):
    def test_decimal_renders_as_string_like_the_cli(self) -> None:
        from genkei.api.serialize import GenkeiJSONResponse

        raw = GenkeiJSONResponse(content={"price": Decimal("60000.5")}).body
        self.assertEqual(json.loads(raw), {"price": "60000.5"})

    def test_date_renders_iso(self) -> None:
        from genkei.api.serialize import GenkeiJSONResponse

        raw = GenkeiJSONResponse(content={"d": date(2026, 5, 12)}).body
        self.assertEqual(json.loads(raw), {"d": "2026-05-12"})


@_fastapi_required
class PoolCeilingTests(unittest.TestCase):
    def test_default_ceiling_is_small(self) -> None:
        from genkei.api import pool

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(pool._ENV_MAX_POOL_SIZE, None)
            self.assertEqual(pool.max_pool_size(), pool.DEFAULT_MAX_POOL_SIZE)
            self.assertLessEqual(pool.DEFAULT_MAX_POOL_SIZE, 4)

    def test_env_override_wins(self) -> None:
        from genkei.api import pool

        with patch.dict(os.environ, {pool._ENV_MAX_POOL_SIZE: "6"}):
            self.assertEqual(pool.max_pool_size(), 6)

    def test_bad_env_falls_back_to_default(self) -> None:
        from genkei.api import pool

        with patch.dict(os.environ, {pool._ENV_MAX_POOL_SIZE: "not-a-number"}):
            self.assertEqual(pool.max_pool_size(), pool.DEFAULT_MAX_POOL_SIZE)


if __name__ == "__main__":
    unittest.main()
